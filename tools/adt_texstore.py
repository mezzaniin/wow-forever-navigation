"""Alle Geländetextur-Gewichte der ADT-Kacheln in einen kompakten Speicher schreiben (Analyse + Straßenbau).

Je MCNK-Chunk und Ebene: sichtbares Gewicht der Textur auf 16x16 Stützstellen (2,08 yd), uint8 0..255.
Blending wie im aktuellen Client (MPHD 0x80, Alphawerte summieren sich zu <= 1): Ebene k>=1 hat Gewicht
a_k, Grundebene 1 - Summe(a_k). (Nicht das alte nacheinander-Überblenden.)

Ergebnis work/adt/texstore_<map>.npz:
  chunk  int32[n]   globaler Chunk-Index (Zeile*1024 + Spalte; Zeile = ty*16+cy, Spalte = tx*16+cx)
  fdid   int32[n]   Textur-FileDataID
  w      uint8[n,16,16]
Weltkoordinaten einer Stützstelle (gr, gc = Chunkzeile*16+sy, Chunkspalte*16+sx):
  wx = ORIGIN - (gr + 0.5) * CHUNK/16 ; wy = ORIGIN - (gc + 0.5) * CHUNK/16
"""
import argparse, json, os, struct, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adt_fetch import chunks  # noqa: E402

SUB = 16


def decode_alpha(mcal, flags, off):
    if not flags & 0x100: return None
    if flags & 0x200:
        a = bytearray(); i = off
        while len(a) < 4096 and i < len(mcal):
            b = mcal[i]; i += 1; n = b & 0x7f
            if b & 0x80: a += bytes([mcal[i]]) * n; i += 1
            else: a += mcal[i:i + n]; i += n
        a = np.frombuffer(bytes(a[:4096]).ljust(4096, b'\0'), np.uint8)
    else:
        a = np.frombuffer(mcal[off:off + 4096].ljust(4096, b'\0'), np.uint8)
    return a.reshape(64, 64).astype(np.float32) / 255


def tile_weights(path):
    """[(ci, fdid, w16x16 float)] für alle Chunks/Ebenen einer tex0-Datei."""
    b = open(path, 'rb').read()
    ids = (); out = []; ci = 0
    for tag, d in chunks(b):
        if tag == 'MDID': ids = struct.unpack_from(f'<{len(d) // 4}I', d)
        elif tag == 'MCNK':
            layers, mcal = [], b''
            for tg, dd in chunks(d):
                if tg == 'MCLY': layers = [struct.unpack_from('<4I', dd, j * 16) for j in range(len(dd) // 16)]
                elif tg == 'MCAL': mcal = dd
            if layers:
                al = [decode_alpha(mcal, fl, off) for _, fl, off, _ in layers[1:]]
                al = [a if a is not None else np.zeros((64, 64), np.float32) for a in al]
                s = np.zeros((64, 64), np.float32)
                for a in al: s += a
                ws = [np.clip(1 - s, 0, 1)] + al
                # Summe > 1 (sollte nicht vorkommen) -> normieren
                tot = np.maximum(sum(ws), 1e-6)
                for (tex, *_), w in zip(layers, ws):
                    fd = ids[tex] if tex < len(ids) else 0
                    w16 = (w / tot).reshape(SUB, 64 // SUB, SUB, 64 // SUB).mean(axis=(1, 3))
                    if w16.max() > 0.01: out.append((ci, fd, w16))
            ci += 1
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--adt', default='work/adt'); ap.add_argument('--maps', default='0,1')
    a = ap.parse_args()
    for m in map(int, a.maps.split(',')):
        d = os.path.join(a.adt, str(m)); tiles = json.load(open(os.path.join(d, 'tiles.json')))
        C, F, W = [], [], []
        for k, (key, t) in enumerate(tiles.items()):
            p = os.path.join(d, f"{t.get('tex0')}.adt")
            if not t.get('tex0') or not os.path.exists(p): continue
            tx, ty = map(int, key.split('_'))
            for ci, fd, w in tile_weights(p):
                cy, cx = divmod(ci, 16)
                C.append((ty * 16 + cy) * 1024 + tx * 16 + cx); F.append(fd); W.append(np.round(w * 255).astype(np.uint8))
            if k % 100 == 0: print(m, k, len(tiles), flush=True)
        np.savez_compressed(os.path.join(a.adt, f'texstore_{m}.npz'), chunk=np.array(C, np.int32), fdid=np.array(F, np.int32), w=np.array(W, np.uint8))
        print(f'Karte {m}: {len(C)} Ebenen-Chunks', flush=True)


if __name__ == '__main__':
    main()
