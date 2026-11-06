#!/usr/bin/env bash
# Kopiert das Addon RoutePlaner in den AddOns-Ordner von WoW Forever.
# Benutzung: ./install.sh ["/pfad/zu/World of Warcraft"]
# Ohne Pfad wird gesucht: Steam/Proton (auch Flatpak-Steam), Lutris/~/Games, Wine-Präfix.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

find_wow() {
    local roots=(
        "$HOME/.local/share/Steam/steamapps/compatdata"
        "$HOME/.steam/steam/steamapps/compatdata"
        "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/compatdata"
        "$HOME/Games" "$HOME/.wine" "$HOME/.local/share/lutris"
    )
    local r
    for r in "${roots[@]}"; do
        [ -d "$r" ] || continue
        # …/World of Warcraft/_forever_  (Elternordner kann anders heißen)
        find "$r" -maxdepth 9 -type d -name '_forever_' 2>/dev/null
    done | sed 's#/_forever_$##' | sort -u
}

WOW="${1:-}"
if [ -z "$WOW" ]; then
    mapfile -t CANDS < <(find_wow)
    if [ "${#CANDS[@]}" -eq 1 ]; then
        WOW="${CANDS[0]}"
    elif [ "${#CANDS[@]}" -gt 1 ]; then
        echo "Mehrere WoW-Forever-Ordner gefunden:"
        for i in "${!CANDS[@]}"; do echo "  [$i] ${CANDS[$i]}"; done
        read -r -p "Nummer wählen: " n
        WOW="${CANDS[$n]}"
    else
        echo "Kein WoW-Forever-Ordner (mit Unterordner _forever_) gefunden."
        read -r -p "Pfad zum Ordner 'World of Warcraft' eingeben: " WOW
    fi
fi
[ -d "$WOW" ] || { echo "Ordner nicht gefunden: $WOW" >&2; exit 1; }
DEST="$WOW/_forever_/Interface/AddOns/RoutePlaner"
mkdir -p "$DEST"
cp -r "$HERE/RoutePlaner/." "$DEST/"
echo "Installiert nach: $DEST  (WoW neu starten bzw. /reload)"
