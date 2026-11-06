"""Bauwerke für die Navigation: Brücken, Tunnel, Tore, Pässe, Stege aus den WMO/M2-Platzierungen der ADTs.

Je Platzierung (MODF/MDDF in _obj0) eines kuratierten Modells (data-src/structures.json: Regex auf den
Listfile-Namen) wird die begehbare Fläche aus der echten Modellgeometrie gerastert:
  WMO: alle Gruppen (GFID -> Gruppendateien), Dreiecke aus MOVT/MOVI(MOVX) mit Kollision (MOPY),
       Normale nach oben (nz >= 0,6) = Boden/Fahrbahn/Tunnelsohle.
  M2:  Kollisionsnetz (collisionIndices/collisionPositions), ebenfalls nur nach oben zeigende Flächen.
Platzierung: Welt X = ORIGIN - pos.z, Y = ORIGIN - pos.x, Gierwinkel = rot.y (Modell-x zeigt in Richtung
(cos, sin) in (X, Y)), Maßstab scale/1024. Nick/Roll werden berücksichtigt (volle Rotation).
Ergebnis: JSON {kontinent: [{name, kind, uid, zellen: [[r, c], ...], wasser: bool}]} für adt_roads_v3 --structs.

  python adt_structs.py --navdata work/NavData.base.lua --adt work/adt --listfile work/listfile.csv \\
      --config data-src/structures.json --out work/structs.json
"""
import argparse, json, math, os, re, struct, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adt_fetch import chunks, fetch  # noqa: E402
from roads import read_navdata  # noqa: E402

TILE = 1600.0 / 3.0
ORIGIN = 32 * TILE
FINE = 2.5
K = 8
BUILD = '1.60.1.70790'


def get(fdid, cache, ext):
    p = os.path.join(cache, f'{fdid}.{ext}')
    st = fetch(f'https://wago.tools/api/casc/{fdid}?version={BUILD}', p)
    if st.startswith('fehler') or not os.path.exists(p): return None
    return open(p, 'rb').read()


