#!/usr/bin/env python3
"""Navmesh (TrinityCore mmaps aus dem Forever-Client) und NPC-Patrouillen ins NavData-Raster einbacken.

Drei Schritte (jeder schreibt eine neue NavData.lua):

  terrain  Basis-Raster (bridge_export.py, Kartenbild-Gelände) -> Gelände aus dem Navmesh
           work/navmesh/navmesh_<map>.npz (tools/navmesh_raster.py, 4-yd-Feinraster, 5x5 je Zelle)
           * Zelle frei (b), wenn >= --frei Anteil der Feinzellen begehbarer Boden eines erreichbaren
             Navmesh-Bereichs ist (Bereich 1 = größter, sonst nur mit >= --min-spawns NPC/Objekt-Spawns:
             Dächer, Bergspitzen, unerreichbare Taschen fallen weg).
           * Berg (c): steil (mittlere Neigung > --steil), im Kartenbild als Bergrücken markiert, oder bisher
             gesperrt und nicht flach-offen. Wenig Boden (>= --berg Anteil: Stege, Gassen, Tunnel) zählt als frei,
             wenn flach.
           * Wasser (d): Navmesh-Wasser; offenes Meer nur bis --kueste Zellen vor Land.
           * gesperrt (a): kein Navmesh; außerdem Klippen: zwei Nachbarzellen mit Boden, deren Böden an der
             gemeinsamen Kante nirgends ohne Höhensprung (> --sprung yd je 4 yd) zusammenhängen ->
             die Zelle mit weniger Boden wird gesperrt (A* läuft sonst Klippen hinunter/hinauf).
           Außerhalb des Navmesh (keine ADT-Kachel) bleibt das Basis-Raster.

  roads    Navmesh-Gelände + Straßen (e) aus adt_roads.py auf dem bisherigen Basis-Raster (siehe overlay_roads)

  npcroads Fertiges Raster (adt_roads.py) + NPC-Wege: work/npc_paths.json (QuestieDB, wowmap
           scripts/navmesh_npc_paths.py) und work/npc_paths_cmangos.json (cMaNGOS classic-db creature_movement,
           wowmap scripts/navmesh_cmangos_paths.py)
           * Nur freundliche NPCs (Wachen, Boten, Karawanen, Eskorten; feindliche Patrouillen liegen nur zu
             ~12 % auf Texturstraßen, freundliche zu ~45 %), Wegausdehnung >= --min-ausdehnung yd, Punkte zu
             >= 90 % auf begehbarem Navmesh-Boden (keine Flieger/Schwimmer).
           * Wege zu >= --min-strasse auf Texturstraßen gelten als Straßenläufer; ihre Stücke abseits der
             Texturstraßen (fehlende/zu blasse Textur) zählen ab --min-laenge yd, wenn sie an eine Straße
             anschließen. Andere Wege (Eskorten, Querfeldein) nur, wo >= 2 NPCs dieselben Zellen ablaufen.
           * Straße (e) nur über freien/Berg-Zellen; diagonale Schritte 4-fach verbunden.

  python navmesh_fuse.py terrain --navdata work/NavData.base.lua --navmesh work/navmesh --spawns work/spawns.json --out work/NavData.nm.lua
  python navmesh_fuse.py roads --terrain work/NavData.nm.lua --roads work/NavData.v3.lua --navmesh work/navmesh --structs work/structs.json --out work/NavData.nmroads.lua
  python navmesh_fuse.py npcroads --navdata work/NavData.nmroads.lua --navmesh work/navmesh --paths work/npc_paths.json work/npc_paths_cmangos.json --out RoutePlaner/NavData.lua
"""
import argparse, json, os, sys
import numpy as np
from scipy import ndimage as ndi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from roads import read_navdata, write_navdata, STRASSE, WASSER, BERG, FREI  # noqa: E402

GESPERRT = 0
N8 = np.ones((3, 3), bool)
N4 = ndi.generate_binary_structure(2, 1)


def _c4():
    c = np.zeros((3, 3), int); c[1, 1] = 1
    return c


def load_mesh(d, cont):
    z = np.load(os.path.join(d, f'navmesh_{cont}.npz'))
    m = {k: z[k] for k in z.files}
    xmax, ymax, fine, H, W = m['meta']
    m['fine'] = float(fine); m['K'] = int(round(20 / fine))
    return m


