"""NUR ZUR KONTROLLE: Straßen grafisch aus den Zonenkarten erkennen (Vergleichsbilder, roads_preview.py).
Die Straßen im Raster kommen aus den ADT-Geländetexturen (adt_roads.py); hier liegen außerdem die
gemeinsamen NavData-Lese-/Schreibfunktionen.

Grafische Erkennung:

Erkennung (je Zone, 1002x668 px): Straßen sind auf den Karten helle, hellbraune Bänder (2-5 px) mit
dunkelbrauner Umrandung auf beiden Seiten. Ein gerichteter Linienfilter (12 Richtungen, Profil
hell | dunkel | hell, ~17 px lang) findet genau dieses Muster; kurze Bergschraffuren, Flussufer und
Schrift antworten schwächer. Danach: Hysterese, Farbe (warmes Hellbraun, keine Schrift/kein Wasser),
dicke Flecken (Plätze, Symbole, Felder) entfernen, nur langgestreckte Stücke behalten.

Raster: Straßenpixel -> 20-yd-Zellen (gleiche Zonen->Welt-Umrechnung wie build_nav.py). Zellen mit
genügend Straßenpixeln werden Klasse e=Straße (Wasser darunter = Brücke, nur kurz; Berg nur, wenn das
Straßenstück überwiegend durch freies Gelände läuft). Gesperrte Zellen und Komponenten (comp) bleiben
unverändert -> die Routenplanung (Schritte, Hash) ändert sich nicht, nur die Fußwege.

  python roads.py --navdata ../RoutePlaner/NavData.lua --maps maps --db2 . --tiles out/tiles \
      [--preview DIR --zones 1429,1436]
"""
import argparse, csv, os, re, sys
import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from scipy.signal import fftconvolve

CELL = 20.0
CODES = 'abcde'          # a=gesperrt b=frei c=Berg d=Wasser e=Straße
FREI, BERG, WASSER, STRASSE = 1, 2, 3, 4

# Erkennungsparameter
P = dict(hi=30.0, lo=18.0, len_half=8.0, minlen=25, fill=0.3, thick=4, px_frac=0.35,
         bridge_max=3, berg_share=0.4, min_cells=4)


