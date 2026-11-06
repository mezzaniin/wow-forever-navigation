"""ADT-Dateien (Geländetexturen _tex0, Objekte _obj0) des Forever-Clients laden und cachen.

Quelle: wago.tools CASC-API (product wowf, Build siehe --version), FileDataIDs aus dem MAID-Chunk der WDT.
Alternativ (--casc DIR): bereits mit tools/extract (CascLib) aus der lokalen Installation ausgelesene Dateien.

  python adt_fetch.py --out work/adt [--version 1.60.1.70790] [--jobs 6]
Ergebnis: work/adt/<map>/<fdid>.adt + work/adt/<map>/tiles.json  {"x_y": {"tex0": fdid, "obj0": fdid, "root": fdid}}
"""
import argparse, concurrent.futures as cf, json, os, struct, sys, time, urllib.request

WDT = {0: 775971, 1: 782779}        # world/maps/azeroth/azeroth.wdt, world/maps/kalimdor/kalimdor.wdt
UA = 'wow-forever-navigation/1.0 (RoutePlaner; github mezzaniin)'


def fetch(url, dest, tries=5):
    if os.path.exists(dest) and os.path.getsize(dest) > 0: return 'cache'
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=120) as r: data = r.read()
            tmp = dest + '.part'
            with open(tmp, 'wb') as f: f.write(data)
            os.replace(tmp, dest)
            return 'ok'
        except Exception as e:  # noqa
            err = e; time.sleep(2 * (i + 1))
    return f'fehler {err}'


def chunks(b):
    i = 0
    while i + 8 <= len(b):
        tag = b[i:i + 4][::-1].decode('latin1'); n = struct.unpack_from('<I', b, i + 4)[0]
        yield tag, b[i + 8:i + 8 + n]
        i += 8 + n


def maid(wdt):
    for tag, d in chunks(wdt):
        if tag == 'MAID':
            out = {}
            for i in range(4096):
                root, obj0, obj1, tex0, lod, mt, mtn, mm = struct.unpack_from('<8I', d, i * 32)
                if root or tex0: out[f'{i % 64}_{i // 64}'] = dict(root=root, obj0=obj0, tex0=tex0)
            return out
    raise SystemExit('WDT ohne MAID')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='work/adt'); ap.add_argument('--version', default='1.60.1.70790')
    ap.add_argument('--jobs', type=int, default=6); ap.add_argument('--maps', default='0,1')
    ap.add_argument('--kinds', default='tex0,obj0')
    a = ap.parse_args()
    base = f'https://wago.tools/api/casc/{{}}?version={a.version}'
    for m in map(int, a.maps.split(',')):
        d = os.path.join(a.out, str(m)); os.makedirs(d, exist_ok=True)
        wp = os.path.join(d, 'map.wdt'); fetch(base.format(WDT[m]), wp)
        tiles = maid(open(wp, 'rb').read())
        json.dump(tiles, open(os.path.join(d, 'tiles.json'), 'w'), indent=0)
        jobs = [(t[k], os.path.join(d, f'{t[k]}.adt')) for t in tiles.values() for k in a.kinds.split(',') if t.get(k)]
        print(f'Karte {m}: {len(tiles)} Kacheln, {len(jobs)} Dateien', flush=True)
        done = fail = 0
        with cf.ThreadPoolExecutor(a.jobs) as ex:
            for res in ex.map(lambda j: fetch(base.format(j[0]), j[1]), jobs):
                done += 1
                if res.startswith('fehler'): fail += 1; print(res, file=sys.stderr)
                if done % 100 == 0: print(f'  {done}/{len(jobs)} ({fail} Fehler)', flush=True)
        print(f'Karte {m} fertig: {done - fail}/{len(jobs)}', flush=True)


if __name__ == '__main__':
    main()