def legit_components(m, spawns, xmax, ymax, min_spawns, log):
    """Bereichsränge, die als erreichbar gelten: größter + Bereiche mit genügend Spawns."""
    comp = m['comp']; H, W = comp.shape; f = m['fine']
    pts = np.array(spawns, float).reshape(-1, 2)
    r = ((xmax - pts[:, 0]) / f).astype(int); c = ((ymax - pts[:, 1]) / f).astype(int)
    ok = (r >= 0) & (r < H) & (c >= 0) & (c < W)
    # Spawn trifft Bereich, wenn innerhalb 3 Feinzellen (12 yd) Boden dieses Bereichs liegt
    near = ndi.grey_dilation(np.where(comp > 0, -comp, -(1 << 30)), size=(7, 7))  # kleinster Rang in der Nähe
    rk = -near[r[ok], c[ok]]
    rk = rk[(rk > 0) & (rk < 1 << 29)]
    cnt = np.bincount(rk, minlength=comp.max() + 1)
    legit = cnt >= min_spawns
    legit[1] = True; legit[0] = False
    n = int(comp.max())
    log(f'    Navmesh-Bereiche: {n} (>= Mindestfläche), erreichbar: {int(legit.sum())} '
        f'(Spawns je Bereich ≥{min_spawns}); verworfen: {n - int(legit.sum())}')
    return legit


def cell_stats(m, legit, H, W, jump):
    """Je 20-yd-Zelle: Bodenanteil, Wasseranteil, Neigung und Klippenkanten (rechts/unten)."""
    K = m['K']
    comp = m['comp'][:H * K, :W * K]
    ground = (m['ground'][:H * K, :W * K] > 0) & legit[comp]
    water = m['water'][:H * K, :W * K] & ~ground
    zmin = m['zmin'][:H * K, :W * K]; zmax = m['zmax'][:H * K, :W * K]
    zmin = np.where(ground, zmin, np.nan); zmax = np.where(ground, zmax, np.nan)

    def blocks(a):
        return a.reshape(H, K, W, K).swapaxes(1, 2)
    gfrac = blocks(ground).mean((2, 3)); wfrac = blocks(water).mean((2, 3))
    # Verbindung zweier Feinzellen: Höhenintervalle überlappen bis auf 'jump' yd
    def link(a0, a1, b0, b1):
        return (a0 <= b1 + jump) & (b0 <= a1 + jump)
    with np.errstate(invalid='ignore'):
        lr = link(zmin[:, :-1], zmax[:, :-1], zmin[:, 1:], zmax[:, 1:])   # Feinzelle -> rechts
        ud = link(zmin[:-1], zmax[:-1], zmin[1:], zmax[1:])               # Feinzelle -> unten
        dz_lr = np.abs(zmin[:, 1:] - zmin[:, :-1]); dz_ud = np.abs(zmin[1:] - zmin[:-1])
    both_lr = ground[:, :-1] & ground[:, 1:]; both_ud = ground[:-1] & ground[1:]
    # Neigung je Zelle: mittlerer Höhenunterschied verbundener Nachbarn / Feinzellenbreite
    sl = np.zeros((H * K, W * K)); n = np.zeros((H * K, W * K))
    v = both_lr & lr; sl[:, :-1] += np.where(v, dz_lr, 0); n[:, :-1] += v
    v = both_ud & ud; sl[:-1] += np.where(v, dz_ud, 0); n[:-1] += v
    slope = blocks(sl).sum((2, 3)) / np.maximum(blocks(n).sum((2, 3)), 1) / m['fine']
    # Kanten zwischen Zellen (Feinpaare über die Zellgrenze)
    cols = np.arange(K - 1, W * K - 1, K)           # letzte Feinspalte jeder Zelle (ohne letzte Zelle)
    e_both = both_lr[:, cols].reshape(H, K, W - 1).sum(1)
    e_ok = (both_lr & lr)[:, cols].reshape(H, K, W - 1).sum(1)
    rows_ = np.arange(K - 1, H * K - 1, K)
    s_both = both_ud[rows_, :].reshape(H - 1, W, K).sum(2)
    s_ok = (both_ud & ud)[rows_, :].reshape(H - 1, W, K).sum(2)
    return dict(gfrac=gfrac, wfrac=wfrac, slope=slope, e_both=e_both, e_ok=e_ok, s_both=s_both, s_ok=s_ok,
                ground=ground)


