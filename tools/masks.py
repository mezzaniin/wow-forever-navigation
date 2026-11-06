import csv, os, numpy as np
from PIL import Image
def rows(f): return list(csv.DictReader(open(f, encoding='utf-8')))
uimap = {r['ID']: r for r in rows('UiMap.csv')}
xart = {r['UiMapID']: r['UiMapArtID'] for r in rows('UiMapXMapArt.csv') if r['PhaseID']=='0'}
ovl = {}
for r in rows('WorldMapOverlay.csv'): ovl.setdefault(r['UiMapArtID'], []).append(r)
otiles = {}
for r in rows('WorldMapOverlayTile.csv'): otiles.setdefault(r['WorldMapOverlayID'], []).append(r)
def zone_mask(mid):
    """Begehbar-Maske 1002x668 (bool) + Wasser-Maske"""
    art = xart[mid]
    rgb = np.asarray(Image.open(f'maps/{mid}.png').convert('RGB')).astype(int)
    if not ovl.get(art):   # Städte: ganze Karte
        cov = np.ones((668,1002), bool)
    else:
        a = np.zeros((1400,1400), np.uint8)
        for o in ovl[art]:
            ox, oy = int(o['OffsetX']), int(o['OffsetY'])
            for t in otiles.get(o['ID'], []):
                p=f"out/tiles/{t['FileDataID']}.blp"
                if os.path.exists(p):
                    al = np.asarray(Image.open(p).convert('RGBA').getchannel('A'))
                    x0, y0 = ox+int(t['ColIndex'])*256, oy+int(t['RowIndex'])*256
                    h, w = al.shape
                    a[y0:y0+h, x0:x0+w] = np.maximum(a[y0:y0+h, x0:x0+w], al)
        cov = a[:668,:1002] > 128
    r,g,b = rgb[...,0], rgb[...,1], rgb[...,2]
    water = (b > 0.72*r) & (abs(r-g) < 30) & (b > 70)
    return cov, water
if __name__ == '__main__':
    import sys
    for mid in sys.argv[1:]:
        cov, water = zone_mask(mid)
        im = np.asarray(Image.open(f'maps/{mid}.png').convert('RGB')).copy()
        im[~cov] = (im[~cov]*0.3).astype(np.uint8)
        im[cov & water] = [40,80,255]
        Image.fromarray(im).save(f'mask_{mid}.png'); print(mid, cov.mean(), (cov&water).mean())
