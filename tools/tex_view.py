"""Kontrollbild: Gewicht einzelner Geländetexturen über der Zonenkarte (Textur-Kuratierung).

  python tex_view.py --zone 1436 --tex 188990,188982 --out bild.png [--crop x0,y0,x1,y1]
"""
import argparse, json, os, sys
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import roads as R  # noqa: E402
from texlib import TexStore  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--adt', default='work/adt'); ap.add_argument('--db2', required=True)
    ap.add_argument('--maps', required=True); ap.add_argument('--tiles', required=True)
    ap.add_argument('--zone', required=True); ap.add_argument('--tex', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--crop', default=''); ap.add_argument('--scale', type=float, default=1.0); ap.add_argument('--gain', type=float, default=1.0)
    ap.add_argument('--names', default='work/tex_inventory.json')
    a = ap.parse_args()
    names = {int(k.split(':')[1]): v[0].replace('tileset/', '') for k, v in json.load(open(a.names)).items()}
    M = R.Maps(a.db2, a.maps, a.tiles)
    z = M.zones[a.zone]; st = TexStore(a.adt, z['cont'])
    rgb = M.image(a.zone)
    x0, y0, x1, y1 = map(int, a.crop.split(',')) if a.crop else (0, 0, 1002, 668)
    s = a.scale
    W, H = int((x1 - x0) * s), int((y1 - y0) * s)
    yy, xx = np.mgrid[0:H, 0:W]
    u = (x0 + (xx + 0.5) / s) / 1002; v = (y0 + (yy + 0.5) / s) / 668
    wy = z['ymax'] - u * (z['ymax'] - z['ymin']); wx = z['xmax'] - v * (z['xmax'] - z['xmin'])
    base = np.asarray(Image.fromarray(rgb.astype(np.uint8)).crop((x0, y0, x1, y1)).resize((W, H))).astype(np.float32)
    panels = []
    groups = [g.split('+') for g in a.tex.split(',')]
    for g in groups:
        fds = [int(f) for f in g]
        w = np.clip(st.sample(fds, wx, wy) * a.gain, 0, 1)[..., None]
        im = base * (1 - 0.75 * w) + np.array([255, 0, 200]) * 0.75 * w
        p = Image.fromarray(np.clip(im, 0, 255).astype(np.uint8))
        d = ImageDraw.Draw(p)
        label = '+'.join(names.get(f, str(f)).split('/')[-1][:-6] for f in fds)
        if len(label) > 60: label = f'{len(fds)} Texturen'
        d.rectangle((0, 0, 8 * len(label) + 6, 14), fill=(0, 0, 0)); d.text((3, 1), label, fill=(255, 255, 255))
        panels.append(p)
    cols = 2 if len(panels) > 1 else 1
    rows = (len(panels) + cols - 1) // cols
    out = Image.new('RGB', (cols * W + (cols - 1) * 4, rows * H + (rows - 1) * 4), (255, 255, 255))
    for i, p in enumerate(panels): out.paste(p, ((i % cols) * (W + 4), (i // cols) * (H + 4)))
    out.save(a.out)
    print(a.out)


if __name__ == '__main__':
    main()