def reconnect(g, g0, min_size, max_len, log):
    """Teile, die im alten Raster mit dem Hauptgebiet verbunden waren, im neuen aber abgetrennt sind
    (Höhlengang/Tunnel schmaler als eine 20-yd-Zelle, Wasserstraße zur Insel, Aufzug), über den kürzesten Weg
    durch alt-begehbare Zellen wieder anschließen (alte Klasse; höchstens max_len Zellen neu)."""
    from collections import deque
    H, W = g.shape
    lab, n = ndi.label(g > 0, N8)
    if n < 2: return
    sizes = np.bincount(lab.ravel()); sizes[0] = 0
    main = int(np.argmax(sizes))
    lab0, _ = ndi.label(g0 > 0, N8)
    joined = 0; carved = 0
    order = [c for c in np.argsort(-sizes) if c != main and sizes[c] >= min_size]
    target = lab == main
    for c in order:
        cells = np.argwhere(lab == c)
        # nur Teile, die im alten Raster zur selben Fläche wie das Hauptgebiet gehörten
        old_ids = set(lab0[tuple(cells.T)].ravel()) - {0}
        main_old = set(np.unique(lab0[target])) - {0}
        if not (old_ids & main_old): continue
        prev = np.full(H * W, -1, np.int64); dist = np.full(H * W, -1, np.int32)
        q = deque()
        for r, cc in cells:
            i = r * W + cc; dist[i] = 0; q.append(i)
        hit = -1
        while q:
            i = q.popleft(); r, cc = divmod(i, W)
            if target[r, cc]: hit = i; break
            if dist[i] >= max_len: continue
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                rr, c2 = r + dr, cc + dc
                if 0 <= rr < H and 0 <= c2 < W:
                    j = rr * W + c2
                    if dist[j] < 0 and (g0[rr, c2] > 0 or g[rr, c2] > 0):
                        dist[j] = dist[i] + 1; prev[j] = i; q.append(j)
        if hit < 0: continue
        i = hit; k = 0
        while i >= 0 and dist[i] > 0:
            r, cc = divmod(i, W)
            if g[r, cc] == GESPERRT:
                g[r, cc] = g0[r, cc] if g0[r, cc] > 0 else BERG; k += 1
            i = prev[i]
        joined += 1; carved += k
        target |= lab == c
    log(f'    wieder angeschlossen: {joined} Teile, {carved} Zellen Gang (Tunnel/Wasserstraßen/Aufzüge)')


