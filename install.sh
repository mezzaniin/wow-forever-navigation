#!/usr/bin/env bash
# Kopiert das Addon in den AddOns-Ordner von WoW Forever.
# Benutzung: ./install.sh "/pfad/zu/World of Warcraft"
set -euo pipefail
WOW="${1:?Pfad zum World-of-Warcraft-Ordner angeben}"
DEST="$WOW/_forever_/Interface/AddOns/RoutePlaner"
mkdir -p "$DEST"
cp -r "$(dirname "$0")/RoutePlaner/." "$DEST/"
echo "Installiert nach: $DEST  (WoW neu starten bzw. /reload)"
