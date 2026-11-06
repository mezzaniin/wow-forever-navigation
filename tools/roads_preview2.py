"""Vergleichsbilder + Kennzahlen: ADT-Straßen (NavData v3) gegen die Kartengrafik je Zone.

Links: Zonenkarte, NavData-Straßenzellen (cyan), Mittellinie aus den Geländetexturen (magenta).
Rechts: Kartengrafik mit grafisch erkannten Straßen (gelb; ohne Berg-Schraffur-Zellen) – Referenz.
Kennzahlen (nur aufgedeckte Fläche, Bergzellen der Basis ausgenommen):
  Abdeckung  = Anteil grafischer Straßenpixel mit Straßenzelle <= 30 yd entfernt (Recall)
  Bestätigt  = Anteil der Straßen-Mittellinie mit grafischer Straße <= 30 yd entfernt (Precision;
               unterschätzt, wo die Grafik-Erkennung versagt: Schnee, dunkle Karten, Städte)

  python roads_preview2.py --navdata RoutePlaner/NavData.lua --base work/NavData.base.lua --fine work/roads_fine.npz \\
     --db2 DB2 --maps MAPS --tiles TILES --out screenshots [--zones 1429:elwynn,...] [--table work/coverage.json]
"""
import argparse, json, os, re, sys
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import roads as R  # noqa: E402