def terrain(a):
    navs = read_navdata(a.navdata)
    spawns = json.load(open(a.spawns))['spawns'] if a.spawns else {}
    log = lambda s: print(s, flush=True)
    stats = {}
    zone_union = {}
    if a.maps:
        import roads as RD
        from adt_roads import zone_masks
        maps = RD.Maps(a.db2, a.maps, a.tiles)
        for mid, zm in zone_masks(navs, maps).items():
            c = maps.zones[mid]['cont']
            zone_union[c] = zone_union.get(c, np.zeros_like(zm)) | zm
    for cont, nav in navs.items():
        log(f'Kontinent {cont}:')
        m = load_mesh(a.navmesh, cont)
        H, W = nav['h'], nav['w']
        legit = legit_components(m, spawns.get(str(cont), []), nav['XMAX'], nav['YMAX'], a.min_spawns, log)
        s = cell_stats(m, legit, H, W, a.sprung)
        g0 = nav['grid'].copy()
        covered = s['gfrac'] + s['wfrac'] > 0              # Navmesh vorhanden (sonst ADT-Lücke)
        tiles = ndi.binary_dilation(m['ground'][:H * m['K'], :W * m['K']].reshape(H, m['K'], W, m['K']).any((1, 3)) |
                                    m['water'][:H * m['K'], :W * m['K']].reshape(H, m['K'], W, m['K']).any((1, 3)),
                                    iterations=30)       # Bereich mit Navmesh-Kacheln (grob)
        g = np.full((H, W), GESPERRT, np.uint8)
        intended = zone_union.get(cont, np.ones((H, W), bool))   # aufgedeckte Zonenflächen (+3 Zellen)
        land = s['gfrac'] >= a.frei
        hang = (s['gfrac'] >= a.berg) & ~land
        steep = s['slope'] > a.steil
        flat_open = (s['gfrac'] >= 0.6) & (s['slope'] < a.flach)
        g[land] = FREI
        g[land & steep] = BERG
        g[land & (g0 == BERG)] = BERG                      # Kartenbild: Bergrücken -> teuer lassen
        newland = land & (g0 == GESPERRT)
        g[newland & ~flat_open] = BERG                     # bisher gesperrt, Navmesh begehbar: Hang -> Berg
        # wenig Boden: flach (Stege, Gassen, Tunnel, Pfade) -> frei, sonst Hang -> Berg
        g[hang & ~steep & (g0 != BERG)] = FREI
        g[hang & (steep | (g0 == BERG))] = BERG
        # Wasser: Navmesh-Wasser, das nicht schon Land ist; offenes Meer nur nahe Land / im Basis-Wasser
        wat = (s['wfrac'] >= 0.5) & (g == GESPERRT)
        near_land = ndi.binary_dilation(g > 0, N8, iterations=a.kueste)
        wat &= near_land | (g0 == WASSER) | (g0 == FREI)
        g[wat] = WASSER
        # außerhalb der Zonenflächen nichts neu öffnen (Kartenrand, unsichtbare Grenzen)
        g[~intended & (g0 == GESPERRT)] = GESPERRT
        # schmale Durchgänge: Zellen mit etwas Boden, die zwei freie Gebiete verbinden -> Berg
        thin = (s['gfrac'] > 0) & (g == GESPERRT) & (intended | (g0 > 0))
        if thin.any():
            fine_g = s['ground']
            sk = ndi.binary_opening(fine_g, N4)  # Rauschen weg
            # Skelett grob: Feinzellen mit Boden, deren 20-yd-Zelle wenig Boden hat, aber durchgehend
            K = m['K']
            run = sk.reshape(H, K, W, K)
            through_lr = run.any(1).sum(2) >= K - 1  # Boden quer durch (fast alle Feinspalten belegt)
            through_ud = run.any(3).sum(1) >= K - 1
            passage = thin & (through_lr | through_ud)
            g[passage] = BERG
        # Rauschen glätten (kleineres NavData, gleiche Wege): einzelne Berg-/Frei-Zellen in der anderen Klasse
        # umfärben, 1-Zellen-Löcher (Fels, Baumstamm) in begehbarer Fläche -> Berg (teuer, aber nicht gesperrt)
        if a.glatt:
            k8 = np.ones((3, 3)); k8[1, 1] = 0
            nf = ndi.convolve((g == FREI).astype(int), k8, mode='constant')
            nb = ndi.convolve((g == BERG).astype(int), k8, mode='constant')
            nw = ndi.convolve(((g == FREI) | (g == BERG)).astype(int), k8, mode='constant')
            n4 = ndi.convolve(((g == FREI) | (g == BERG)).astype(int), N4.astype(int) - _c4(), mode='constant')
            flipb = (g == BERG) & (nf >= 6); flipf = (g == FREI) & (nb >= 6)
            hole = (g == GESPERRT) & (nw >= 7) & (n4 == 4) & tiles
            g[flipb] = FREI; g[flipf] = BERG; g[hole] = BERG
            log(f'    geglättet: Berg->frei {int(flipb.sum())}, frei->Berg {int(flipf.sum())}, Löcher->Berg {int(hole.sum())}')
        # Klippen: Nachbarzellen mit Boden an der Kante, aber ohne Verbindung
        cliffs = 0
        e_cliff = (s['e_both'] >= 2) & (s['e_ok'] == 0)
        s_cliff = (s['s_both'] >= 2) & (s['s_ok'] == 0)
        gf = s['gfrac']
        for (rr, cc) in zip(*np.nonzero(e_cliff)):
            if g[rr, cc] in (FREI, BERG) and g[rr, cc + 1] in (FREI, BERG):
                t = (rr, cc) if gf[rr, cc] <= gf[rr, cc + 1] else (rr, cc + 1)
                g[t] = GESPERRT; cliffs += 1
        for (rr, cc) in zip(*np.nonzero(s_cliff)):
            if g[rr, cc] in (FREI, BERG) and g[rr + 1, cc] in (FREI, BERG):
                t = (rr, cc) if gf[rr, cc] <= gf[rr + 1, cc] else (rr + 1, cc)
                g[t] = GESPERRT; cliffs += 1
        # außerhalb des Navmesh-Bereichs: Basis behalten
        g = np.where(tiles, g, g0)
        reconnect(g, g0, a.min_teil, a.max_gang, log)
        # winzige Inseln (< 4 Zellen) ohne Wasser-Nachbarschaft weg
        lab, n = ndi.label(g > 0, N8)
        if n:
            size = np.bincount(lab.ravel())
            small = (size < 4); small[0] = False
            g[small[lab]] = GESPERRT
        nav['grid'] = g
        names = {GESPERRT: 'gesperrt', FREI: 'frei', BERG: 'Berg', WASSER: 'Wasser'}
        ch = {f'{names[x]}->{names[y]}': int(((g0 == x) & (g == y)).sum()) for x in names for y in names if x != y}
        log(f'    Klippen gesperrt: {cliffs}; Änderungen: ' + ', '.join(f'{k} {v}' for k, v in ch.items() if v))
        log('    vorher ' + ', '.join(f'{names[k]} {int((g0 == k).sum())}' for k in names) +
            ' | nachher ' + ', '.join(f'{names[k]} {int((g == k).sum())}' for k in names))
        stats[cont] = ch
    write_navdata(a.out, navs, recompute_comp=True)
    if a.stats:
        json.dump(stats, open(a.stats, 'w'), indent=1)


