"""Straßen aus den echten Geländedaten (ADT) des Forever-Clients ins Navigationsraster einbacken.

Quelle: ADT-Kacheln (_tex0: MDID + MCNK/MCLY/MCAL, _obj0: MODF/MDDF) über tools/adt_fetch.py (wago.tools
CASC, product wowf). Straßen-Texturen: data-src/road_textures.json (kuratiert). Je Alphamap-Pixel
(0,52 yd) wird der sichtbare Anteil der Straßentexturen berechnet (Ebenen werden wie im Client der Reihe
nach überblendet: Anteil_k = a_k * Prod_{j>k}(1 - a_j)), auf ein 5-yd-Feinraster gemittelt
(Straße, wenn Anteil >= min_anteil) und auf die 20-yd-NavData-Zellen aggregiert (Zelle = Straße, wenn
mindestens MIN_FINE der 16 Feinpixel Straße sind, d. h. ein durchgehendes Band hindurchläuft).
Brücken: WMO/M2-Platzierungen, deren Modellname "bridge" enthält -> gedrehtes Rechteck aus der
Modell-Bounding-Box -> Straßenzellen (auch über Wasser/Schlucht).

  python adt_roads.py --navdata BASIS/NavData.lua --out RoutePlaner/NavData.lua --adt work/adt \
      --listfile work/listfile.csv [--fine-out work/roads_fine.npz]
"""
import argparse, json, math, os, struct, sys
import numpy as np
from scipy import ndimage as ndi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adt_fetch import chunks, fetch  # noqa: E402
from roads import read_navdata, write_navdata, STRASSE, WASSER  # noqa: E402

TILE = 1600.0 / 3.0          # 533,33 yd
CHUNK = TILE / 16            # 33,33 yd
ORIGIN = 32 * TILE           # 17066,67
FINE = 5.0                   # Feinraster (yd)
SUB = 16                     # Alphamap 64x64 -> 16x16 Stützstellen je Chunk (2,08 yd)
MIN_FINE = 3                 # Feinpixel (von 16) je 20-yd-Zelle


def alpha_maps(mcal, layers, big=True):
    """Alphamaps der Ebenen 1..n (64x64 float 0..1)."""
    out = []
    for tex, flags, off, _ in layers[1:]:
        if not flags & 0x100:
            out.append(np.zeros((64, 64), np.float32)); continue
        if flags & 0x200:                              # RLE-komprimiert
            a = bytearray(); i = off
            while len(a) < 4096 and i < len(mcal):
                b = mcal[i]; i += 1; n = b & 0x7f
                if b & 0x80: a += bytes([mcal[i]]) * n; i += 1
                else: a += mcal[i:i + n]; i += n
            a = np.frombuffer(bytes(a[:4096]).ljust(4096, b'\0'), np.uint8)
        elif big:
            a = np.frombuffer(mcal[off:off + 4096], np.uint8)
        else:                                          # 4 bit
            r = np.frombuffer(mcal[off:off + 2048], np.uint8)
            a = np.empty(4096, np.uint8); a[0::2] = (r & 15) * 17; a[1::2] = (r >> 4) * 17
        out.append(a.reshape(64, 64).astype(np.float32) / 255)
    return out


