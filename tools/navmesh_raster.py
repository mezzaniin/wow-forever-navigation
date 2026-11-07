#!/usr/bin/env python3
"""Rastert die Navmesh-Dreiecke (navmesh_dump, NMD2) auf ein feines Kontinentraster (Standard 4 yd).

Ausgabe <out>/navmesh_<map>.npz:
  ground  uint8   Anzahl/Flag: Boden-Polygon (Area 11) eines behaltenen Bereichs deckt die Zellmitte
  steep   bool    steiler Boden (Area 10)
  water   bool    Wasser (9) / Magma-Schleim (8)
  zmin, zmax float32  Höhenbereich aller begehbaren (11/10) Flächen an der Zellmitte (NaN = keine)
  comp    int32   Bereichs-ID (größte Fläche an der Zellmitte, 0 = keine) nach Umnummerierung (1 = größter)
  meta    xmax, ymax, fine, H, W
Bereiche (zusammenhängende Navmesh-Teile) mit < --min-area yd² werden verworfen (Dächer, Bergspitzen,
unerreichbare Taschen) – außer sie enthalten Wasser-Verbindungen (dann sind sie Teil des großen Bereichs).
Raster-Geometrie wie NavData: Zeile r -> Welt-X = xmax - (r+0.5)*cell, Spalte c -> Welt-Y = ymax - (c+0.5)*cell.
"""
import argparse, json, os, sys, time
import numpy as np

GRIDS = {0: dict(xmax=3837.4997558594, ymax=3449.9997558594, w=510, h=964),
         1: dict(xmax=11831.25, ymax=5441.6665039062, w=690, h=1116)}
REC = np.dtype([('comp', '<u4'), ('area', 'u1'), ('pad', 'u1', 3), ('v', '<f4', (3, 3))])


def load(path):
    raw = open(path, 'rb').read()
    assert raw[:4] == b'NMD2', 'falsches Format'
    n = int(np.frombuffer(raw, '<u4', 1, 4)[0])
    return np.frombuffer(raw, REC, n, 8)