# ------------------------------------------------------------------ Texturstraßen übernehmen
def overlay_roads(a):
    """Straßenzellen (e) des bisherigen Laufs (adt_roads.py auf dem Kartenbild-Raster, Texturfilter wie
    kuratiert) auf das Navmesh-Gelände legen. adt_roads.py direkt auf dem Navmesh-Raster würde die
    Filter 'weich'/'bergabstand'/'wasserabstand' anders auswerten (andere Berg-/Wasserzellen) und echte
    Straßen verlieren. Verworfen werden nur Straßenzellen ohne jeden Navmesh-Boden im 3x3-Umfeld
    (Textur unter Gebäuden/über Abgründen), außer sie stammen aus Bauwerken (Brücken/Tunnel)."""
    T = read_navdata(a.terrain); Rd = read_navdata(a.roads)
    structs = json.load(open(a.structs)) if a.structs and os.path.exists(a.structs) else {}
    log = lambda s: print(s, flush=True)
    for cont, nav in T.items():
        g = nav['grid']; road = Rd[cont]['grid'] == STRASSE
        m = load_mesh(a.navmesh, cont); K = m['K']; H, W = g.shape
        anyg = (m['ground'][:H * K, :W * K] > 0).reshape(H, K, W, K).any((1, 3))
        anyg = ndi.binary_dilation(anyg, N8)
        st = np.zeros((H, W), bool)
        for s_ in structs.get(str(cont), []):
            for r, c in s_['zellen']:
                if 0 <= r < H and 0 <= c < W: st[r, c] = True
        keep = road & (anyg | st)
        over_blocked = keep & (g == GESPERRT)
        g[keep] = STRASSE
        log(f'Kontinent {cont}: {int(road.sum())} Straßenzellen, übernommen {int(keep.sum())} '
            f'(davon {int(over_blocked.sum())} über Navmesh-gesperrten Zellen: Hangstraßen/Klippenkanten), '
            f'verworfen {int((road & ~keep).sum())} ohne Navmesh-Boden')
    write_navdata(a.out, T, recompute_comp=True)


# ------------------------------------------------------------------ NPC-Wege
def raster_line(r0, c0, r1, c1):
    n = int(max(abs(r1 - r0), abs(c1 - c0)) * 2) + 1
    t = np.linspace(0, 1, n)
    return np.round(r0 + (r1 - r0) * t).astype(int), np.round(c0 + (c1 - c0) * t).astype(int)


