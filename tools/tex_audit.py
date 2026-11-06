"""Textur-Audit je Zone: welche Geländetexturen bilden Straßen/Wege?

Für jede Zone (UiMap Typ 3 auf Karte 0/1) auf einem 4,17-yd-Weltraster innerhalb der aufgedeckten Kartenfläche:
  flaeche   Pixel mit Texturgewicht >= 0,5 (yd²/1000)
  duenn     Anteil dieser Pixel in schmalen Bändern (überstehen keine Öffnung mit Radius ~9 yd)
  art       Anreicherung: Anteil nahe an grafisch erkannten Kartenstraßen (roads.detect) / Grundrate
Ergebnis work/tex_audit.json; dient nur der Auswahl der Straßentexturen (data-src/road_textures.json).

  python tex_audit.py --adt work/adt --db2 DB2 --maps MAPS --tiles TILES [--zones 1426,1432]
"""
import argparse, json, os, sys
import numpy as np
from scipy import ndimage as ndi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import roads as R  # noqa: E402
from texlib import TexStore  # noqa: E402

RES = 4.1666667


def zone_grid(z, res=RES):
    W = int((z['ymax'] - z['ymin']) / res); H = int((z['xmax'] - z['xmin']) / res)
    yy, xx = np.mgrid[0:H, 0:W]
    wx = z['xmax'] - (yy + 0.5) * res; wy = z['ymax'] - (xx + 0.5) * res
    # Kartenpixel (1002x668)
    px = np.clip(((z['ymax'] - wy) / (z['ymax'] - z['ymin']) * 1002).astype(int), 0, 1001)
    py = np.clip(((z['xmax'] - wx) / (z['xmax'] - z['xmin']) * 668).astype(int), 0, 667)
    return wx, wy, px, py


def art_roads(M, mid, cache):
    p = os.path.join(cache, f'{mid}.npy')
    if os.path.exists(p): return np.load(p)
    rgb = M.image(mid); cov = M.coverage(mid)
    if rgb is None: return None
    m = R.detect(rgb, cov if cov is not None else np.ones(rgb.shape[:2], bool))
    os.makedirs(cache, exist_ok=True); np.save(p, m)
    return m


def disk(r):
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    return x * x + y * y <= r * r + 0.5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--adt', default='work/adt'); ap.add_argument('--db2', required=True)
    ap.add_argument('--maps', required=True); ap.add_argument('--tiles', required=True)
    ap.add_argument('--zones', default=''); ap.add_argument('--out', default='work/tex_audit.json')
    ap.add_argument('--names', default='work/tex_inventory.json')
    a = ap.parse_args()
    names = {int(k.split(':')[1]): v[0].replace('tileset/', '') for k, v in json.load(open(a.names)).items()}
    M = R.Maps(a.db2, a.maps, a.tiles)
    stores = {}
    res = json.load(open(a.out)) if os.path.exists(a.out) else {}
    only = set(filter(None, a.zones.split(',')))
    for mid, z in sorted(M.zones.items(), key=lambda kv: int(kv[0])):
        if only and mid not in only: continue
        cov = M.coverage(mid)
        if cov is None: cov = np.ones((668, 1002), bool)
        art = art_roads(M, mid, os.path.join(os.path.dirname(a.out), 'artroads'))
        if art is None: continue
        st = stores.setdefault(z['cont'], TexStore(a.adt, z['cont']))
        wx, wy, px, py = zone_grid(z)
        C = cov[py, px]
        if C.sum() < 100: continue
        artd = ndi.binary_dilation(art, iterations=1)[py, px]
        base = artd[C].mean()
        pres = st.present(wx[C], wy[C])
        out = {}
        for fd, nch in sorted(pres.items(), key=lambda kv: -kv[1]):
            if nch < 3: continue
            Wt = st.sample([fd], wx, wy)
            m = (Wt >= 0.5) & C
            n = int(m.sum())
            if n < 20: continue
            opened = ndi.binary_opening(m, structure=disk(2))
            thin = 1 - opened[m].mean()
            enr = artd[m].mean() / max(base, 1e-6)
            out[str(fd)] = dict(name=names.get(fd, '?'), chunks=nch, flaeche=round(n * RES * RES / 1000, 1),
                                duenn=round(float(thin), 3), art=round(float(enr), 2))
        res[mid] = dict(name=z['name'], cont=z['cont'], base=round(float(base), 4), tex=out)
        top = sorted(out.items(), key=lambda kv: -kv[1]['art'] * min(1, kv[1]['flaeche'] / 50))[:8]
        print(f"{mid} {z['name']}: " + '; '.join(f"{v['name'].split('/')[-1][:-6]} a{v['art']} d{v['duenn']} f{v['flaeche']}" for k, v in top), flush=True)
        json.dump(res, open(a.out, 'w'), indent=0, ensure_ascii=False)


if __name__ == '__main__':
    main()
