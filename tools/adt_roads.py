"""Straßen aus den echten Geländedaten (ADT) des Forever-Clients ins Navigationsraster einbacken (v3).

Quellen
  work/adt/texstore_<map>.npz (tools/adt_texstore.py): sichtbares Gewicht jeder Geländetextur je 2,08 yd,
      Blending wie im aktuellen Client (Alphawerte additiv, Grundebene = 1 - Summe).
  data-src/road_textures.json: kuratierte Straßentexturen; je Textur optional "zonen" (UiMapIDs, sonst überall)
      und "art": "hart" (Pflaster/Straße: Gewicht = Straße) oder "weich" (Erde/Pfad: nur schmale, lange
      Bänder zählen – dieselbe Textur füllt sonst auch Flächen).
  Bauwerke (Brücken, Tunnel, Tore, Pässe): tools/adt_structs.py (begehbare Böden der WMO/M2-Modelle).

Ablauf je Kontinent (Feinraster 2,5 yd = 8x8 je 20-yd-NavData-Zelle)
  1. Straßengewicht H = Summe der harten Texturen (+ Linienantwort der weichen), leicht geglättet.
  2. Hysterese: Pixel >= lo, zusammenhängend mit Pixeln >= hi; kurze Flecken weg.
  3. Mittellinie (Skelett), Stichwege < 15 yd abschneiden, Lücken <= 20 yd in Laufrichtung schließen.
  4. Zellen: Mittellinie läuft hindurch (>= 2 Feinpixel) oder Platz (>= 50 % Straßenfläche);
     diagonale Schritte 4-fach verbunden (A* schneidet keine Ecken).
  5. Straßenzellen über gesperrten Zellen nur, wenn das Stück zwei offene Straßenstücke verbindet.

  python adt_roads.py --navdata BASIS/NavData.lua --out RoutePlaner/NavData.lua --adt work/adt \\
      --db2 . --maps maps --tiles out/tiles [--fine-out work/roads_fine.npz] [--structs work/structs.json]
"""
import argparse, json, math, os, sys
import numpy as np
from scipy import ndimage as ndi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from roads import read_navdata, write_navdata, STRASSE, WASSER, BERG, FREI  # noqa: E402
from texlib import TexStore, ORIGIN, STEP, SUB  # noqa: E402

CELL = 20.0
K = 8                    # Feinpixel je Zelle (Kante)
FINE = CELL / K          # 2,5 yd
N8 = np.ones((3, 3), bool)


# ---------------------------------------------------------------- Zonenmasken (Zellraster)
def zone_masks(navs, maps):
    """{uiMapId: bool[h,w]} aufgedeckte Fläche jeder Zone (Städte: Rechteck), 3 Zellen erweitert."""
    out = {}
    for mid, z in maps.zones.items():
        nav = navs.get(z['cont'])
        if nav is None: continue
        m = np.zeros((nav['h'], nav['w']), bool)
        r0 = max(int((nav['XMAX'] - z['xmax']) / CELL), 0); r1 = min(int(math.ceil((nav['XMAX'] - z['xmin']) / CELL)), nav['h'])
        c0 = max(int((nav['YMAX'] - z['ymax']) / CELL), 0); c1 = min(int(math.ceil((nav['YMAX'] - z['ymin']) / CELL)), nav['w'])
        cov = maps.coverage(mid)
        if cov is None:
            m[r0:r1, c0:c1] = True
        else:
            rr, cc = np.mgrid[r0:r1, c0:c1]
            wx = nav['XMAX'] - (rr + 0.5) * CELL; wy = nav['YMAX'] - (cc + 0.5) * CELL
            px = ((z['ymax'] - wy) / (z['ymax'] - z['ymin']) * 1002).astype(int)
            py = ((z['xmax'] - wx) / (z['xmax'] - z['xmin']) * 668).astype(int)
            ok = (px >= 0) & (px < 1002) & (py >= 0) & (py < 668)
            v = np.zeros(rr.shape, bool); v[ok] = cov[py[ok], px[ok]]
            m[r0:r1, c0:c1] = v
        out[mid] = ndi.binary_dilation(m, N8, iterations=3)
    return out


