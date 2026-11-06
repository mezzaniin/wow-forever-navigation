#!/usr/bin/env bash
# Erzeugt RoutePlaner/NavData.lua aus den Weltkarten deiner lokalen WoW-Forever-Installation.
# Benutzung: tools/build.sh "/pfad/zu/World of Warcraft"
set -euo pipefail
WOW="${1:?Pfad zum World-of-Warcraft-Ordner angeben}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$ROOT/work"; mkdir -p "$WORK"; cd "$WORK"

# 1) CascLib bauen (liest die CASC-Archive von WoW)
if [ ! -x extract ]; then
  [ -d CascLib ] || git clone --depth 1 https://github.com/ladislav-zezula/CascLib.git
  mkdir -p obj
  for f in $(find CascLib/src -name '*.cpp' -o -name '*.c' | grep -v -E 'DllMain|zlib/(example|minigzip)|overwatch'); do
    o="obj/$(echo "$f" | tr '/' '_').o"
    case "$f" in *.c) gcc -O2 -w -fPIC -c "$f" -o "$o" -ICascLib/src -ICascLib/src/zlib ;;
                 *)   g++ -O2 -w -fPIC -c "$f" -o "$o" -ICascLib/src -ICascLib/src/zlib ;; esac
  done
  g++ -O2 -w "$ROOT/tools/extract.cpp" -I. obj/*.o -o extract -lpthread
fi

# 2) Python-Umgebung
[ -d venv ] || { python3 -m venv venv; venv/bin/pip -q install pillow numpy scipy; }

# 3) Kartentabellen (Forever = product wowf) von wago.tools
for t in WorldMapOverlay WorldMapOverlayTile UiMapXMapArt UiMapArtTile UiMap UiMapAssignment; do
  curl -sfL -o "$t.csv" "https://wago.tools/db2/$t/csv?product=wowf"
done

# 4) Kartenkacheln auslesen (FileDataIDs direkt aus den Tabellen)
( awk -F, 'NR>1{print $5";tiles/"$5".blp"}' UiMapArtTile.csv
  awk -F, 'NR>1{print $5";tiles/"$5".blp"}' WorldMapOverlayTile.csv ) | sort -u | ./extract "$WOW" wowf out

# 5) Karten zusammensetzen, Gelände erkennen, Raster bauen, Pässe verbinden, exportieren
cp "$ROOT"/tools/*.py .
venv/bin/python render.py > /dev/null
venv/bin/python build_nav.py
venv/bin/python bridge_export.py "$WORK"          # Basis-Raster (ohne Straßen) -> work/NavData.lua

# 6) Straßen aus den ADT-Geländetexturen + Bauwerke (wago.tools CASC, product wowf) einbacken
#    Texturliste: data-src/road_textures.json, Bauwerke: data-src/structures.json; ADT-Cache: work/adt (~1,3 GB, einmalig)
[ -s listfile.csv ] || curl -sfL -o listfile.csv https://github.com/wowdev/wow-listfile/releases/latest/download/community-listfile.csv
venv/bin/pip -q install scikit-image
venv/bin/python adt_fetch.py --out adt
venv/bin/python adt_texstore.py --adt adt                        # Texturgewichte je 2 yd -> adt/texstore_<map>.npz
venv/bin/python adt_structs.py --navdata NavData.lua --adt adt --listfile listfile.csv \
  --config "$ROOT/data-src/structures.json" --out structs.json    # Brücken/Tunnel/Tore/Stege aus Modellgeometrie
venv/bin/python adt_roads.py --navdata NavData.lua --out "$ROOT/RoutePlaner/NavData.lua" --adt adt \
  --db2 . --maps maps --tiles out/tiles --textures "$ROOT/data-src/road_textures.json" --structs structs.json --fine-out roads_fine.npz
echo "Fertig: $ROOT/RoutePlaner/NavData.lua"
