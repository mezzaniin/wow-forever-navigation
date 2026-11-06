import csv, numpy as np
from masks import zone_mask, rows, uimap
from mountains import features
CELL = 20.0
assign = {}
for r in rows('UiMapAssignment.csv'):
    if r['OrderIndex'] == '0': assign[r['UiMapID']] = r
zones = {}
for mid, m in uimap.items():
    a = assign.get(mid)
    if m['Type'] != '3' or not a: continue
    if a['MapID'] not in ('0', '1'): continue
    zones[mid] = dict(cont=int(a['MapID']), xmin=float(a['Region_0']), ymin=float(a['Region_1']),
                      xmax=float(a['Region_3']), ymax=float(a['Region_4']), name=m['Name_lang'])
out = {}
for cont in (0, 1):
    zs = {k: v for k, v in zones.items() if v['cont'] == cont}
    XMAX = max(z['xmax'] for z in zs.values()); XMIN = min(z['xmin'] for z in zs.values())
    YMAX = max(z['ymax'] for z in zs.values()); YMIN = min(z['ymin'] for z in zs.values())
    H = int(np.ceil((XMAX - XMIN) / CELL)); W = int(np.ceil((YMAX - YMIN) / CELL))
    grid = np.zeros((H, W), np.uint8)   # 0 gesperrt, 1 frei, 2 Berg, 3 Wasser
    for mid, z in zs.items():
        cov, water = zone_mask(mid)
        mount, _ = features(mid)
        cls = np.zeros(cov.shape, np.uint8)
        cls[cov] = 1
        cls[cov & mount] = 2
        cls[cov & water] = 3
        r0 = int((XMAX - z['xmax']) / CELL); r1 = int(np.ceil((XMAX - z['xmin']) / CELL))
        c0 = int((YMAX - z['ymax']) / CELL); c1 = int(np.ceil((YMAX - z['ymin']) / CELL))
        rr, cc = np.mgrid[r0:r1, c0:c1]
        wx = XMAX - (rr + 0.5) * CELL; wy = YMAX - (cc + 0.5) * CELL
        v = (z['xmax'] - wx) / (z['xmax'] - z['xmin']); u = (z['ymax'] - wy) / (z['ymax'] - z['ymin'])
        ok = (u >= 0) & (u < 1) & (v >= 0) & (v < 1)
        px = np.clip((u * 1002).astype(int), 0, 1001); py = np.clip((v * 668).astype(int), 0, 667)
        val = np.where(ok, cls[py, px], 0)
        cur = grid[r0:r1, c0:c1]
        # Priorität: frei(1) < Wasser(3) < Berg(2) < gesperrt(0)  -> bestes (billigstes) gewinnt
        rank = np.array([9, 0, 2, 1])
        better = (val > 0) & ((cur == 0) | (rank[val] < rank[cur]))
        cur[better] = val[better]
    out[cont] = dict(XMAX=XMAX, YMAX=YMAX, W=W, H=H, grid=grid, zones=zs)
    print(cont, W, H, 'frei', (grid==1).sum(), 'berg', (grid==2).sum(), 'wasser', (grid==3).sum())
np.save('nav.npy', {c: dict(XMAX=o['XMAX'], YMAX=o['YMAX'], W=o['W'], H=o['H'], grid=o['grid']) for c, o in out.items()}, allow_pickle=True)
# Vorschau
from PIL import Image
for c, o in out.items():
    pal = np.array([[20,20,20],[90,170,60],[200,60,40],[40,90,230]], np.uint8)
    Image.fromarray(pal[o['grid']]).save(f'nav_{c}.png')
