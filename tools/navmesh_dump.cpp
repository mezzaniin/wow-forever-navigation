// Lädt alle TrinityCore-mmaps-Kacheln einer Karte in ein dtNavMesh (verknüpft Kachelgrenzen),
// bestimmt zusammenhängende Bereiche per Flutfüllung über die Detour-Links und schreibt alle
// Boden-Polygone in Weltkoordinaten:
//   Datei: "NMD2" u32 triCount, je Dreieck (Fächer aus dem konvexen Polygon, 44 Byte):
//          u32 comp, u8 area, 3 Byte frei, 3 * float[3] (X,Y,Z Welt)
// Bau (im tcbuild-Container): g++ -O2 navmesh_dump.cpp -I<TC>/dep/recastnavigation/Detour/Include libDetour.a
// Aufruf: navmesh_dump <mmaps-Ordner> <mapId> <ausgabe.bin>
#include "DetourNavMesh.h"
#include "DetourCommon.h"
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <string>
#include <vector>
#include <deque>
#include <unordered_map>
#include <dirent.h>

struct MmapNavMeshHeader { uint32_t magic, version; dtNavMeshParams params; uint32_t offmesh; };
struct MmapTileHeader { uint32_t magic, dtVersion, mmapVersion, size; char usesLiquids; char pad[3]; };

int main(int argc, char** argv)
{
    if (argc < 4) { fprintf(stderr, "navmesh_dump <mmaps> <mapId> <out>\n"); return 1; }
    std::string dir = argv[1]; int mapId = atoi(argv[2]);
    char name[64]; snprintf(name, sizeof name, "%04d", mapId);
    FILE* f = fopen((dir + "/" + name + ".mmap").c_str(), "rb");
    if (!f) { fprintf(stderr, "keine .mmap\n"); return 2; }
    MmapNavMeshHeader mh; if (fread(&mh, sizeof mh, 1, f) != 1) return 2; fclose(f);
    mh.params.maxTiles = 64 * 64 * 4;  // genug Platz
    dtNavMesh* nav = dtAllocNavMesh();
    if (dtStatusFailed(nav->init(&mh.params))) { fprintf(stderr, "init fehlgeschlagen\n"); return 3; }
    DIR* d = opendir(dir.c_str()); dirent* e; int tiles = 0;
    std::string pre = std::string(name) + "_";
    while ((e = readdir(d)))
    {
        std::string fn = e->d_name;
        if (fn.rfind(pre, 0) != 0 || fn.find(".mmtile") == std::string::npos) continue;
        FILE* t = fopen((dir + "/" + fn).c_str(), "rb");
        MmapTileHeader th; if (fread(&th, sizeof th, 1, t) != 1) { fclose(t); continue; }
        unsigned char* data = (unsigned char*)dtAlloc(th.size, DT_ALLOC_PERM);
        if (fread(data, th.size, 1, t) != 1) { fclose(t); dtFree(data); continue; }
        fclose(t);
        if (dtStatusFailed(nav->addTile(data, th.size, DT_TILE_FREE_DATA, 0, nullptr))) { dtFree(data); fprintf(stderr, "addTile %s fehlgeschlagen\n", fn.c_str()); continue; }
        ++tiles;
    }
    closedir(d);
    fprintf(stderr, "Karte %d: %d Kacheln geladen\n", mapId, tiles);
    const dtNavMesh* cn = nav;
    // Flutfüllung
    std::unordered_map<dtPolyRef, uint32_t> comp; uint32_t ncomp = 0; size_t npolys = 0;
    for (int i = 0; i < nav->getMaxTiles(); ++i)
    {
        const dtMeshTile* tile = cn->getTile(i);
        if (!tile || !tile->header) continue;
        dtPolyRef base = nav->getPolyRefBase(tile);
        for (int p = 0; p < tile->header->polyCount; ++p)
        {
            if (tile->polys[p].getType() != DT_POLYTYPE_GROUND) continue;
            ++npolys;
            dtPolyRef start = base | (dtPolyRef)p;
            if (comp.count(start)) continue;
            uint32_t c = ++ncomp; std::deque<dtPolyRef> q; q.push_back(start); comp[start] = c;
            while (!q.empty())
            {
                dtPolyRef r = q.front(); q.pop_front();
                const dtMeshTile* tt; const dtPoly* pp; cn->getTileAndPolyByRefUnsafe(r, &tt, &pp);
                for (unsigned int k = pp->firstLink; k != DT_NULL_LINK; k = tt->links[k].next)
                {
                    dtPolyRef n = tt->links[k].ref;
                    if (n && !comp.count(n)) { comp[n] = c; q.push_back(n); }
                }
            }
        }
    }
    fprintf(stderr, "%zu Polygone, %u Bereiche\n", npolys, ncomp);
    std::vector<unsigned char> buf; uint32_t ntri = 0;
    for (int i = 0; i < nav->getMaxTiles(); ++i)
    {
        const dtMeshTile* tile = cn->getTile(i);
        if (!tile || !tile->header) continue;
        dtPolyRef base = nav->getPolyRefBase(tile);
        for (int p = 0; p < tile->header->polyCount; ++p)
        {
            const dtPoly& poly = tile->polys[p];
            if (poly.getType() != DT_POLYTYPE_GROUND) continue;
            uint32_t c = comp[base | (dtPolyRef)p]; uint8_t area = poly.getArea();
            float w[DT_VERTS_PER_POLYGON][3];
            for (int v = 0; v < poly.vertCount; ++v)
            {
                const float* rv = &tile->verts[poly.verts[v] * 3];
                w[v][0] = rv[2]; w[v][1] = rv[0]; w[v][2] = rv[1];  // Recast (y,z,x) -> Welt (x,y,z)
            }
            for (int v = 1; v + 1 < poly.vertCount; ++v)
            {
                unsigned char rec[44] = {};
                memcpy(rec, &c, 4); rec[4] = area;
                memcpy(rec + 8, w[0], 12); memcpy(rec + 20, w[v], 12); memcpy(rec + 32, w[v + 1], 12);
                buf.insert(buf.end(), rec, rec + 44); ++ntri;
            }
        }
    }
    FILE* o = fopen(argv[3], "wb"); fwrite("NMD2", 1, 4, o); fwrite(&ntri, 4, 1, o);
    fwrite(buf.data(), 1, buf.size(), o);
    fclose(o);
    return 0;
}