# ---------------------------------------------------------------- Texturgewichte ins Feinraster
def paint(store, nav, fds, zmasks):
    """Summe der Gewichte der Texturen fds (je Textur Zonenliste oder None) auf dem Feinraster (h*K, w*K)."""
    gr0 = int(math.floor((ORIGIN - nav['XMAX']) / STEP)) - 1; gc0 = int(math.floor((ORIGIN - nav['YMAX']) / STEP)) - 1
    Hn = int(nav['h'] * CELL / STEP) + 4; Wn = int(nav['w'] * CELL / STEP) + 4
    A = np.zeros((Hn, Wn), np.float32)
    ar = np.arange(SUB)
    for fd, spec in fds.items():
        zones, thr, fac = (spec.get('zonen'), spec.get('schwelle', 0.0), spec.get('faktor', 1.0)) if isinstance(spec, dict) else (spec, 0.0, 1.0)
        s = store.rows_of(int(fd)); ch = store.chunk[s]; w = store.w[s]
        if not len(ch): continue
        crow, ccol = ch // 1024, ch % 1024
        if zones:
            wx = ORIGIN - (crow * SUB + SUB / 2) * STEP; wy = ORIGIN - (ccol * SUB + SUB / 2) * STEP
            r = ((nav['XMAX'] - wx) / CELL).astype(int); c = ((nav['YMAX'] - wy) / CELL).astype(int)
            ok = (r >= 0) & (r < nav['h']) & (c >= 0) & (c < nav['w'])
            zm = np.zeros(len(ch), bool)
            for mid in zones:
                if mid in zmasks: zm[ok] |= zmasks[mid][r[ok], c[ok]]
            ch, w, crow, ccol = ch[zm], w[zm], crow[zm], ccol[zm]
        r0 = crow * SUB - gr0; c0 = ccol * SUB - gc0
        ok = (r0 >= 0) & (r0 + SUB <= Hn) & (c0 >= 0) & (c0 + SUB <= Wn)
        r0, c0, w = r0[ok], c0[ok], w[ok]
        for i0 in range(0, len(r0), 20000):
            rr = r0[i0:i0 + 20000, None, None] + ar[None, :, None]; cc = c0[i0:i0 + 20000, None, None] + ar[None, None, :]
            v = w[i0:i0 + 20000].astype(np.float32) / 255
            if thr > 0 or fac != 1.0:
                # Textur, die auch flächig dünn aufgetragen ist: nur kräftige Pinselstriche zählen
                v = np.clip((v - thr) / max(1 - thr, 1e-3) * fac, 0, 1)
            A[rr, cc] += v
    i = np.arange(nav['h'] * K); j = np.arange(nav['w'] * K)
    gr = np.floor((ORIGIN - (nav['XMAX'] - (i + 0.5) * FINE)) / STEP).astype(int) - gr0
    gc = np.floor((ORIGIN - (nav['YMAX'] - (j + 0.5) * FINE)) / STEP).astype(int) - gc0
    return np.minimum(A[np.ix_(gr, gc)], 1.0)


def line_response(S, length_yd=40, side_yd=12):
    """Schmale, lange Bänder in einem Flächengewicht S (Feinraster): max über 12 Richtungen von
    Mittel entlang einer Linie minus Mittel parallel daneben (beidseitig, kleinere Differenz)."""
    S5 = S.reshape(S.shape[0] // 2, 2, S.shape[1] // 2, 2).mean(axis=(1, 3))      # 5 yd
    L = int(length_yd / 5 / 2); off = side_yd / 5
    best = np.zeros(S5.shape, np.float32)
    R = L + int(off) + 2
    yy, xx = np.mgrid[-R:R + 1, -R:R + 1].astype(float)
    for k in range(12):
        t = math.pi * k / 12; c, s = math.cos(t), math.sin(t)
        along = xx * c + yy * s; across = -xx * s + yy * c
        on = (np.abs(along) <= L)
        kc = (on & (np.abs(across) <= 0.6)).astype(np.float32); kc /= kc.sum()
        k1 = (on & (np.abs(across - off) <= 0.6)).astype(np.float32); k1 /= k1.sum()
        k2 = (on & (np.abs(across + off) <= 0.6)).astype(np.float32); k2 /= k2.sum()
        C = ndi.convolve(S5, kc, mode='nearest'); A1 = ndi.convolve(S5, k1, mode='nearest'); A2 = ndi.convolve(S5, k2, mode='nearest')
        np.maximum(best, np.minimum(C - A1, C - A2), out=best)
    return np.repeat(np.repeat(best, 2, axis=0), 2, axis=1)


# ---------------------------------------------------------------- Skelett-Werkzeuge
def neighbours(S):
    n = ndi.convolve(S.astype(np.uint8), np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], np.uint8), mode='constant')
    return n


