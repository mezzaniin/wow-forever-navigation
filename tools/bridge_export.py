import sys, csv, numpy as np
from scipy import ndimage as ndi
navs = np.load('nav.npy', allow_pickle=True).item()
A = {r['UiMapID']: r for r in csv.DictReader(open('UiMapAssignment.csv')) if r['OrderIndex'] == '0'}
def cell(cont, mid, u, v):
    a = A[mid]; xmin, ymin, xmax, ymax = (float(a[k]) for k in ('Region_0','Region_1','Region_3','Region_4'))
    wx = xmax - v/100*(xmax-xmin); wy = ymax - u/100*(ymax-ymin)
    n = navs[cont]; return int((n['XMAX']-wx)/20), int((n['YMAX']-wy)/20)
def labels(g): return ndi.label(g > 0, structure=np.ones((3,3)))[0]
def snap(g, r, c, lab=None, comp=None):
    best=None
    for rad in range(0, 40):
        for dr in range(-rad, rad+1):
            for dc in range(-rad, rad+1):
                rr, cc = r+dr, c+dc
                if 0<=rr<g.shape[0] and 0<=cc<g.shape[1] and g[rr,cc]>0 and (comp is None or lab[rr,cc]==comp):
                    return rr, cc
    raise SystemExit(f'kein Anker bei {r},{c}')
def corridor(g, p, q, code, width=1):
    n = int(max(abs(q[0]-p[0]), abs(q[1]-p[1]))) + 1
    for t in np.linspace(0, 1, n):
        r, c = int(round(p[0]+(q[0]-p[0])*t)), int(round(p[1]+(q[1]-p[1])*t))
        for dr in range(-width, width+1):
            for dc in range(-width, width+1):
                if g[r+dr, c+dc] == 0: g[r+dr, c+dc] = code
    return n*20
def bridge(cont, a, b, code, mode):
    g = navs[cont]['grid']; lab = labels(g)
    pa = snap(g, *cell(cont, *a)); pb = snap(g, *cell(cont, *b))
    ca, cb = lab[pa], lab[pb]
    if ca == cb: print('  schon verbunden', a, b); return
    if mode == 'closest':
        d, ind = ndi.distance_transform_edt(lab != cb, return_indices=True)
        m = np.where(lab == ca, d, np.inf); r, c = np.unravel_index(np.argmin(m), m.shape)
        pa, pb = (r, c), (ind[0][r,c], ind[1][r,c])
    print(f'  Brücke {a[0]}{a[1:]} -> {b[0]}{b[1:]}: {corridor(g, pa, pb, code):.0f} yd, Code {code}')
FREI, BERG, WASSER = 1, 2, 3
print('Östliche Königreiche')
bridge(0, ('1437', 53.5, 73.3), ('1432', 25.7, 9.0), FREI, 'direct')     # Pass von Dun Algaz (Straße)
bridge(0, ('1417', 46, 80), ('1437', 50, 40), FREI, 'closest')           # Thandolübergang
bridge(0, ('1434', 40, 50), ('1434', 27, 77), FREI, 'closest')           # Schlingendorntal / Beutebucht
print('Kalimdor')
bridge(1, ('1413', 50, 50), ('1411', 64, 85), WASSER, 'closest')         # Echoinseln
bridge(1, ('1413', 50, 50), ('1413', 74, 57), WASSER, 'closest')         # Brachland Küste
bridge(1, ('1444', 50, 50), ('1444', 31, 40), WASSER, 'closest')         # Feralas Mondfederinsel
bridge(1, ('1444', 50, 50), ('1444', 33, 60), WASSER, 'closest')         # Feralas Westküste
for cont, n in navs.items():
    lab = labels(n['grid']); sizes = np.bincount(lab.ravel())
    print(cont, 'Komponenten >300:', [(i, s) for i, s in enumerate(sizes) if i and s > 300])
np.save('nav_bridged.npy', navs, allow_pickle=True)
# Export
codes = 'abcd'
def rle(row, alphabet):
    s=[]; i=0
    while i < len(row):
        j=i
        while j < len(row) and row[j]==row[i]: j+=1
        s.append(f"{alphabet(row[i])}{j-i}"); i=j
    return '"' + ''.join(s) + '"'
compchar = lambda v: 'a' if v == 0 else (chr(ord('b') + v - 1) if v < 25 else 'z')
out = ['local _, ns = ...', '-- automatisch erzeugt aus den Weltkarten: a=gesperrt b=frei c=Berg d=Wasser; comp = zusammenhängende Bereiche', 'ns.nav = {}']
for cont, o in navs.items():
    g = o['grid']; lab = labels(g)
    # Komponenten nach Größe nummerieren (größte = b)
    sizes = np.bincount(lab.ravel()); order = [i for i in np.argsort(-sizes) if i != 0]
    remap = np.zeros(len(sizes), int)
    for k, i in enumerate(order): remap[i] = min(k + 1, 25)
    comp = remap[lab]
    out.append(f"ns.nav[{cont}] = {{ xmax = {o['XMAX']}, ymax = {o['YMAX']}, cell = 20, w = {o['W']}, h = {o['H']}, rows = {{")
    rows = [rle(r, lambda v: codes[v]) for r in g]
    for i in range(0, len(rows), 8): out.append('  ' + ','.join(rows[i:i+8]) + ',')
    out.append('}, comp = {')
    rows = [rle(r, compchar) for r in comp]
    for i in range(0, len(rows), 8): out.append('  ' + ','.join(rows[i:i+8]) + ',')
    out.append('} }')
open(sys.argv[1] + '/NavData.lua', 'w').write('\n'.join(out) + '\n')
