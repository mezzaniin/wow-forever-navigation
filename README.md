# WoW Forever Navigation – RoutePlaner

Navigations-Addon für **World of Warcraft Forever** (Classic 1–60 auf dem modernen Client, Interface 16001).
Es plant Routen von deinem Standort zu einem Ziel – zu Fuß um Berge und Wasser herum, mit Booten,
Zeppelinen, Portalen, Tiefenbahn, Flugrouten und Teleports – und zeigt sie auf Weltkarte, Minimap,
mit TomTom-Pfeil und in einem Schritt-Fenster an.

## Funktionen

- **Ziel setzen:** Strg+Klick auf die Weltkarte, `/route 45,2 60,1`, `/route Darnassus 40 50`
  oder über das **Suchfeld** oben links auf der Weltkarte.
- **Suche:** Gebiete, Städte, Flugpunkte, Stege/Portale, **Dungeon-Eingänge**, **Quests**
  (Annahme, Abgabe, Questziele für Quests im Log), **NPCs** (Name oder Titel, z. B. „Angellehrer“)
  und **Objekte** – über die Datenbank von [Questie](https://github.com/Questie/Questie).
  Klick auf einen Treffer = Route dorthin.
- **Wegführung um Gelände:** Navigationsraster (20 Yards) aus den Weltkarten von Forever:
  begehbar / Berg (teuer) / Wasser (Schwimmen) / gesperrt. A*-Suche im Spiel, über Frames verteilt.
  Laufen nur innerhalb zusammenhängender Bereiche – keine Luftlinien über Gebirge.
- **Nur verfügbare Verkehrsmittel:** eigene Fraktion, nur entdeckte Flugpunkte (pro Charakter),
  Teleports nur wenn der Zauber bekannt ist.
- **Gelernte Wege:** selbst gelaufene Wege werden gespeichert und bevorzugt.
- **Anzeige:** Linien auf Weltkarte und Minimap, Schritt-Fenster (Details-Stil),
  **Stufenbereiche aller Zonen** auf der Weltkarte (`/route stufen`).

## Installation

```bash
./install.sh "/pfad/zu/World of Warcraft"
```
Danach WoW neu starten und **RoutePlaner** in der Addon-Liste aktivieren.
Empfohlen: [TomTom](https://www.curseforge.com/wow/addons/tomtom) (Pfeil) und Questie (Suche).

Einmalig bei einem Flugmeister die Flugkarte öffnen, damit das Addon deine Flugpunkte kennt.

## Befehle

`/route hilfe` zeigt alle Befehle, u. a.:
`/route stop`, `/route fenster`, `/route suche <text>`, `/route tempo <yd/s>`,
`/route punkte`, `/route punkt <key>:a|b` (Steg/Portal-Position korrigieren),
`/route wege [an|aus|löschen]`, `/route stufen`.

## Navigationsdaten neu erzeugen

`RoutePlaner/NavData.lua` wird aus den Kartendateien deiner lokalen Installation erzeugt
(z. B. nach einem Forever-Patch):

```bash
tools/build.sh "/pfad/zu/World of Warcraft"
```

Ablauf: [CascLib](https://github.com/ladislav-zezula/CascLib) liest die Kartenkacheln aus den
CASC-Archiven (Produkt `wowf`), die Kartentabellen kommen von [wago.tools](https://wago.tools),
`render.py` setzt die vollständig aufgedeckten Karten zusammen, `masks.py`/`mountains.py`
erkennen begehbare Flächen, Wasser und gezeichnete Berge, `build_nav.py` baut daraus das
Raster je Kontinent, `bridge_export.py` verbindet bekannte Pässe/Brücken (Dun Algaz,
Thandolübergang, Inseln) und exportiert das Raster lauflängenkodiert nach Lua.

Ausgelesene Blizzard-Dateien (Kacheln, Karten) werden **nicht** ins Repo eingecheckt.

## Grenzen

- Höhlen, Gebäude und nur dünn gezeichnete Hügel kennt das Raster nicht.
- Straßen werden noch nicht bevorzugt (Erkennung bisher zu unzuverlässig).
- Koordinaten einiger Stege/Portale sind geschätzt – mit `/route punkt` korrigierbar.
- Stufenbereiche: Classic-Standardwerte, sofern der Client keine eigenen liefert.
