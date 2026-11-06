"""Vergleichsbilder: Straßen aus den ADT-Geländedaten (eingebacken in NavData) gegen die Kartengrafik.

Links: Zonenkarte mit ADT-Straßen (Feinraster 5 yd = magenta, NavData-Straßenzellen = cyan).
Rechts: dieselbe Karte mit der rein grafischen Linienerkennung (roads.detect, gelb) – nur zur Kontrolle.
Kennzahlen je Zone: Anteil der grafisch erkannten Straßenpixel mit ADT-Straßenzelle in <= 1 Zelle
(Abdeckung) und Anteil der ADT-Straßenzellen mit grafischer Erkennung in <= 2 Zellen (Bestätigung).

  python roads_preview.py --navdata RoutePlaner/NavData.lua --fine work/roads_fine.npz --maps maps --db2 . \
      --tiles out/tiles --out screenshots --zones 1429:elwynn,1436:westfall
"""
import argparse, os, sys
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import roads as R  # noqa: E402


def zone_cells(z, nav, H=668, W=1002):
    yy, xx = np.mgrid[0:H, 0:W]
    wy = z['ymax'] - (xx + 0.5) / W * (z['ymax'] - z['ymin']); wx = z['xmax'] - (yy + 0.5) / H * (z['xmax'] - z['xmin'])
    return wx, wy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--navdata', required=True); ap.add_argument('--fine', required=True)
    ap.add_argument('--maps', default='maps'); ap.add_argument('--db2', default='.'); ap.add_argument('--tiles', default='out/tiles')
    ap.add_argument('--out', required=True); ap.add_argument('--zones', required=True)
    a = ap.parse_args()
    navs = R.read_navdata(a.navdata)
    fine = np.load(a.fine)
    M = R.Maps(a.db2, a.maps, a.tiles)
    os.makedirs(a.out, exist_ok=True)
    for spec in a.zones.split(','):
        mid, _, name = spec.partition(':')
        z = M.zones[mid]; nav = navs[z['cont']]; F = fine[str(z['cont'])]
        rgb = M.image(mid); cov = M.coverage(mid)
        wx, wy = zone_cells(z, nav)
        rr = ((nav['XMAX'] - wx) / 20).astype(int); cc = ((nav['YMAX'] - wy) / 20).astype(int)
        fr = ((nav['XMAX'] - wx) / 5).astype(int); fc = ((nav['YMAX'] - wy) / 5).astype(int)
        ok = (rr >= 0) & (rr < nav['h']) & (cc >= 0) & (cc < nav['w'])
        cls = np.zeros(rr.shape, np.uint8); cls[ok] = nav['grid'][rr[ok], cc[ok]]
        fp = np.zeros(rr.shape, bool); fp[ok] = F[fr[ok], fc[ok]]
        road = cls == R.STRASSE
        if cov is None: cov = np.ones(road.shape, bool)
        left = rgb * 0.75; left[~cov] *= 0.45
        left[road] = left[road] * 0.35 + np.array([0, 230, 255]) * 0.65
        left[fp] = left[fp] * 0.3 + np.array([255, 0, 200]) * 0.7
        img = R.detect(rgb, cov)
        right = rgb * 0.75; right[~cov] *= 0.45
        right[img] = [255, 230, 0]
        # Kennzahlen in Kartenpixeln (Zellabstände über Distanztransformation im Raster)
        G = nav['grid'] == R.STRASSE
        d = ndi.distance_transform_edt(~G)
        dist_road = np.full(rr.shape, 99.0); dist_road[ok] = d[rr[ok], cc[ok]]
        rec = (dist_road[img & cov] <= 1.5).mean() if (img & cov).any() else float('nan')
        imgcells = np.zeros(G.shape, bool); imgcells[rr[img & ok], cc[img & ok]] = True
        di = ndi.distance_transform_edt(~imgcells)
        zr = np.zeros(G.shape, bool); zr[rr[ok & cov], cc[ok & cov]] = True
        zc = G & zr
        prec = (di[zc] <= 2.5).mean() if zc.any() else float('nan')
        both = np.concatenate([left, np.full((668, 6, 3), 255.0), right], axis=1)
        p = os.path.join(a.out, f'5x-roads-{name or mid}.png')
        Image.fromarray(np.clip(both, 0, 255).astype(np.uint8)).save(p)
        print(f'{mid} {z["name"]}: {int(zc.sum())} ADT-Straßenzellen; grafische Straßen abgedeckt {rec * 100:.0f} %, '
              f'ADT-Zellen grafisch bestätigt {prec * 100:.0f} %  -> {p}', flush=True)


if __name__ == '__main__':
    main()