def wmo_tris(fdid, cache):
    """Dreiecke (n,3,3) im Modellraum mit Kollision (alle Gruppen)."""
    b = get(fdid, cache, 'wmo')
    if b is None: return None
    gfid = []; ngroups = 0
    for tag, d in chunks(b):
        if tag == 'MOHD': ngroups = struct.unpack_from('<I', d, 4)[0]
        elif tag == 'GFID': gfid = list(struct.unpack_from(f'<{len(d) // 4}I', d))
    gfid = [g for g in gfid if g][:max(ngroups, 0) or len(gfid)]
    out = []
    for g in gfid:
        gb = get(g, cache, 'wmogrp')
        if gb is None: continue
        for tag, d in chunks(gb):
            if tag != 'MOGP': continue
            sub = d[0x44:]   # MOGP-Kopf 0x44 Bytes, dann Unterchunks
            mopy = movi = movt = None
            for t2, d2 in chunks(sub):
                if t2 == 'MOPY': mopy = np.frombuffer(d2, np.uint8).reshape(-1, 2)
                elif t2 == 'MOVI': movi = np.frombuffer(d2, np.uint16).astype(np.int64)
                elif t2 == 'MOVX': movi = np.frombuffer(d2, np.uint32).astype(np.int64)
                elif t2 == 'MOVT': movt = np.frombuffer(d2, np.float32).reshape(-1, 3)
            if movi is None or movt is None or not len(movi): continue
            tri = movi[:len(movi) // 3 * 3].reshape(-1, 3)
            if mopy is not None and len(mopy) == len(tri):
                fl, mat = mopy[:, 0], mopy[:, 1]
                # kollidierbar: F_COLLISION oder (F_RENDER und nicht F_DETAIL)
                coll = ((fl & 0x08) != 0) | (((fl & 0x20) != 0) & ((fl & 0x04) == 0))
                tri = tri[coll]
            if len(tri) and tri.max() < len(movt): out.append(movt[tri])
    return np.concatenate(out) if out else np.zeros((0, 3, 3), np.float32)


def m2_tris(fdid, cache):
    b = get(fdid, cache, 'm2')
    if b is None: return None
    if b[:4] == b'MD21':
        for tag, d in chunks(b):
            if tag == 'MD21': b = d; break
    if b[:4] != b'MD20': return None
    ni, oi = struct.unpack_from('<2I', b, 0xD8); nv, ov = struct.unpack_from('<2I', b, 0xE0)
    if not ni or not nv: return np.zeros((0, 3, 3), np.float32)
    idx = np.frombuffer(b, np.uint16, ni, oi).astype(np.int64)
    pos = np.frombuffer(b, np.float32, nv * 3, ov).reshape(-1, 3)
    tri = idx[:ni // 3 * 3].reshape(-1, 3)
    tri = tri[tri.max(axis=1) < nv]
    return pos[tri]


def rot_matrix(rx, ry, rz):
    """Modellraum (z oben) -> Welt (X Nord, Y West, Z oben) für die MODF/MDDF-Rotation (Grad).
    Entspricht der Client-/noggit-Matrix translate(pos)·rotY(ry-270)·rotZ(-rx)·rotX(rz-90) im Y-oben-System:
    zuerst Rollen um die lokale x-Achse (rz), dann Nicken um die lokale y-Achse (rx), dann Gieren um z (ry)."""
    a, b, c = math.radians(ry), math.radians(rx), math.radians(rz)
    Rz = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
    Ry = np.array([[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(c), -math.sin(c)], [0, math.sin(c), math.cos(c)]])
    return Rz @ Ry @ Rx


def raster(tris, nav, nzmin=0.6):
    """Fein-Pixel (2,5 yd) der nach oben zeigenden Dreiecke -> Dict Zelle -> Anzahl Feinpixel."""
    if not len(tris): return {}
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    n = np.cross(v1 - v0, v2 - v0); ln = np.linalg.norm(n, axis=1) + 1e-9
    nz = np.abs(n[:, 2]) / ln          # Wicklung uneinheitlich -> Betrag (Decken unter Boden sind selten)
    keep = (nz >= nzmin) & (ln > 1e-4)
    tris = tris[keep]
    hits = set()
    for t in tris:
        # Feinraster-Koordinaten: zeile = (XMAX - X)/FINE, spalte = (YMAX - Y)/FINE
        ys = (nav['XMAX'] - t[:, 0]) / FINE; xs = (nav['YMAX'] - t[:, 1]) / FINE
        y0, y1 = int(math.floor(ys.min())), int(math.floor(ys.max())); x0, x1 = int(math.floor(xs.min())), int(math.floor(xs.max()))
        if (y1 - y0 + 1) * (x1 - x0 + 1) > 40000: continue
        gy, gx = np.mgrid[y0:y1 + 1, x0:x1 + 1]; py = gy + 0.5; px = gx + 0.5
        (ay, by, cy), (ax, bx, cx) = ys, xs
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-9:
            hits.add((int(ys.mean()), int(xs.mean()))); continue
        l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / den
        l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / den
        inside = (l1 >= -0.05) & (l2 >= -0.05) & (l1 + l2 <= 1.05)
        for yy, xx in zip(gy[inside], gx[inside]): hits.add((int(yy), int(xx)))
        hits.add((int(ys.mean()), int(xs.mean())))
    cells = {}
    for yy, xx in hits:
        r, c = yy // K, xx // K
        if 0 <= r < nav['h'] and 0 <= c < nav['w']: cells[(r, c)] = cells.get((r, c), 0) + 1
    return cells


def placements(adtdir, names, pats):
    tiles = json.load(open(os.path.join(adtdir, 'tiles.json')))
    seen = set(); out = []
    for key, t in tiles.items():
        p = os.path.join(adtdir, f"{t.get('obj0')}.adt")
        if not t.get('obj0') or not os.path.exists(p): continue
        for tag, d in chunks(open(p, 'rb').read()):
            if tag == 'MODF':
                for j in range(len(d) // 64):
                    nid, uid = struct.unpack_from('<2I', d, j * 64)
                    pos = struct.unpack_from('<3f', d, j * 64 + 8); rot = struct.unpack_from('<3f', d, j * 64 + 20)
                    flags, _, _, scale = struct.unpack_from('<4H', d, j * 64 + 56)
                    nm = names.get(nid, '') if flags & 0x8 else ''
                    k = match(nm, pats['wmo'])
                    if k and ('w', uid) not in seen:
                        seen.add(('w', uid)); out.append(dict(kind='wmo', fd=nid, pos=pos, rot=rot, sc=(scale or 1024) / 1024 if flags & 0x4 else 1.0, name=nm, typ=k, uid=uid))
            elif tag == 'MDDF':
                for j in range(len(d) // 36):
                    nid, uid = struct.unpack_from('<2I', d, j * 36)
                    pos = struct.unpack_from('<3f', d, j * 36 + 8); rot = struct.unpack_from('<3f', d, j * 36 + 20)
                    scale, flags = struct.unpack_from('<2H', d, j * 36 + 32)
                    nm = names.get(nid, '') if flags & 0x40 else ''
                    k = match(nm, pats['m2'])
                    if k and ('m', uid) not in seen:
                        seen.add(('m', uid)); out.append(dict(kind='m2', fd=nid, pos=pos, rot=rot, sc=scale / 1024, name=nm, typ=k, uid=uid))
    return out


def match(nm, pats):
    if not nm: return None
    for typ, rx, ex in pats:
        if re.search(rx, nm) and not (ex and re.search(ex, nm)): return typ
    return None


def main():
    global BUILD
    ap = argparse.ArgumentParser()
    ap.add_argument('--navdata', required=True); ap.add_argument('--adt', default='work/adt')
    ap.add_argument('--listfile', default='work/listfile.csv')
    ap.add_argument('--config', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data-src', 'structures.json'))
    ap.add_argument('--out', required=True); ap.add_argument('--min-px', type=int, default=4)
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding='utf-8'))
    BUILD = cfg.get('build', BUILD)
    pats = {k: [(p['typ'], p['regex'], p.get('ausser')) for p in cfg[k]] for k in ('wmo', 'm2')}
    names = {}
    for line in open(a.listfile, encoding='utf-8', errors='replace'):
        i, _, n = line.strip().partition(';')
        if n.endswith('.wmo') or n.endswith('.m2'): names[int(i)] = n
    navs = read_navdata(a.navdata)
    cache = os.path.join(a.adt, 'models'); os.makedirs(cache, exist_ok=True)
    geo = {}
    res = {}
    for cont, nav in navs.items():
        pl = placements(os.path.join(a.adt, str(cont)), names, pats)
        lst = []
        for p in pl:
            key = (p['kind'], p['fd'])
            if key not in geo: geo[key] = wmo_tris(p['fd'], cache) if p['kind'] == 'wmo' else m2_tris(p['fd'], cache)
            t = geo[key]
            if t is None or not len(t): continue
            R = rot_matrix(*p['rot'])
            v = t.reshape(-1, 3).astype(np.float64) * p['sc'] @ R.T
            wx, wy, wz = ORIGIN - p['pos'][2], ORIGIN - p['pos'][0], p['pos'][1]
            v = v + np.array([wx, wy, wz]); v = v.reshape(-1, 3, 3)
            if p['typ'] == 'tor':
                # Tore: nur der Durchgang am Boden, nicht die begehbaren Mauerkronen/Türme
                n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]); up = np.abs(n[:, 2]) / (np.linalg.norm(n, axis=1) + 1e-9) >= 0.6
                if up.any():
                    zmin = v[up][:, :, 2].mean(axis=1).min()
                    v = v[v[:, :, 2].mean(axis=1) <= zmin + 3.0]
            cells = raster(v, nav)
            cl = sorted([list(k) for k, n in cells.items() if n >= a.min_px])
            if not cl: continue
            g = nav['grid']
            wet = sum(1 for r, c in cl if g[r, c] in (0, 3))
            lst.append(dict(name=p['name'].split('/')[-1], typ=p['typ'], uid=p['uid'], wx=round(wx, 1), wy=round(wy, 1), zellen=cl,
                            wasser=bool(wet and sum(1 for r, c in cl if g[r, c] == 3) >= sum(1 for r, c in cl if g[r, c] == 0))))
        res[str(cont)] = lst
        by = {}
        for s in lst: by[s['typ']] = by.get(s['typ'], 0) + 1
        print(f'Kontinent {cont}: {len(lst)} Bauwerke {by}, {sum(len(s["zellen"]) for s in lst)} Zellen', flush=True)
    json.dump(res, open(a.out, 'w'), ensure_ascii=False)


if __name__ == '__main__':
    main()