K = 8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--navdata', required=True); ap.add_argument('--base', required=True); ap.add_argument('--fine', required=True)
    ap.add_argument('--db2', required=True); ap.add_argument('--maps', required=True); ap.add_argument('--tiles', required=True)
    ap.add_argument('--out', required=True); ap.add_argument('--zones', default=''); ap.add_argument('--table')
    ap.add_argument('--artcache', default='work/artroads'); ap.add_argument('--prefix', default='6x-roads-')
    ap.add_argument('--taxi', help='taxi.json der Webkarte: Flugmeister je Zone, Abstand zum Straßennetz')
    a = ap.parse_args()
    navs = R.read_navdata(a.navdata); base = R.read_navdata(a.base)
    fz = np.load(a.fine)
    M = R.Maps(a.db2, a.maps, a.tiles)
    skel = {}
    for c in navs:
        if f'{c}_skel' in fz:
            shp = tuple(fz[f'{c}_shape']); skel[c] = np.unpackbits(fz[f'{c}_skel'])[:shp[0] * shp[1]].reshape(shp).astype(bool)
    specs = [s.partition(':') for s in a.zones.split(',') if s] or [(m, '', str(m)) for m in sorted(M.zones, key=int)]
    table = json.load(open(a.table)) if a.table and os.path.exists(a.table) else {}
    os.makedirs(a.out, exist_ok=True)
    taxi = json.load(open(a.taxi))['nodes'] if a.taxi else []
    dist = {c: ndi.distance_transform_edt(navs[c]['grid'] != R.STRASSE) * 20 for c in navs}
    lab = {c: ndi.label(navs[c]['grid'] == R.STRASSE, np.ones((3, 3)))[0] for c in navs}
    for mid, _, name in specs:
        z = M.zones[mid]; c = z['cont']
        if c not in navs or c not in skel: continue
        nav = navs[c]; g = nav['grid']; gb = base[c]['grid']; S = skel[c]
        rgb = M.image(mid); cov = M.coverage(mid)
        city = cov is None
        if city: cov = np.ones((668, 1002), bool)
        yy, xx = np.mgrid[0:668, 0:1002]
        wy = z['ymax'] - (xx + 0.5) / 1002 * (z['ymax'] - z['ymin']); wx = z['xmax'] - (yy + 0.5) / 668 * (z['xmax'] - z['xmin'])
        rr = np.floor((nav['XMAX'] - wx) / 20).astype(int); cc = np.floor((nav['YMAX'] - wy) / 20).astype(int)
        ok = (rr >= 0) & (rr < nav['h']) & (cc >= 0) & (cc < nav['w'])
        cls = np.zeros(rr.shape, np.uint8); cls[ok] = g[rr[ok], cc[ok]]
        bcls = np.zeros(rr.shape, np.uint8); bcls[ok] = gb[rr[ok], cc[ok]]
        road = cls == R.STRASSE
        # Mittellinie (Feinraster 2,5 yd) in Kartenpixel: jedes Skelettpixel auf sein Kartenpixel
        sy, sx = np.nonzero(S)
        swx = nav['XMAX'] - (sy + 0.5) * 2.5; swy = nav['YMAX'] - (sx + 0.5) * 2.5
        px = ((z['ymax'] - swy) / (z['ymax'] - z['ymin']) * 1002).astype(int); py = ((z['xmax'] - swx) / (z['xmax'] - z['xmin']) * 668).astype(int)
        inz = (px >= 0) & (px < 1002) & (py >= 0) & (py < 668)
        sk = np.zeros((668, 1002), bool); sk[py[inz], px[inz]] = True
        p = os.path.join(a.artcache, f'{mid}.npy')
        art = np.load(p) if os.path.exists(p) else R.detect(rgb, cov)
        ref = art & cov & (bcls != R.BERG)
        lab_, n = ndi.label(ref, np.ones((3, 3)))
        if n:
            # nur lange Linienstücke (Schraffur-/Schrift-Fehltreffer der Grafik-Erkennung sind kurz)
            ext = np.array([max(s_[0].stop - s_[0].start, s_[1].stop - s_[1].start) for s_ in ndi.find_objects(lab_)])
            ref = np.concatenate([[False], ext >= 15])[lab_]
        # Abstände in Yards
        pxyd = (z['ymax'] - z['ymin']) / 1002
        lim = max(1.0, 30 / pxyd)
        d_road = ndi.distance_transform_edt(~road)
        rec = float((d_road[ref] <= lim).mean()) if ref.any() else float('nan')
        d_ref = ndi.distance_transform_edt(~ref)
        skz = sk & cov & (bcls != R.BERG)
        prec = float((d_ref[skz] <= lim).mean()) if skz.any() else float('nan')
        left = rgb * 0.75; left[~cov] *= 0.45
        left[road] = left[road] * 0.35 + np.array([0, 230, 255]) * 0.65
        left[sk] = [255, 0, 200]
        right = rgb * 0.75; right[~cov] *= 0.45
        right[ref] = [255, 230, 0]
        both = np.concatenate([left, np.full((668, 6, 3), 255.0), right], axis=1)
        out = os.path.join(a.out, f'{a.prefix}{name}.png')
        Image.fromarray(np.clip(both, 0, 255).astype(np.uint8)).save(out)
        ncell = int(len(np.unique((rr * 4096 + cc)[road & cov & ok])))
        # Flugmeister der Zone: Abstand zur nächsten Straßenzelle
        fm = []
        for t in taxi:
            if t.get('mapId') != c or not t.get('world') or re.match(r'Generic', t.get('en', '')): continue
            tx = int((z['ymax'] - t['world'][1]) / (z['ymax'] - z['ymin']) * 1002); ty = int((z['xmax'] - t['world'][0]) / (z['xmax'] - z['xmin']) * 668)
            if 0 <= tx < 1002 and 0 <= ty < 668 and cov[ty, tx] and not city: fm.append(t)
        fmd = []
        for t in fm:
            r = int((nav['XMAX'] - t['world'][0]) / 20); cc2 = int((nav['YMAX'] - t['world'][1]) / 20)
            if 0 <= r < nav['h'] and 0 <= cc2 < nav['w']: fmd.append(float(dist[c][r, cc2]))
        fm_ok = sum(1 for d in fmd if d <= 80)
        # Netz: Anteil der Straßenzellen der Zone im größten zusammenhängenden Straßennetz (kontinentweit)
        zl = lab[c][rr[road & cov & ok], cc[road & cov & ok]]
        net = float(np.bincount(zl).max() / len(zl)) if len(zl) else float('nan')
        table[mid] = dict(name=z['name'], zellen=ncell, abdeckung=round(rec, 3), bestaetigt=round(prec, 3), stadt=city, bild=out,
                          flugmeister=len(fmd), flugmeister_an_strasse=fm_ok, netz=round(net, 3))
        print(f'{mid:>5} {z["name"][:24]:24} {ncell:5d} Zellen  Abdeckung {rec * 100:5.1f} %  bestätigt {prec * 100:5.1f} %  '
              f'FM {fm_ok}/{len(fmd)}  Netz {net * 100:4.0f} %  {out}', flush=True)
    if a.table: json.dump(table, open(a.table, 'w'), indent=1, ensure_ascii=False)


if __name__ == '__main__':
    main()
