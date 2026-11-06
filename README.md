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
./install.sh                              # sucht WoW Forever unter Steam/Proton, Lutris, Wine
./install.sh "/pfad/zu/World of Warcraft" # oder Pfad angeben
```
Danach WoW neu starten und **RoutePlaner** in der Addon-Liste aktivieren.
Empfohlen: [TomTom](https://www.curseforge.com/wow/addons/tomtom) (Pfeil) und Questie (Suche).

Einmalig bei einem Flugmeister die Flugkarte öffnen, damit das Addon deine Flugpunkte kennt.

## Befehle

`/route hilfe` zeigt alle Befehle, u. a.:
`/route stop`, `/route fenster`, `/route suche <text>`, `/route tempo <yd/s>`,
`/route punkte`, `/route punkt <key>:a|b` (Steg/Portal-Position korrigieren),
`/route wege [an|aus|löschen]`, `/route stufen`, `/route strassen [an|aus]` (Straßen/Wege/Brücken
bevorzugen, Standard an; die Webkarte übernimmt die Einstellung über die Pixel-Leiste).

## Webkarte (Forever-Karte) synchron

`Bridge.lua` zeichnet nach `/route web an` oben links eine Leiste farbiger Pixel (56 Zellen à 4 px, per
`/route web größe <px>` änderbar) mit Position, Blickrichtung, Ziel, Routen-Hash, Schritt, bekannten
Flugpunkten, Fraktion/Tempo/Teleports sowie Klasse, Rasse, Stufe und Talentpunkten. Das Begleitprogramm
`forever-karte` (Repo wowmap, `companion/`) liest sie per Bildschirmaufnahme und schickt sie an die Webkarte;
diese plant mit denselben Daten (NavData.lua, Data.lua) und demselben Algorithmus und zeigt dieselbe Route.
Kein Netzwerk im Addon, keine geschützten Funktionen. `/route web status` zeigt Leiste, Position und Hash.

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

**Straßen** (Klasse `e`, NavData-Format v2) stammen aus den echten Geländedaten: `adt_fetch.py` lädt die
ADT-Kacheln (`_tex0` = Texturebenen + Alphamaps, `_obj0` = Objektplatzierungen) über die CASC-API von
wago.tools (FileDataIDs aus dem MAID-Chunk der WDT), `adt_roads.py` berechnet je Alphamap-Pixel den
sichtbaren Anteil der Straßentexturen aus `data-src/road_textures.json` (kuratiert: `*road*`,
`*cobblestone*`, Pflaster-/Stadtböden; Liste und Schwellen dort), mittelt auf 5 yd, und eine 20-yd-Zelle
wird Straße, wenn ein durchgehendes Band hindurchläuft. Brücken: WMO/M2-Platzierungen mit `bridge` im
Namen (Name aus dem Community-Listfile) → gedrehtes Rechteck der Modellbox (Fahrbahnbreite begrenzt) →
Straßenzellen, auch über Wasser/Schlucht. `roads_preview.py` erzeugt Vergleichsbilder gegen die
Kartengrafik (`roads.py` = grafische Linienerkennung, nur noch zur Kontrolle).

Kosten in `Nav.lua`: frei 1, Straße 0,6, Wasser 4, Berg 6 (Heuristik × 0,6, damit A* exakt bleibt;
Glätten kürzt nur ab, wenn die Gerade nicht teurer ist als der gefundene Weg).

Ausgelesene Blizzard-Dateien (Kacheln, Karten) werden **nicht** ins Repo eingecheckt.

## Grenzen

- Höhlen, Gebäude und nur dünn gezeichnete Hügel kennt das Raster nicht.
- Straßen nur dort, wo das Gelände eine eigene Straßentextur hat: in Dun Morogh (Schneepfade ohne
  eigene Textur) und teils Westfall/Loch Modan fehlen Wege; Durotar hat einzelne Straßentextur-Flecken.
- Koordinaten einiger Stege/Portale sind geschätzt – mit `/route punkt` korrigierbar.
- Stufenbereiche: Classic-Standardwerte, sofern der Client keine eigenen liefert.