def tile_road(path, roadset):
    """Straßenanteil je Alphamap-Stützstelle: Array (16,16,SUB,SUB) [cy,cx,sy,sx] oder None."""
    b = open(path, 'rb').read()
    ids = (); res = None; ci = 0
    for tag, d in chunks(b):
        if tag == 'MDID': ids = struct.unpack_from(f'<{len(d) // 4}I', d)
        elif tag == 'MTEX' and not ids: raise SystemExit('MTEX statt MDID – Format nicht unterstützt')
        elif tag == 'MCNK':
            layers, mcal = [], b''
            for tg, dd in chunks(d):
                if tg == 'MCLY': layers = [struct.unpack_from('<4I', dd, j * 16) for j in range(len(dd) // 16)]
                elif tg == 'MCAL': mcal = dd
            isroad = [ids[l[0]] in roadset if l[0] < len(ids) else False for l in layers]
            if any(isroad):
                al = alpha_maps(mcal, layers)
                w = np.zeros((64, 64), np.float32); keep = np.ones((64, 64), np.float32)
                for k in range(len(layers) - 1, -1, -1):      # oberste Ebene zuerst
                    a = al[k - 1] if k > 0 else np.ones((64, 64), np.float32)
                    if isroad[k]: w += a * keep
                    keep *= (1 - a)
                if res is None: res = np.zeros((16, 16, SUB, SUB), np.float32)
                f = 64 // SUB
                res[ci // 16, ci % 16] = w.reshape(SUB, f, SUB, f).mean(axis=(1, 3))
            ci += 1
    return res


def fine_raster(nav, adtdir, roadset):
    H, W = nav['h'] * 4, nav['w'] * 4
    s = np.zeros(H * W, np.float64); n = np.zeros(H * W, np.float64)
    tiles = json.load(open(os.path.join(adtdir, 'tiles.json')))
    step = CHUNK / SUB
    off = (np.arange(SUB) + 0.5) * step
    for key, t in tiles.items():
        p = os.path.join(adtdir, f"{t['tex0']}.adt")
        if not t.get('tex0') or not os.path.exists(p): continue
        tx, ty = map(int, key.split('_'))
        r = tile_road(p, roadset)
        # alle Stützstellen der Kachel (auch ohne Straße) zählen, damit der Anteil stimmt
        cy, cx, sy, sx = np.meshgrid(np.arange(16), np.arange(16), np.arange(SUB), np.arange(SUB), indexing='ij')
        wx = ORIGIN - (ty * TILE + cy * CHUNK + off[sy])
        wy = ORIGIN - (tx * TILE + cx * CHUNK + off[sx])
        fr = np.floor((nav['XMAX'] - wx) / FINE).astype(np.int64); fc = np.floor((nav['YMAX'] - wy) / FINE).astype(np.int64)
        ok = (fr >= 0) & (fr < H) & (fc >= 0) & (fc < W)
        if not ok.any(): continue
        idx = (fr * W + fc)[ok]
        n += np.bincount(idx, minlength=H * W)
        if r is not None: s += np.bincount(idx, weights=r[ok], minlength=H * W)
    frac = np.where(n > 0, s / np.maximum(n, 1), 0).reshape(H, W)
    return frac


# ---------------------------------------------------------------- Brücken
def model_bbox(fdid, cache, kind):
    p = os.path.join(cache, f'{fdid}.{kind}')
    st = fetch(f'https://wago.tools/api/casc/{fdid}?version={BUILD}', p)
    if st.startswith('fehler') or not os.path.exists(p): return None
    b = open(p, 'rb').read()
    if kind == 'wmo':
        for tag, d in chunks(b):
            if tag == 'MOHD': return struct.unpack_from('<6f', d, 36)
    else:
        if b[:4] == b'MD21':
            for tag, d in chunks(b):
                if tag == 'MD21': b = d; break
        if b[:4] == b'MD20': return struct.unpack_from('<6f', b, 160)
    return None


def bridges(adtdir, names, cache):
    """Liste (cx, cy, halbe Länge, halbe Breite, Winkel, Name) in Weltkoordinaten (X=Nord, Y=West)."""
    tiles = json.load(open(os.path.join(adtdir, 'tiles.json')))
    seen, out = set(), []
    os.makedirs(cache, exist_ok=True)
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
                    if 'bridge' in nm and ('w', uid) not in seen:
                        seen.add(('w', uid)); out.append(('wmo', nid, pos, rot, (scale or 1024) / 1024, nm))
            elif tag == 'MDDF':
                for j in range(len(d) // 36):
                    nid, uid = struct.unpack_from('<2I', d, j * 36)
                    pos = struct.unpack_from('<3f', d, j * 36 + 8); rot = struct.unpack_from('<3f', d, j * 36 + 20)
                    scale, flags = struct.unpack_from('<2H', d, j * 36 + 32)
                    nm = names.get(nid, '') if flags & 0x40 else ''
                    if 'bridge' in nm and ('m', uid) not in seen:
                        seen.add(('m', uid)); out.append(('m2', nid, pos, rot, scale / 1024, nm))
    res = []
    for kind, fd, pos, rot, sc, nm in out:
        bb = model_bbox(fd, cache, kind)
        if not bb: continue
        x0, y0, z0, x1, y1, z1 = bb
        lx, ly = (x1 - x0) * sc / 2, (y1 - y0) * sc / 2
        mx, my = (x0 + x1) / 2 * sc, (y0 + y1) / 2 * sc
        wx, wy = ORIGIN - pos[2], ORIGIN - pos[0]
        res.append(dict(wx=wx, wy=wy, lx=lx, ly=ly, mx=mx, my=my, yaw=rot[1], name=nm, kind=kind))
    return res


def bridge_cells(b, nav, yaw_off):
    """Zellen (r, c) im gedrehten Rechteck der Brücke."""
    a = math.radians(b['yaw'] + yaw_off)
    ca, sa = math.cos(a), math.sin(a)
    cxw = b['wx'] + ca * b['mx'] - sa * b['my']; cyw = b['wy'] + sa * b['mx'] + ca * b['my']
    R = max(b['lx'], b['ly']) + 20
    r0 = int((nav['XMAX'] - (cxw + R)) / 20); r1 = int((nav['XMAX'] - (cxw - R)) / 20) + 1
    c0 = int((nav['YMAX'] - (cyw + R)) / 20); c1 = int((nav['YMAX'] - (cyw - R)) / 20) + 1
    if b['lx'] < b['ly']:   # Brückenlänge entlang der lokalen y-Achse -> Achsen tauschen
        b = dict(b, lx=b['ly'], ly=b['lx'], yaw=b['yaw'] + 90)
        a = math.radians(b['yaw'] + yaw_off); ca, sa = math.cos(a), math.sin(a)
    cells = []
    for r in range(max(r0, 0), min(r1, nav['h'])):
        for c in range(max(c0, 0), min(c1, nav['w'])):
            x = nav['XMAX'] - (r + 0.5) * 20 - cxw; y = nav['YMAX'] - (c + 0.5) * 20 - cyw
            u = ca * x + sa * y; v = -sa * x + ca * y
            if abs(u) <= b['lx'] + 5 and abs(v) <= min(max(b['ly'], 4), BRIDGE_HALF_WIDTH) + 2: cells.append((r, c))
    return cells


BUILD = '1.60.1.70790'
BRIDGE_HALF_WIDTH = 8.0     # yd: begehbare Fahrbahn, nicht die ganze Modell-Box (Thandolübergang)


def aggregate(frac, g, cfg):
    """Feinraster (Straßenanteil je 5 yd) -> Straßenpixel F und Straßenzellen R (20 yd).
    Glättung gegen das Pinsel-Rauschen der Alphamaps, Schwelle, Zellen mit durchgehendem Band,
    kleine Inseln (Einzelflecken der Straßentextur) entfernen."""
    sm = ndi.gaussian_filter(frac, cfg.get('glaettung', 1.0)) if cfg.get('glaettung', 1.0) > 0 else frac
    F = sm >= cfg.get('min_anteil', 0.5)
    h, w = g.shape
    cnt = F.reshape(h, 4, w, 4).sum(axis=(1, 3))
    R = (cnt >= cfg.get('min_fein', MIN_FINE)) & (g > 0)
    lab, n = ndi.label(R, structure=np.ones((3, 3)))
    if n:
        size = ndi.sum(R, lab, np.arange(1, n + 1))
        ext = np.array([max(s[0].stop - s[0].start, s[1].stop - s[1].start) for s in ndi.find_objects(lab)])
        keep = (size >= cfg.get('min_zellen', 4)) & (ext >= cfg.get('min_ausdehnung', 5))
        R = np.concatenate([[False], keep])[lab]
    return F, R


def main():
    global BUILD
    ap = argparse.ArgumentParser()
    ap.add_argument('--navdata', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--adt', default='work/adt'); ap.add_argument('--listfile', default='work/listfile.csv')
    ap.add_argument('--textures', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data-src', 'road_textures.json'))
    ap.add_argument('--fine-out'); ap.add_argument('--no-bridges', action='store_true')
    a = ap.parse_args()
    cfg = json.load(open(a.textures, encoding='utf-8'))
    BUILD = cfg.get('build', BUILD)
    roadset = {int(k) for k in cfg['texturen']}
    minfrac = cfg.get('min_anteil', 0.5)
    navs = read_navdata(a.navdata)
    if any((o['grid'] == STRASSE).any() for o in navs.values()):
        sys.exit('NavData enthält schon Straßen (e) – bitte die Basis-NavData aus bridge_export.py angeben')
    names = {}
    if not a.no_bridges:
        for line in open(a.listfile, encoding='utf-8', errors='replace'):
            i, _, n = line.strip().partition(';')
            if 'bridge' in n and (n.endswith('.wmo') or n.endswith('.m2')): names[int(i)] = n
    fines = {}
    for cont, nav in navs.items():
        cache = os.path.join(a.adt, f'frac_{cont}.npy')
        key = sorted(roadset)
        if os.path.exists(cache) and os.path.exists(cache + '.key') and json.load(open(cache + '.key')) == key:
            frac = np.load(cache)
        else:
            frac = fine_raster(nav, os.path.join(a.adt, str(cont)), roadset).astype(np.float32)
            np.save(cache, frac); json.dump(key, open(cache + '.key', 'w'))
        F, R = aggregate(frac, nav['grid'], cfg)
        fines[cont] = F
        g = nav['grid']
        nb = nw = 0
        if not a.no_bridges:
            for b in bridges(os.path.join(a.adt, str(cont)), names, os.path.join(a.adt, 'models')):
                # Ausrichtung: Modell-x entlang yaw; welche der beiden Achsen die Brückenlänge ist, entscheidet
                # das Gelände (Enden auf Land, Mitte über Wasser/gesperrt) – sonst die längere Achse
                cells = bridge_cells(b, nav, 0)
                if not cells: continue
                for r, c in cells:
                    if g[r, c] == 0 or g[r, c] == WASSER: nw += 1
                    R[r, c] = True
                    if g[r, c] == 0: g[r, c] = WASSER   # Schlucht/Lücke unter der Brücke begehbar machen
                nb += 1
        g[R] = STRASSE
        print(f'Kontinent {cont}: {int(F.sum())} Feinpixel Straße, {int(R.sum())} Straßenzellen, {nb} Brücken ({nw} Zellen über Wasser/Lücke)', flush=True)
    if a.fine_out:
        np.savez_compressed(a.fine_out, **{str(c): f for c, f in fines.items()})
    write_navdata(a.out, navs, recompute_comp=True)


if __name__ == '__main__':
    main()
