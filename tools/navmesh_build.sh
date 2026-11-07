#!/usr/bin/env bash
# Navmesh (TrinityCore mmaps) aus der lokalen WoW-Forever-Installation bauen und rastern – rootless podman,
# kein root/sudo nötig. Läuft auf Raphas PC (Fedora); WoW-Installation wird nur gelesen (:ro).
#
#   tools/navmesh_build.sh "/pfad/zu/World of Warcraft" [arbeitsordner]
#
# Ergebnis: <arbeitsordner>/raster/navmesh_{0,1}.npz (-> work/navmesh/ im Addon-Repo kopieren) und die
# mmaps in <arbeitsordner>/mmaps (0000*.mmtile, 0001*.mmtile; ~0,9 GB).
# Danach (auf hermes): tools/navmesh_fuse.py terrain|roads|npcroads, siehe dort.
#
# Ablauf / Fallstricke:
#  * TrinityCore master (Retail-Extraktoren) kann CASC-Produkt wowf öffnen (-p wowf), liest aber Map/Liquid*.db2
#    mit fest einkompilierten Retail-Layouts (LayoutHash-Prüfung) -> tools/navmesh_patch_tc.py ersetzt diese
#    Tabellen durch einkompilierte wago.tools-CSV (product=wowf), nur Karten 0/1, GameObject-Modelle übersprungen.
#  * CascLib öffnet lokale Dateien O_RDWR -> scheitert an schreibgeschütztem Mount (EROFS, Fehler 2) -> Patch O_RDONLY.
#  * SELinux: WoW-Ordner mit --security-opt label=disable einhängen (nicht :Z – würde Labels der Installation ändern).
#  * vmap4extractor: Locale-Option heißt -dl (nicht -l = "large"); Threads begrenzen (--threads).
#  * mmaps_generator braucht einen (leeren) Ordner dbc/<locale>/.
#  * nice/ionice wirken nur innerhalb des Containers (podman-Client ist nicht der Elternprozess).
set -euo pipefail
WOW="${1:?Pfad zum World-of-Warcraft-Ordner}"
WD="${2:-$HOME/wow/navmesh-build}"
JOBS=${JOBS:-16}; THREADS=${THREADS:-10}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$WD"; cd "$WD"

# 1) Quellen + Patch
[ -d TrinityCore ] || { git clone -q --depth 1 --filter=blob:none --sparse https://github.com/TrinityCore/TrinityCore.git
                        (cd TrinityCore && git sparse-checkout set src dep cmake contrib); }
mkdir -p csv
for t in Map LiquidMaterial LiquidObject LiquidType; do
  [ -s csv/$t.csv ] || curl -sfL -o csv/$t.csv "https://wago.tools/db2/$t/csv?product=wowf"
done
python3 "$ROOT/tools/navmesh_patch_tc.py" TrinityCore csv

# 2) Bau-Container (Ubuntu 24.04 + Boost/MySQL-Header) und Werkzeuge
cat > Containerfile <<'EOF'
FROM docker.io/library/ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends git cmake make g++ libssl-dev libmysqlclient-dev libreadline-dev zlib1g-dev libbz2-dev libboost-dev libboost-system-dev libboost-filesystem-dev libboost-program-options-dev libboost-iostreams-dev libboost-regex-dev libboost-locale-dev libboost-thread-dev ca-certificates && rm -rf /var/lib/apt/lists/*
EOF
podman image exists tcbuild || podman build -q -t tcbuild -f Containerfile .
printf 'FROM docker.io/library/python:3.12-slim\nRUN pip install --no-cache-dir numpy scipy\n' > Containerfile.py
podman image exists navpy || podman build -q -t navpy -f Containerfile.py .
cp "$ROOT/tools/navmesh_dump.cpp" "$ROOT/tools/navmesh_raster.py" .
mkdir -p build
podman run --rm --userns=keep-id -v "$PWD":/src:Z tcbuild bash -c "cd /src/build && \
  nice -n 19 cmake ../TrinityCore -DSERVERS=0 -DTOOLS=1 -DWITHOUT_GIT=1 -DCMAKE_BUILD_TYPE=Release -DUSE_COREPCH=0 -DUSE_SCRIPTPCH=0 -DCMAKE_INSTALL_PREFIX=/src/install >/dev/null && \
  nice -n 19 ionice -c3 make -j$JOBS mapextractor vmap4extractor vmap4assembler mmaps_generator >/dev/null && make install >/dev/null && \
  cd /src && g++ -O2 -std=c++17 navmesh_dump.cpp -ITrinityCore/dep/recastnavigation/Detour/Include build/dep/recastnavigation/Detour/libDetour.a -o navmesh_dump"

# 3) Extrahieren (maps, vmaps), mmaps erzeugen, Navmesh auslesen
mkdir -p data/dbc/deDE
podman run --rm --userns=keep-id --security-opt label=disable -v "$PWD":/src -v "$WOW":/wow:ro tcbuild bash -c "
  set -e; export LD_LIBRARY_PATH=/src/install/lib; B=/src/install/bin; cd /src/data
  [ -d maps ] || nice -n 19 ionice -c3 \$B/mapextractor -i /wow -o /src/data -e 1 -p wowf -l deDE > /src/log_map.txt
  [ -d vmaps ] || { nice -n 19 ionice -c3 \$B/vmap4extractor -d /wow -p wowf -dl deDE --threads $THREADS > /src/log_vmapx.txt
                    mkdir -p vmaps; nice -n 19 ionice -c3 \$B/vmap4assembler Buildings vmaps > /src/log_vmapa.txt; }
  for m in 0 1; do ls mmaps/000\${m}.mmap >/dev/null 2>&1 || nice -n 19 ionice -c3 \$B/mmaps_generator \$m --threads $THREADS > /src/log_mmaps_\$m.txt; done
  for m in 0 1; do ../navmesh_dump mmaps \$m ../nav\$m.bin; done"
podman run --rm --userns=keep-id -v "$PWD":/src:Z navpy nice -n 19 python /src/navmesh_raster.py --bin /src --out /src/raster

# 4) Aufräumen: nur mmaps + Raster behalten
mv data/mmaps ./mmaps 2>/dev/null || true
rm -rf data/maps data/vmaps data/Buildings build nav0.bin nav1.bin
du -sh mmaps raster