def raster(tris, g, fine, min_area, log):
    H, W = g['h'] * 20 // fine, g['w'] * 20 // fine
    X = tris['v'][:, :, 0].astype(np.float64); Y = tris['v'][:, :, 1].astype(np.float64); Z = tris['v'][:, :, 2]
    # Fläche je Dreieck (2D) -> Bereichsgrößen
    a2 = 0.5 * np.abs((X[:, 1] - X[:, 0]) * (Y[:, 2] - Y[:, 0]) - (X[:, 2] - X[:, 0]) * (Y[:, 1] - Y[:, 0]))
    comp = tris['comp'].astype(np.int64); area = tris['area']
    csize = np.bincount(comp, weights=a2)
    order = np.argsort(-csize)
    log(f'  {len(tris)} Dreiecke, {np.count_nonzero(csize)} Bereiche; größte (yd²): ' +
        ', '.join(f'{csize[i]:.0f}' for i in order[:12]))
    keep_c = csize >= min_area
    log(f'  behalten: {keep_c.sum()} Bereiche ({csize[keep_c].sum() / csize.sum() * 100:.1f} % der Fläche)')
    rank = np.zeros(len(csize), np.int32); rank[order] = np.arange(1, len(order) + 1)
    # Rasterkoordinaten (kontinuierlich, in feinen Zellen)
    R = (g['xmax'] - X) / fine; C = (g['ymax'] - Y) / fine
    ground = np.zeros((H, W), np.uint8); steep = np.zeros((H, W), bool); water = np.zeros((H, W), bool)
    zmin = np.full(H * W, np.inf, np.float32); zmax = np.full(H * W, -np.inf, np.float32)
    best = np.zeros(H * W, np.int32)  # Bereichsrang (klein = groß) an der Zellmitte
    best[:] = 1 << 30
    r0 = np.floor(R.min(1) - 0.5).astype(np.int64); r1 = np.ceil(R.max(1) - 0.5).astype(np.int64)
    c0 = np.floor(C.min(1) - 0.5).astype(np.int64); c1 = np.ceil(C.max(1) - 0.5).astype(np.int64)
    span = np.maximum(r1 - r0, c1 - c0) + 1
    # Kantenfunktionen vorbereiten
    def edge(ax, ay, bx, by, px, py):
        return (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    sel_keep = keep_c[comp]
    for lo, hi in ((0, 1), (1, 2), (2, 4), (4, 8), (8, 16), (16, 64), (64, 100000)):
        idx = np.nonzero((span > lo) & (span <= hi))[0]
        if not len(idx):
            continue
        k = hi if hi < 100000 else int(span[idx].max())
        for part in np.array_split(idx, max(1, len(idx) * k * k // 8_000_000 + 1)):
            dr, dc = np.mgrid[0:k, 0:k]; dr = dr.ravel(); dc = dc.ravel()
            rr = r0[part][:, None] + dr[None]; cc = c0[part][:, None] + dc[None]
            pr = rr + 0.5; pc = cc + 0.5
            Rr, Cc = R[part], C[part]
            e0 = edge(Rr[:, 0:1], Cc[:, 0:1], Rr[:, 1:2], Cc[:, 1:2], pr, pc)
            e1 = edge(Rr[:, 1:2], Cc[:, 1:2], Rr[:, 2:3], Cc[:, 2:3], pr, pc)
            e2 = edge(Rr[:, 2:3], Cc[:, 2:3], Rr[:, 0:1], Cc[:, 0:1], pr, pc)
            inside = ((e0 >= 0) & (e1 >= 0) & (e2 >= 0)) | ((e0 <= 0) & (e1 <= 0) & (e2 <= 0))
            inside &= (rr >= 0) & (rr < H) & (cc >= 0) & (cc < W) & (rr <= r1[part][:, None]) & (cc <= c1[part][:, None])
            ti, pi = np.nonzero(inside)
            t = part[ti]; lin = rr[ti, pi] * W + cc[ti, pi]
            # Höhe an der Zellmitte (baryzentrisch)
            den = e0[ti, pi] + e1[ti, pi] + e2[ti, pi]
            den = np.where(np.abs(den) < 1e-12, 1e-12, den)
            # e1 gehört zu Ecke 0, e2 zu Ecke 1, e0 zu Ecke 2
            z = (e1[ti, pi] * Z[t, 0] + e2[ti, pi] * Z[t, 1] + e0[ti, pi] * Z[t, 2]) / den
            _accumulate(ground, steep, water, zmin, zmax, best, lin, t, z, area, sel_keep, rank[comp[t]], W)
        log(f'    Größe ≤{hi}: {len(idx)} Dreiecke')
    # Splitter: Schwerpunkt jedes Dreiecks markieren (dünne Stege/Brücken unter der Zellauflösung)
    cr = np.floor(R.mean(1)).astype(np.int64); ccn = np.floor(C.mean(1)).astype(np.int64)
    ok = (cr >= 0) & (cr < H) & (ccn >= 0) & (ccn < W)
    t = np.nonzero(ok)[0]; lin = cr[t] * W + ccn[t]
    _accumulate(ground, steep, water, zmin, zmax, best, lin, t, Z[t].mean(1), area, sel_keep, rank[comp[t]], W)
    zmin = zmin.reshape(H, W); zmax = zmax.reshape(H, W)
    zmin[~np.isfinite(zmin)] = np.nan; zmax[~np.isfinite(zmax)] = np.nan
    best = best.reshape(H, W); best[best == 1 << 30] = 0
    return dict(ground=ground, steep=steep, water=water, zmin=zmin, zmax=zmax, comp=best,
                csize=csize[order[:2000]].astype(np.float32))


def _accumulate(ground, steep, water, zmin, zmax, best, lin, t, z, area, keep, crank, W):
    a = area[t]; k = keep[t]
    g = (a == 11) & k; s = (a == 10) & k; w = ((a == 9) | (a == 8)) & k
    gf = ground.ravel(); sf = steep.ravel(); wf = water.ravel()
    gf[lin[g]] = 1; sf[lin[s]] = True; wf[lin[w]] = True
    m = (g | s)
    np.minimum.at(zmin, lin[m], z[m].astype(np.float32)); np.maximum.at(zmax, lin[m], z[m].astype(np.float32))
    np.minimum.at(best, lin[k], crank[k])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bin', required=True, help='Ordner mit nav<map>.bin')
    ap.add_argument('--out', required=True); ap.add_argument('--maps', default='0,1')
    ap.add_argument('--fine', type=int, default=4); ap.add_argument('--min-area', type=float, default=20000)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for m in map(int, a.maps.split(',')):
        t0 = time.time(); log = lambda s: print(s, flush=True)
        log(f'Karte {m}')
        tris = load(os.path.join(a.bin, f'nav{m}.bin'))
        g = GRIDS[m]
        res = raster(tris, g, a.fine, a.min_area, log)
        np.savez_compressed(os.path.join(a.out, f'navmesh_{m}.npz'), **res,
                            meta=np.array([g['xmax'], g['ymax'], a.fine, res['ground'].shape[0], res['ground'].shape[1]]))
        log(f'  Boden {res["ground"].mean() * 100:.1f} %, steil {res["steep"].mean() * 100:.1f} %, Wasser {res["water"].mean() * 100:.1f} % '
            f'({time.time() - t0:.0f} s)')


if __name__ == '__main__':
    main()
