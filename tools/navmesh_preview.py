#!/usr/bin/env python3
"""Vergleichsbilder Basis-Raster vs. Navmesh-Raster (je Kontinent, optional je Zone mit Kartenbild).

  python navmesh_preview.py --a work/NavData.base.lua --b work/NavData.nm.lua --out DIR [--maps public/maps --db2 cache/db2 --zones 1441,1446]
Farben: grün frei, braun Berg, blau Wasser, gelb Straße, schwarz gesperrt.
Diff-Bild: rot = war begehbar, jetzt gesperrt; cyan = neu begehbar; sonst Klasse von B abgedunkelt.
"""
import argparse, os, sys
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from roads import read_navdata, Maps  # noqa: E402

PAL = np.array([[15, 15, 15], [90, 170, 60], [150, 110, 70], [40, 90, 220], [250, 220, 40]], np.uint8)


def diff_img(ga, gb):
    img = (PAL[gb] * 0.6).astype(np.uint8)
    img[(ga > 0) & (gb == 0)] = (230, 40, 40)
    img[(ga == 0) & (gb > 0)] = (40, 230, 230)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--a', required=True); ap.add_argument('--b', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--maps'); ap.add_argument('--db2'); ap.add_argument('--tiles', default='')
    ap.add_argument('--zones', default=''); ap.add_argument('--scale', type=int, default=2)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    A = read_navdata(a.a); B = read_navdata(a.b)
    for cont in A:
        ga, gb = A[cont]['grid'], B[cont]['grid']
        Image.fromarray(np.concatenate([PAL[ga], diff_img(ga, gb), PAL[gb]], 1)).save(os.path.join(a.out, f'cont{cont}.png'))
    if a.zones and a.maps:
        M = Maps(a.db2, a.maps, a.tiles)
        for mid in a.zones.split(','):
            z = M.zones[mid]; cont = z['cont']; nav = A[cont]
            r0 = int((nav['XMAX'] - z['xmax']) / 20); r1 = int(np.ceil((nav['XMAX'] - z['xmin']) / 20))
            c0 = int((nav['YMAX'] - z['ymax']) / 20); c1 = int(np.ceil((nav['YMAX'] - z['ymin']) / 20))
            ga = A[cont]['grid'][r0:r1, c0:c1]; gb = B[cont]['grid'][r0:r1, c0:c1]
            s = a.scale
            tiles = [PAL[ga], diff_img(ga, gb), PAL[gb]]
            art = M.image(mid)
            if art is not None:
                im = Image.fromarray(art.astype(np.uint8)).resize((ga.shape[1] * s, ga.shape[0] * s))
                art = np.asarray(im)
            tiles = [np.asarray(Image.fromarray(t).resize((ga.shape[1] * s, ga.shape[0] * s), Image.NEAREST)) for t in tiles]
            if art is not None:
                blend = (0.55 * art + 0.45 * tiles[2]).astype(np.uint8)
                tiles = [art] + tiles[1:] + [blend]
            Image.fromarray(np.concatenate(tiles, 1)).save(os.path.join(a.out, f'zone{mid}.png'))
            print('zone', mid, z['name'])


if __name__ == '__main__':
    main()