def prune(S, L):
    """Stichwege (Äste mit freiem Ende) bis L Pixel entfernen; echte Enden wachsen wieder nach."""
    S0 = S.copy(); S = S.copy()
    for _ in range(L):
        e = S & (neighbours(S) <= 1)
        S &= ~e
    R = S.copy()
    front = S & (neighbours(S) <= 1)
    for _ in range(L):
        new = ndi.binary_dilation(front, N8) & S0 & ~R
        if not new.any(): break
        R |= new; front = new
    return R


def endpoints_dirs(S, back=6):
    """Endpunkte des Skeletts mit Laufrichtung (Einheitsvektor vom Inneren zum Ende)."""
    ys, xs = np.nonzero(S & (neighbours(S) == 1))
    out = []
    for y, x in zip(ys, xs):
        py, px = y, x; prev = None; path = [(y, x)]
        for _ in range(back):
            nxt = None
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if (dy or dx) and 0 <= py + dy < S.shape[0] and 0 <= px + dx < S.shape[1] and S[py + dy, px + dx] and (py + dy, px + dx) != prev and (py + dy, px + dx) not in path:
                        nxt = (py + dy, px + dx)
            if nxt is None: break
            prev = (py, px); py, px = nxt; path.append(nxt)
        dy, dx = y - py, x - px; n = math.hypot(dy, dx)
        if n >= 2: out.append((y, x, dy / n, dx / n))
    return out


def draw_line(S, y0, x0, y1, x1):
    n = int(max(abs(y1 - y0), abs(x1 - x0))) + 1
    for t in np.linspace(0, 1, n + 1):
        S[int(round(y0 + (y1 - y0) * t)), int(round(x0 + (x1 - x0) * t))] = True


def bridge_gaps(S, maxd, cos_min=0.6):
    """Lücken schließen: von jedem Endpunkt zum nächsten Skelettpixel eines anderen Stücks (oder weit
    entfernten Teil desselben) innerhalb maxd Pixel, in Laufrichtung (Kegel), Linie einzeichnen."""
    lab, n = ndi.label(S, N8)
    ys, xs = np.nonzero(S)
    from scipy.spatial import cKDTree
    tree = cKDTree(np.c_[ys, xs])
    added = 0
    for y, x, dy, dx in endpoints_dirs(S):
        best = None
        for i in tree.query_ball_point((y, x), maxd):
            ty, tx = ys[i], xs[i]
            vy, vx = ty - y, tx - x; d = math.hypot(vy, vx)
            if d < 2: continue
            if lab[ty, tx] == lab[y, x] and d < maxd * 0.75 + 4: continue   # eigenes Stück, zu nah (Kurve)
            if (vy * dy + vx * dx) / d < cos_min: continue
            score = d * (2 - (vy * dy + vx * dx) / d)
            if best is None or score < best[0]: best = (score, ty, tx)
        if best:
            draw_line(S, y, x, best[1], best[2]); added += 1
    return added


# ---------------------------------------------------------------- Zellen
def to_cells(skel, mask, H, h, w, plaza=0.5, min_px=2):
    cnt = skel.reshape(h, K, w, K).sum(axis=(1, 3))
    area = mask.reshape(h, K, w, K).mean(axis=(1, 3))
    R = (cnt >= min_px) | (area >= plaza)
    # Diagonale Schritte der Mittellinie zwischen Zellen 4-fach verbinden
    ys, xs = np.nonzero(skel)
    cr, cc = ys // K, xs // K
    Hc = H.reshape(h, K, w, K).mean(axis=(1, 3))
    for dy, dx in ((1, 1), (1, -1)):
        y2, x2 = ys + dy, xs + dx
        ok = (y2 < skel.shape[0]) & (x2 >= 0) & (x2 < skel.shape[1])
        ok[ok] = skel[y2[ok], x2[ok]]
        a_r, a_c, b_r, b_c = cr[ok], cc[ok], y2[ok] // K, x2[ok] // K
        diag = (a_r != b_r) & (a_c != b_c)
        for r1, c1, r2, c2 in zip(a_r[diag], a_c[diag], b_r[diag], b_c[diag]):
            if R[r1, c2] or R[r2, c1]: continue
            if Hc[r1, c2] >= Hc[r2, c1]: R[r1, c2] = True
            else: R[r2, c1] = True
    return R