def four_connect(R, g):
    """Diagonale Schritte schließen (A* schneidet keine Ecken): Eckzelle mit besserer Klasse dazu."""
    R = R.copy()
    pref = np.where(g == FREI, 3, np.where(g == STRASSE, 4, np.where(g == BERG, 2, np.where(g == WASSER, 1, 0))))
    H, W = R.shape
    rs, cs = np.nonzero(R)
    S = set(zip(rs.tolist(), cs.tolist()))
    for r, c in list(S):
        for dr, dc in ((1, 1), (1, -1)):
            q = (r + dr, c + dc)
            if q in S and (r + dr, c) not in S and (r, c + dc) not in S:
                o1, o2 = (r + dr, c), (r, c + dc)
                p1 = pref[o1] if 0 <= o1[0] < H and 0 <= o1[1] < W else 0
                p2 = pref[o2] if 0 <= o2[0] < H and 0 <= o2[1] < W else 0
                t = o1 if p1 >= p2 else o2
                if pref[t] > 0:
                    R[t] = True; S.add(t)
    return R


def npcroads(a):
    navs = read_navdata(a.navdata)
    data = {'paths': [q for f in a.paths for q in json.load(open(f))['paths']]}
    log = lambda s: print(s, flush=True)
    report = {}
    for cont, nav in navs.items():
        m = load_mesh(a.navmesh, cont)
        g = nav['grid']; H, W = g.shape; K = m['K']; f = m['fine']
        ground = m['ground'] > 0
        groundd = ndi.binary_dilation(ground, iterations=2)      # 8 yd Toleranz (Wegpunkte-Rundung)
        paths = [p for p in data['paths'] if p['map'] == cont]
        votes = {}            # Zelle -> Menge NPC-IDs
        used = 0; rejected = 0; hostile = 0; offroad = 0
        road = g == STRASSE
        roadd = ndi.binary_dilation(road, N8, iterations=1)
        for p in paths:
            P = np.array(p['pts'], float)
            ext = float(np.hypot(*(P.max(0) - P.min(0))))
            # nur freundliche NPCs (Wachen, Boten, Karawanen, Eskorten) – feindliche Patrouillen laufen
            # meist Kreise durchs Gelände; kurze Runden (Stadtschreier, Wache am Tor) zählen nicht
            if a.nur_freundlich and not p.get('fr'):
                hostile += 1; continue
            if ext < a.min_ausdehnung:
                rejected += 1; continue
            fr = ((nav['XMAX'] - P[:, 0]) / f).astype(int); fc = ((nav['YMAX'] - P[:, 1]) / f).astype(int)
            ok = (fr >= 0) & (fr < ground.shape[0]) & (fc >= 0) & (fc < ground.shape[1])
            on = np.zeros(len(P), bool); on[ok] = groundd[fr[ok], fc[ok]]
            if on.mean() < a.boden:
                rejected += 1; continue
            cr = (nav['XMAX'] - P[:, 0]) / 20; cc = (nav['YMAX'] - P[:, 1]) / 20
            ri, ci = cr.astype(int), cc.astype(int)
            okc = (ri >= 0) & (ri < H) & (ci >= 0) & (ci < W)
            share = roadd[ri[okc], ci[okc]].mean() if okc.any() else 0
            # Weg muss zu >= --min-strasse auf Texturstraßen liegen (Wachen/Boten auf Straßen); Eskorten
            # und Querfeldein-Runden fallen weg – deren Stücke zählen nur, wenn ein zweiter NPC sie bestätigt
            solo = share < a.min_strasse
            if solo: offroad += 1
            used += 1
            for i in range(len(P) - 1):
                seg = np.hypot(P[i + 1, 0] - P[i, 0], P[i + 1, 1] - P[i, 1])
                if seg > a.max_sprung or not (on[i] and on[i + 1]):
                    continue        # Teleport/Lücke im Weg
                rr, ccs = raster_line(cr[i], cc[i], cr[i + 1], cc[i + 1])
                for r, c in zip(rr, ccs):
                    if 0 <= r < H and 0 <= c < W:
                        votes.setdefault((r, c), [set(), False])
                        votes[(r, c)][0].add(p['npc'])
                        votes[(r, c)][1] |= not solo
        V = np.zeros((H, W), np.int32)
        for (r, c), (s_, good) in votes.items():
            V[r, c] = len(s_) if (good or len(s_) >= 2) else 0
        cand = V > 0
        # zusammenhängende Wegstücke abseits der Texturstraßen: nur ab --min-laenge yd Ausdehnung
        off = cand & ~ndi.binary_dilation(road, N8, iterations=1)
        lab, n = ndi.label(off, N8)
        keep = np.zeros(n + 1, bool)
        for i, sl in enumerate(ndi.find_objects(lab)):
            if sl is None: continue
            msk = lab[sl] == i + 1
            ext = max(sl[0].stop - sl[0].start, sl[1].stop - sl[1].start) * 20
            touch = (ndi.binary_dilation(msk, N8, iterations=2) & road[sl]).any()
            multi = (V[sl][msk] >= 2).mean()
            keep[i + 1] = ext >= a.min_laenge and (touch or multi >= 0.5)
        R = keep[lab] & ((g == FREI) | (g == BERG))
        R = four_connect(R | road, g) & ((g == FREI) | (g == BERG) | road)
        new = R & ~road
        g[new] = STRASSE
        log(f'Kontinent {cont}: {len(paths)} NPC-Wege, {used} benutzt ({offroad} davon abseits der Straßen, nur bestätigt), {hostile} feindlich, {rejected} verworfen (kurz/Flug/Schwimm); '
            f'{int(cand.sum())} belegte Zellen, {int(new.sum())} neue Straßenzellen, '
            f'{int((cand & road).sum())} bestätigen Texturstraßen')
        report[cont] = dict(paths=len(paths), used=used, hostile=hostile, rejected=rejected, cells=int(cand.sum()),
                            new=int(new.sum()), agree=int((cand & road).sum()))
        if a.mask_out:
            np.save(a.mask_out.replace('.npy', f'_{cont}.npy'), np.stack([V, new.astype(np.int32)]))
    write_navdata(a.out, navs, recompute_comp=True)
    if a.stats:
        json.dump(report, open(a.stats, 'w'), indent=1)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('terrain')
    t.add_argument('--navdata', required=True); t.add_argument('--navmesh', required=True)
    t.add_argument('--spawns'); t.add_argument('--out', required=True); t.add_argument('--stats')
    t.add_argument('--frei', type=float, default=0.3); t.add_argument('--berg', type=float, default=0.12)
    t.add_argument('--steil', type=float, default=0.7, help='mittlere Neigung (dz/dx) ab der Berg gilt')
    t.add_argument('--sprung', type=float, default=5.0); t.add_argument('--kueste', type=int, default=3)
    t.add_argument('--min-spawns', type=int, default=3); t.add_argument('--flach', type=float, default=0.45)
    t.add_argument('--db2'); t.add_argument('--maps'); t.add_argument('--tiles', default='')
    t.add_argument('--roh', dest='glatt', action='store_false', help='kein Glätten')
    t.add_argument('--min-teil', type=int, default=40, help='abgetrennte Teile ab so vielen Zellen wieder anschließen')
    t.add_argument('--max-gang', type=int, default=120, help='höchstens so viele Zellen Gang je Teil')
    o = sub.add_parser('roads')
    o.add_argument('--terrain', required=True); o.add_argument('--roads', required=True)
    o.add_argument('--navmesh', required=True); o.add_argument('--structs'); o.add_argument('--out', required=True)
    n = sub.add_parser('npcroads')
    n.add_argument('--navdata', required=True); n.add_argument('--navmesh', required=True)
    n.add_argument('--paths', required=True, nargs='+'); n.add_argument('--out', required=True); n.add_argument('--stats')
    n.add_argument('--boden', type=float, default=0.9); n.add_argument('--max-sprung', type=float, default=80)
    n.add_argument('--min-laenge', type=float, default=100); n.add_argument('--mask-out')
    n.add_argument('--min-ausdehnung', type=float, default=150, help='Mindestausdehnung eines NPC-Wegs (yd)')
    n.add_argument('--min-strasse', type=float, default=0.3, help='Mindestanteil eines NPC-Wegs auf Texturstraßen')
    n.add_argument('--alle', dest='nur_freundlich', action='store_false', help='auch feindliche NPC-Patrouillen')
    a = ap.parse_args()
    {'terrain': terrain, 'roads': overlay_roads, 'npcroads': npcroads}[a.cmd](a)


if __name__ == '__main__':
    main()