def rows(path):
    with open(path, encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- NavData.lua lesen/schreiben
def rle_decode(s, w, alphabet):
    out = np.zeros(w, np.uint8); c = 0
    for ch, n in re.findall(r'([a-zA-Z])(\d+)', s):
        n = int(n); out[c:c + n] = alphabet.index(ch); c += n
    return out


def rle(row, ch):
    s = []; i = 0; n = len(row)
    while i < n:
        j = i
        while j < n and row[j] == row[i]: j += 1
        s.append(f'{ch(row[i])}{j - i}'); i = j
    return '"' + ''.join(s) + '"'


def read_navdata(path):
    txt = open(path, encoding='utf-8').read()
    navs = {}
    for m in re.finditer(r'ns\.nav\[(\d+)\] = \{ xmax = ([-\d.e]+), ymax = ([-\d.e]+), cell = (\d+), w = (\d+), h = (\d+), (?:v = \d+, )?rows = \{(.*?)\}, comp = \{(.*?)\} \}', txt, re.S):
        cont = int(m.group(1)); w, h = int(m.group(5)), int(m.group(6))
        rws = re.findall(r'"([^"]*)"', m.group(7))
        grid = np.stack([rle_decode(r, w, CODES) for r in rws])
        comp = re.findall(r'"([^"]*)"', m.group(8))
        navs[cont] = dict(xmax=m.group(2), ymax=m.group(3), XMAX=float(m.group(2)), YMAX=float(m.group(3)),
                          cell=int(m.group(4)), w=w, h=h, grid=grid, comp=comp, base_open=grid > 0)
        assert grid.shape == (h, w)
    return navs


def comp_rows(g):
    """Komponenten wie bridge_export.py (8er-Nachbarschaft, nach Größe nummeriert, größte = b)."""
    lab = ndi.label(g > 0, structure=np.ones((3, 3)))[0]
    sizes = np.bincount(lab.ravel()); order = [i for i in np.argsort(-sizes) if i != 0]
    remap = np.zeros(len(sizes), int)
    for k, i in enumerate(order): remap[i] = min(k + 1, 25)
    compchar = lambda v: 'a' if v == 0 else (chr(ord('b') + v - 1) if v < 25 else 'z')
    return [rle(r, compchar)[1:-1] for r in remap[lab]]


def write_navdata(path, navs, recompute_comp=False):
    out = ['local _, ns = ...',
           '-- automatisch erzeugt: Gelände aus dem Forever-Navmesh (TrinityCore mmaps, tools/navmesh_fuse.py) bzw. den Weltkarten, Straßen aus ADT-Geländetexturen (tools/adt_roads.py) + NPC-Patrouillen;',
           '-- a=gesperrt b=frei c=Berg d=Wasser e=Straße/Weg/Brücke;',
           '-- comp = zusammenhängende Bereiche. Format-Version 2 (v = 2): Klasse e neu, ältere Nav.lua kennen sie nicht.',
           'ns.navVersion = 2',
           'ns.nav = {}']
    for cont, o in navs.items():
        if recompute_comp and o.get('base_open') is not None and not np.array_equal(o['base_open'], o['grid'] > 0):
            o['comp'] = comp_rows(o['grid'])
            print(f'  Kontinent {cont}: Komponenten neu berechnet (Brücken über gesperrte Zellen)')
        out.append(f"ns.nav[{cont}] = {{ xmax = {o['xmax']}, ymax = {o['ymax']}, cell = {o['cell']}, w = {o['w']}, h = {o['h']}, v = 2, rows = {{")
        rr = [rle(r, lambda v: CODES[v]) for r in o['grid']]
        for i in range(0, len(rr), 8): out.append('  ' + ','.join(rr[i:i + 8]) + ',')
        out.append('}, comp = {')
        cr = ['"' + c + '"' for c in o['comp']]
        for i in range(0, len(cr), 8): out.append('  ' + ','.join(cr[i:i + 8]) + ',')
        out.append('} }')
    open(path, 'w', encoding='utf-8').write('\n'.join(out) + '\n')


# ---------------------------------------------------------------- Karten
class Maps:
    def __init__(self, db2, maps, tiles):
        self.maps, self.tiles = maps, tiles
        self.uimap = {r['ID']: r for r in rows(os.path.join(db2, 'UiMap.csv'))}
        self.xart = {r['UiMapID']: r['UiMapArtID'] for r in rows(os.path.join(db2, 'UiMapXMapArt.csv')) if r['PhaseID'] == '0'}
        self.ovl = {}
        for r in rows(os.path.join(db2, 'WorldMapOverlay.csv')):
            if r.get('PlayerConditionID', '0') == '0': self.ovl.setdefault(r['UiMapArtID'], []).append(r)
        self.otiles = {}
        for r in rows(os.path.join(db2, 'WorldMapOverlayTile.csv')):
            if r.get('LayerIndex', '0') == '0': self.otiles.setdefault(r['WorldMapOverlayID'], []).append(r)
        assign = {r['UiMapID']: r for r in rows(os.path.join(db2, 'UiMapAssignment.csv')) if r['OrderIndex'] == '0'}
        self.zones = {}
        for mid, m in self.uimap.items():
            a = assign.get(mid)
            if m['Type'] != '3' or not a or a['MapID'] not in ('0', '1'): continue
            self.zones[mid] = dict(cont=int(a['MapID']), xmin=float(a['Region_0']), ymin=float(a['Region_1']),
                                   xmax=float(a['Region_3']), ymax=float(a['Region_4']), name=m['Name_lang'])

    def image(self, mid):
        for ext in ('png', 'webp'):
            p = os.path.join(self.maps, f'{mid}.{ext}')
            if os.path.exists(p): return np.asarray(Image.open(p).convert('RGB')).astype(np.float32)

    def coverage(self, mid):
        """Aufgedeckte Fläche (Overlays) wie masks.zone_mask; None = keine Overlays (Stadt)."""
        art = self.xart.get(mid)
        if not self.ovl.get(art): return None
        a = np.zeros((1400, 1400), np.uint8)
        for o in self.ovl[art]:
            ox, oy = int(o['OffsetX']), int(o['OffsetY'])
            for t in self.otiles.get(o['ID'], []):
                p = os.path.join(self.tiles, f"{t['FileDataID']}.blp")
                if not os.path.exists(p): continue
                al = np.asarray(Image.open(p).convert('RGBA').getchannel('A'))
                x0, y0 = ox + int(t['ColIndex']) * 256, oy + int(t['RowIndex']) * 256
                h, w = al.shape
                a[y0:y0 + h, x0:x0 + w] = np.maximum(a[y0:y0 + h, x0:x0 + w], al)
        return a[:668, :1002] > 128


# ---------------------------------------------------------------- Erkennung
_K = {}


def strips(theta, core, lh, ss=4):
    """Streifen-Kerne quer zur Richtung theta: Kern |t|<=core, Umrandung links/rechts (core..core+2),
    außen links/rechts (core+2..core+4); jeweils über die Länge 2*lh gemittelt."""
    key = (round(theta, 4), core, lh)
    if key in _K: return _K[key]
    R = int(lh + core + 5)
    o = (np.arange(ss) + 0.5) / ss - 0.5
    yy, xx = np.mgrid[-R:R + 1, -R:R + 1].astype(float)
    ks = [np.zeros(yy.shape) for _ in range(5)]
    c, s = np.cos(theta), np.sin(theta)
    for dy in o:
        for dx in o:
            y, x = yy + dy, xx + dx
            along = x * c + y * s; t = -x * s + y * c
            on = np.abs(along) <= lh
            ks[0] += on & (np.abs(t) <= core)
            ks[1] += on & (t > core) & (t <= core + 2)
            ks[2] += on & (t < -core) & (t >= -core - 2)
            ks[3] += on & (t > core + 2) & (t <= core + 4)
            ks[4] += on & (t < -core - 2) & (t >= -core - 4)
    ks = [k / k.sum() for k in ks]
    _K[key] = ks
    return ks


def response(L):
    """Straßenmuster hell|dunkel|hell beidseitig: min(Kern-Umrandung links, rechts) + halbe Rückkehr außen."""
    best = np.full(L.shape, -1e9, np.float32)
    for core in (1.5, 2.5, 3.5):
        for i in range(12):
            C, O1, O2, E1, E2 = (fftconvolve(L, k, mode='same') for k in strips(np.pi * i / 12, core, P['len_half']))
            r = np.minimum(C - O1, C - O2) + 0.5 * np.clip(np.minimum(E1 - O1, E2 - O2), -20, 10)
            np.maximum(best, r, out=best)
    return best


def elongated(m, minlen, fill):
    lab, n = ndi.label(m, structure=np.ones((3, 3)))
    if not n: return m
    sl = ndi.find_objects(lab); size = ndi.sum(m, lab, np.arange(1, n + 1))
    keep = np.zeros(n + 1, bool)
    for i, s in enumerate(sl):
        h, w = s[0].stop - s[0].start, s[1].stop - s[1].start
        if max(h, w) >= minlen and size[i] / (h * w) < fill: keep[i + 1] = True
    return keep[lab]


def detect(rgb, cov):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    L = 0.3 * r + 0.55 * g + 0.15 * b
    resp = response(L)
    # warmes Hellbraun; Schrift (sehr hell, wenig Rot-Überschuss) und Wasser (bläulich) raus
    color = (r - b > 50) & (b < 0.85 * g) & (r - g > 5) & ~((L > 185) & (r - g < 35))
    weak = (resp > P['lo']) & color
    if cov is not None: weak &= cov
    lab, n = ndi.label(weak, structure=np.ones((3, 3)))
    seeds = np.unique(lab[(resp > P['hi']) & weak]); seeds = seeds[seeds > 0]
    keep = np.zeros(n + 1, bool); keep[seeds] = True
    m = keep[lab]
    m = ndi.binary_closing(m, structure=np.ones((3, 3)))
    # dicke Flecken (Plätze, Gebäude, Felder) entfernen: was eine Öffnung mit großem Kern übersteht
    t = P['thick']
    blob = ndi.binary_opening(m, structure=np.ones((2 * t + 1, 2 * t + 1)))
    blob = ndi.binary_dilation(blob, iterations=2)
    m &= ~blob
    m = elongated(m, P['minlen'], P['fill'])
    if cov is not None: m &= cov
    return m


# ---------------------------------------------------------------- ins Raster
def project(navs, zones, masks):
    votes = {c: np.zeros((o['h'], o['w']), np.float32) for c, o in navs.items()}
    need = {}
    for mid, m in masks.items():
        z = zones[mid]; o = navs[z['cont']]
        py, px = np.nonzero(m)
        u = (px + 0.5) / 1002; v = (py + 0.5) / 668
        wy = z['ymax'] - u * (z['ymax'] - z['ymin']); wx = z['xmax'] - v * (z['xmax'] - z['xmin'])
        rr = ((o['XMAX'] - wx) / CELL).astype(int); cc = ((o['YMAX'] - wy) / CELL).astype(int)
        ok = (rr >= 0) & (rr < o['h']) & (cc >= 0) & (cc < o['w'])
        # Straßenpixel je Zelle relativ zur Zellbreite in Pixeln (große Zonen: 1 px reicht)
        pxsize = (z['ymax'] - z['ymin']) / 1002
        np.add.at(votes[z['cont']], (rr[ok], cc[ok]), pxsize / CELL)
    out = {}
    for c, o in navs.items():
        out[c] = votes[c] >= P['px_frac']
    return out


def apply(navs, roadcells, blocked=None):
    stats = {}
    for c, o in navs.items():
        g = o['grid']
        R = roadcells[c] & (g > 0)
        if blocked is not None: R &= ~blocked[c]
        # 1-Zellen-Lücken schließen (Beschriftungen, Symbole auf der Straße)
        R = (ndi.binary_closing(R, structure=np.ones((3, 3))) | R) & (g > 0)
        if blocked is not None: R &= ~blocked[c]
        # Berg: nur wo ringsum (11x11 Zellen) genug Straße auf freiem Gelände liegt -> Schraffur-Fehlerkennungen
        # mitten im Gebirge fallen weg, Straßen durch als Berg erkannte Talränder bleiben
        win = 11
        free = ndi.uniform_filter((R & (g == FREI)).astype(np.float32), win)
        allr = ndi.uniform_filter(R.astype(np.float32), win)
        R &= (g != BERG) | (free >= P['berg_share'] * allr)
        # Brücken: Wasserzellen nur nahe an Straßenzellen auf Land (kurze Querungen)
        landroad = R & (g != WASSER)
        near = ndi.binary_dilation(landroad, structure=np.ones((3, 3)), iterations=P['bridge_max'])
        R &= (g != WASSER) | near
        # kleine Reste weg
        lab, n = ndi.label(R, structure=np.ones((3, 3)))
        if n:
            size = ndi.sum(R, lab, np.arange(1, n + 1)); fr = ndi.sum(R & (g == FREI), lab, np.arange(1, n + 1))
            keep = np.concatenate([[False], (size >= P['min_cells']) & (fr >= 2)])
            R = keep[lab]
        stats[c] = dict(road=int(R.sum()), water=int((R & (g == WASSER)).sum()), berg=int((R & (g == BERG)).sum()))
        g[R] = STRASSE
    return stats


def city_cells(navs, M):
    """Zellen der Stadtkarten (ohne Overlays): dort keine Straßen aus den umliegenden Zonenkarten."""
    out = {c: np.zeros((o['h'], o['w']), bool) for c, o in navs.items()}
    for mid, z in M.zones.items():
        if M.coverage(mid) is not None: continue
        o = navs.get(z['cont'])
        if o is None: continue
        r0 = int((o['XMAX'] - z['xmax']) / CELL); r1 = int(np.ceil((o['XMAX'] - z['xmin']) / CELL))
        c0 = int((o['YMAX'] - z['ymax']) / CELL); c1 = int(np.ceil((o['YMAX'] - z['ymin']) / CELL))
        out[z['cont']][max(r0, 0):r1, max(c0, 0):c1] = True
    return out


def preview(path, rgb, cov, mask, z, nav):
    im = rgb.copy() * 0.6
    if cov is not None: im[~cov] *= 0.4
    # Rasterzellen der Straße zurück in Kartenpixel
    H, W = mask.shape
    yy, xx = np.mgrid[0:H, 0:W]
    wy = z['ymax'] - (xx + 0.5) / 1002 * (z['ymax'] - z['ymin']); wx = z['xmax'] - (yy + 0.5) / 668 * (z['xmax'] - z['xmin'])
    rr = ((nav['XMAX'] - wx) / CELL).astype(int); cc = ((nav['YMAX'] - wy) / CELL).astype(int)
    ok = (rr >= 0) & (rr < nav['h']) & (cc >= 0) & (cc < nav['w'])
    cls = np.zeros((H, W), np.uint8); cls[ok] = nav['grid'][rr[ok], cc[ok]]
    cellroad = cls == STRASSE
    berg = cls == BERG
    im[berg] = im[berg] * 0.7 + np.array([120, 0, 0]) * 0.3
    im[cellroad] = im[cellroad] * 0.4 + np.array([0, 220, 255]) * 0.6
    im[mask] = [255, 0, 255]
    Image.fromarray(np.clip(im, 0, 255).astype(np.uint8)).save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--navdata', required=True)
    ap.add_argument('--out')
    ap.add_argument('--maps', default='maps'); ap.add_argument('--db2', default='.'); ap.add_argument('--tiles', default='out/tiles')
    ap.add_argument('--preview'); ap.add_argument('--zones', default='')
    ap.add_argument('--only', default='', help='nur diese Zonen erkennen (Test)')
    a = ap.parse_args()
    navs = read_navdata(a.navdata)
    for o in navs.values():
        if (o['grid'] == STRASSE).any(): sys.exit('NavData enthält schon Straßen (e) – bitte aus der Basis-NavData erzeugen')
    M = Maps(a.db2, a.maps, a.tiles)
    only = set(a.only.split(',')) - {''}
    masks, covs, imgs = {}, {}, {}
    for mid, z in sorted(M.zones.items(), key=lambda kv: int(kv[0])):
        if only and mid not in only: continue
        cov = M.coverage(mid)
        if cov is None: continue                  # Städte: dichte Zeichnung, keine Straßenerkennung
        rgb = M.image(mid)
        if rgb is None: continue
        m = detect(rgb, cov)
        masks[mid] = m; covs[mid] = cov; imgs[mid] = rgb
        print(f'  {mid} {z["name"]}: {m.mean() * 100:.2f} % Straßenpixel', flush=True)
    stats = apply(navs, project(navs, M.zones, masks), city_cells(navs, M))
    for c, s in stats.items(): print(f'Kontinent {c}: {s["road"]} Straßenzellen ({s["water"]} Brücke, {s["berg"]} Berg)')
    write_navdata(a.out or a.navdata, navs)
    if a.preview:
        os.makedirs(a.preview, exist_ok=True)
        for spec in filter(None, a.zones.split(',')):
            mid, _, name = spec.partition(':')
            if mid not in masks: continue
            z = M.zones[mid]
            preview(os.path.join(a.preview, f'5x-roads-{name or mid}.png'), imgs[mid], covs[mid], masks[mid], z, navs[z['cont']])


if __name__ == '__main__':
    main()