def four_connect(R, prefer):
    """Nur diagonal verbundene Straßenzellen 4-fach verbinden (Ecke mit höherem prefer wird Straße)."""
    R = R.copy()
    for _ in range(2):
        A = R[:-1, :-1] & R[1:, 1:] & ~R[:-1, 1:] & ~R[1:, :-1]       # (y,x)-(y+1,x+1)
        for y, x in zip(*np.nonzero(A)):
            if prefer[y, x + 1] >= prefer[y + 1, x]: R[y, x + 1] = True
            else: R[y + 1, x] = True
        B = R[:-1, 1:] & R[1:, :-1] & ~R[:-1, :-1] & ~R[1:, 1:]       # (y,x+1)-(y+1,x)
        for y, x in zip(*np.nonzero(B)):
            if prefer[y, x] >= prefer[y + 1, x + 1]: R[y, x] = True
            else: R[y + 1, x + 1] = True
    return R


# ---------------------------------------------------------------- Hauptteil je Kontinent
def extract(nav, store, cfg, zmasks, log):
    h, w = nav['h'], nav['w']
    hard = {fd: t for fd, t in cfg['texturen'].items() if t.get('art', 'hart') == 'hart'}
    soft = {fd: t for fd, t in cfg['texturen'].items() if t.get('art') == 'weich'}
    H = paint(store, nav, hard, zmasks)
    if soft:
        S = paint(store, nav, soft, zmasks)
        Lr = line_response(S, cfg.get('weich_laenge', 40), cfg.get('weich_abstand', 12))
        Hs = np.clip(Lr * cfg.get('weich_faktor', 1.6), 0, 1) * (S >= cfg.get('weich_min', 0.35))
        del S, Lr
        # Uferstreifen (Erde entlang Flüssen/Seen) sind keine Wege: weiche Bänder nahe Wasserzellen verwerfen
        wat = ndi.binary_dilation(nav['grid'] == WASSER, N8, iterations=cfg.get('weich_wasser', 1))
        okc = (nav['grid'] == FREI) & ~wat          # Bergschraffur-Ränder (Erde an Hängen) ebenfalls nicht
        if cfg.get('weich_berg', 0):
            okc &= ~ndi.binary_dilation(nav['grid'] == BERG, N8, iterations=cfg['weich_berg'])
        Hs[~np.repeat(np.repeat(okc, K, axis=0), K, axis=1)] = 0
        H = np.maximum(H, Hs.astype(np.float32)); del Hs
    sig = cfg.get('glaettung', 0.8)
    Hg = ndi.gaussian_filter(H, sig) if sig > 0 else H
    lo, hi = cfg.get('lo', 0.18), cfg.get('hi', 0.4)
    weak = Hg >= lo
    lab, n = ndi.label(weak, N8)
    keep = np.zeros(n + 1, bool); keep[np.unique(lab[Hg >= hi])] = True; keep[0] = False
    # kurze Flecken weg (Ausdehnung < min_len)
    sl = ndi.find_objects(lab)
    minlen = cfg.get('min_laenge', 40) / FINE
    for i, s in enumerate(sl):
        if s is None or not keep[i + 1]: continue
        if max(s[0].stop - s[0].start, s[1].stop - s[1].start) < minlen: keep[i + 1] = False
    mask = keep[lab]; del lab
    mask = ndi.binary_closing(mask, N8) | mask
    from skimage.morphology import skeletonize
    skel = skeletonize(mask)
    skel = prune(skel, int(cfg.get('stichweg', 15) / FINE))
    added = bridge_gaps(skel, cfg.get('luecke', 20) / FINE)
    R = to_cells(skel, mask, Hg, h, w, cfg.get('platz', 0.5), cfg.get('min_skelett', 2))
    log(f'    Feinraster: {int(mask.sum())} Straßenpixel, Skelett {int(skel.sum())} px, {added} Lücken geschlossen, {int(R.sum())} Zellen')
    return R, mask, skel, Hg


