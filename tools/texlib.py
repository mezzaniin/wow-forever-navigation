"""Gemeinsame Helfer: Texturgewichte (texstore_<map>.npz aus adt_texstore.py) auf beliebige Weltpunkte abbilden."""
import os
import numpy as np

TILE = 1600.0 / 3.0
CHUNK = TILE / 16
ORIGIN = 32 * TILE
SUB = 16
STEP = CHUNK / SUB          # 2,083 yd


class TexStore:
    def __init__(self, adtdir, m):
        z = np.load(os.path.join(adtdir, f'texstore_{m}.npz'))
        self.chunk, self.fdid, self.w = z['chunk'], z['fdid'], z['w']
        o = np.lexsort((self.chunk, self.fdid))
        self.chunk, self.fdid, self.w = self.chunk[o], self.fdid[o], self.w[o]
        self.fd_unique, self.fd_start = np.unique(self.fdid, return_index=True)
        self.fd_end = np.append(self.fd_start[1:], len(self.fdid))

    def textures(self):
        return list(self.fd_unique)

    def rows_of(self, fd):
        i = np.searchsorted(self.fd_unique, fd)
        if i >= len(self.fd_unique) or self.fd_unique[i] != fd: return slice(0, 0)
        return slice(self.fd_start[i], self.fd_end[i])

    def sample(self, fds, wx, wy):
        """Summe der Gewichte (0..1) der Texturen fds an den Weltpunkten (wx, wy)."""
        gr = np.floor((ORIGIN - wx) / STEP).astype(np.int64); gc = np.floor((ORIGIN - wy) / STEP).astype(np.int64)
        ch = (gr // SUB) * 1024 + gc // SUB; sy = gr % SUB; sx = gc % SUB
        out = np.zeros(np.shape(wx), np.float32)
        for fd in np.atleast_1d(fds):
            s = self.rows_of(int(fd)); cs = self.chunk[s]
            if not len(cs): continue
            i = np.searchsorted(cs, ch); i = np.clip(i, 0, len(cs) - 1)
            hit = cs[i] == ch
            if hit.any():
                out[hit] += self.w[s][i[hit], sy[hit], sx[hit]].astype(np.float32) / 255
        return out

    def present(self, wx, wy):
        """Texturen, die an den Punkten vorkommen (FileDataIDs) mit Anzahl Chunks."""
        gr = np.floor((ORIGIN - wx) / STEP).astype(np.int64); gc = np.floor((ORIGIN - wy) / STEP).astype(np.int64)
        ch = np.unique((gr // SUB) * 1024 + gc // SUB)
        m = np.isin(self.chunk, ch)
        f, n = np.unique(self.fdid[m], return_counts=True)
        return dict(zip(f.tolist(), n.tolist()))
