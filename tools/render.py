import csv, os, sys
from PIL import Image
def rows(f): return list(csv.DictReader(open(f, encoding='utf-8')))
uimap = {r['ID']: r for r in rows('UiMap.csv')}
xart = {r['UiMapID']: r['UiMapArtID'] for r in rows('UiMapXMapArt.csv') if r['PhaseID']=='0'}
arttiles = {}
for r in rows('UiMapArtTile.csv'):
    if r['LayerIndex']=='0': arttiles.setdefault(r['UiMapArtID'], []).append(r)
ovl = {}
for r in rows('WorldMapOverlay.csv'): ovl.setdefault(r['UiMapArtID'], []).append(r)
otiles = {}
for r in rows('WorldMapOverlayTile.csv'): otiles.setdefault(r['WorldMapOverlayID'], []).append(r)
def tile(fid):
    p = f'out/tiles/{fid}.blp'
    return Image.open(p).convert('RGBA') if os.path.exists(p) else None
os.makedirs('maps', exist_ok=True)
for mid, art in xart.items():
    m = uimap.get(mid)
    if not m or m['Type'] != '3': continue   # nur Zonen
    canvas = Image.new('RGBA', (1024, 768), (0,0,0,255))
    for t in arttiles.get(art, []):
        im = tile(t['FileDataID'])
        if im: canvas.alpha_composite(im, (int(t['ColIndex'])*256, int(t['RowIndex'])*256))
    for o in ovl.get(art, []):
        ox, oy = int(o['OffsetX']), int(o['OffsetY'])
        for t in otiles.get(o['ID'], []):
            im = tile(t['FileDataID'])
            if im:
                canvas.alpha_composite(im, (ox + int(t['ColIndex'])*256, oy + int(t['RowIndex'])*256))
    canvas.crop((0,0,1002,668)).convert('RGB').save(f"maps/{mid}.png")
    print(mid, m['Name_lang'], len(ovl.get(art, [])))