def finish(R, g, cfg, log, extra=None):
    """Straßenzellen ins Raster: gesperrte Zellen nur als Verbindungsstück, kleine Inseln weg."""
    R = R.copy()
    blocked = R & (g == 0)
    if blocked.any():
        openroad = R & (g > 0)
        ol, _ = ndi.label(openroad, N8)
        bl, nb = ndi.label(blocked, N8)
        keepb = np.zeros(nb + 1, bool)
        for i, s in enumerate(ndi.find_objects(bl)):
            if s is None: continue
            sub = (slice(max(s[0].start - 1, 0), s[0].stop + 1), slice(max(s[1].start - 1, 0), s[1].stop + 1))
            ring = ndi.binary_dilation(bl[sub] == i + 1, N8) & (bl[sub] != i + 1)
            touch = set(np.unique(ol[sub][ring])) - {0}
            if len(touch) >= 2 and (bl[sub] == i + 1).sum() <= cfg.get('gesperrt_max', 40): keepb[i + 1] = True
        R &= ~blocked | keepb[bl]
        log(f'    über gesperrten Zellen: {int(keepb.sum())} Verbindungsstücke ({int((keepb[bl]).sum())} Zellen) behalten, {int(blocked.sum() - keepb[bl].sum())} Zellen verworfen')
    # Straßentextur unter Wasser (versunkene Ruinen, Furten): nur kurze Querungen nahe Straßen an Land
    land = R & (g != WASSER)
    near = ndi.binary_dilation(land, N8, iterations=cfg.get('wasser_max', 2))
    drop = R & (g == WASSER) & ~near
    if drop.any(): log(f'    unter Wasser: {int(drop.sum())} Zellen verworfen')
    R &= ~drop
    if extra is not None: R |= extra
    lab, n = ndi.label(R, N8)
    if n:
        size = ndi.sum(R, lab, np.arange(1, n + 1))
        ext = np.array([max(s[0].stop - s[0].start, s[1].stop - s[1].start) for s in ndi.find_objects(lab)])
        keep = (size >= cfg.get('min_zellen', 6)) & (ext >= cfg.get('min_ausdehnung', 5))
        if extra is not None:
            hasx = ndi.maximum(extra, lab, np.arange(1, n + 1)).astype(bool)
            keep |= hasx
        R = np.concatenate([[False], keep])[lab]
    prefer = np.where(g == FREI, 3, np.where(g == STRASSE, 4, np.where(g == WASSER, 1, np.where(g == BERG, 2, 0))))
    R = four_connect(R, prefer)
    return R


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--navdata', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--adt', default='work/adt')
    ap.add_argument('--db2', default='.'); ap.add_argument('--maps', default='maps'); ap.add_argument('--tiles', default='out/tiles')
    ap.add_argument('--textures', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data-src', 'road_textures.json'))
    ap.add_argument('--structs', help='Bauwerke (adt_structs.py), JSON mit Zellen je Kontinent')
    ap.add_argument('--fine-out'); ap.add_argument('--conts', default='0,1')
    a = ap.parse_args()
    cfg = json.load(open(a.textures, encoding='utf-8'))
    navs = read_navdata(a.navdata)
    if any((o['grid'] == STRASSE).any() for o in navs.values()):
        sys.exit('NavData enthält schon Straßen (e) – bitte die Basis-NavData aus bridge_export.py angeben')
    import roads as RD
    maps = RD.Maps(a.db2, a.maps, a.tiles)
    zm_all = zone_masks(navs, maps)
    structs = json.load(open(a.structs)) if a.structs and os.path.exists(a.structs) else {}
    fines = {}
    log = lambda s: print(s, flush=True)
    for cont, nav in navs.items():
        if str(cont) not in a.conts.split(','): continue
        log(f'Kontinent {cont}:')
        store = TexStore(a.adt, cont)
        zm = {mid: m for mid, m in zm_all.items() if maps.zones[mid]['cont'] == cont}
        R, mask, skel, Hg = extract(nav, store, cfg, zm, log)
        g = nav['grid']
        extra = None
        st = structs.get(str(cont))
        if st:
            extra = np.zeros(g.shape, bool)
            for s in st:
                for r, c in s['zellen']:
                    if 0 <= r < nav['h'] and 0 <= c < nav['w']:
                        extra[r, c] = True
                        if g[r, c] == 0: g[r, c] = WASSER if s.get('wasser') else FREI
            log(f'    Bauwerke: {len(st)} ({int(extra.sum())} Zellen)')
        R = finish(R, g, cfg, log, extra)
        log(f'  Kontinent {cont}: {int(R.sum())} Straßenzellen (davon Berg {int((R & (g == BERG)).sum())}, Wasser {int((R & (g == WASSER)).sum())}, frei {int((R & (g == FREI)).sum())})')
        g[R] = STRASSE
        fines[cont] = dict(mask=np.packbits(mask), skel=np.packbits(skel), shape=np.array(mask.shape))
    if a.fine_out:
        np.savez_compressed(a.fine_out, **{f'{c}_{k}': v for c, d in fines.items() for k, v in d.items()})
    write_navdata(a.out, navs, recompute_comp=True)


if __name__ == '__main__':
    main()
