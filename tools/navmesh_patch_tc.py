#!/usr/bin/env python3
"""Patcht TrinityCore-master-Extraktoren (map/vmap/mmaps) für WoW Forever (product wowf).

Problem: TC master liest Map/LiquidType/LiquidMaterial/LiquidObject/GameObjectDisplayInfo.db2 mit
fest einkompilierten Retail-Layouts (LayoutHash-Prüfung) -> Forever-DB2 (Classic-Layout) scheitert.
Lösung: die paar benötigten Tabellen kommen als C++-Header aus den wago.tools-CSV (product=wowf);
nur die Karten aus --maps (Standard 0,1) werden eingetragen, GameObject-Modelle werden übersprungen.

Benutzung: navmesh_patch_tc.py <TrinityCore-Quellordner> <csv-Ordner> [--maps 0,1]
  csv-Ordner braucht Map.csv LiquidMaterial.csv LiquidObject.csv LiquidType.csv
  (https://wago.tools/db2/<Tabelle>/csv?product=wowf)
"""
import argparse, csv, os, re, sys


def rows(d, name):
    return list(csv.DictReader(open(os.path.join(d, name + '.csv'), encoding='utf-8')))


def s32(v):
    v = int(v) & 0xffffffff
    return v - (1 << 32) if v > 0x7fffffff else v


def cstr(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def header(csvdir, maps):
    out = ['#pragma once', '// erzeugt von tools/navmesh_patch_tc.py aus wago.tools-CSV (product wowf)',
           '#include "Define.h"',
           'struct ForeverMapRow { uint32 Id; int32 Wdt; int16 Parent; uint8 MapType; uint8 InstanceType; int32 Flags1; char const* Name; char const* Dir; };',
           'static ForeverMapRow const ForeverMaps[] = {']
    for r in rows(csvdir, 'Map'):
        if r['ID'] not in maps or not int(r['WdtFileDataID'] or 0):
            continue
        p = int(r['ParentMapID'])
        if p < 0:
            p = int(r['CosmeticParentMapID'])
        out.append(f"  {{ {r['ID']}, {r['WdtFileDataID']}, {p}, {r['MapType']}, {r['InstanceType']}, {s32(r['Flags_0'])}, {cstr(r['MapName_lang'])}, {cstr(r['Directory'])} }},")
    out.append('};')
    out.append('struct ForeverLiquidMaterialRow { uint32 Id; uint32 Flags; uint8 LVF; };')
    out.append('static ForeverLiquidMaterialRow const ForeverLiquidMaterials[] = {')
    out += [f"  {{ {r['ID']}, {int(r['Flags']) & 0xffffffff}u, {r['LVF']} }}," for r in rows(csvdir, 'LiquidMaterial')]
    out.append('};')
    out.append('struct ForeverLiquidObjectRow { uint32 Id; uint16 LiquidTypeID; };')
    out.append('static ForeverLiquidObjectRow const ForeverLiquidObjects[] = {')
    out += [f"  {{ {r['ID']}, {r['LiquidTypeID']} }}," for r in rows(csvdir, 'LiquidObject')]
    out.append('};')
    out.append('struct ForeverLiquidTypeRow { uint32 Id; uint8 SoundBank; uint8 MaterialID; };')
    out.append('static ForeverLiquidTypeRow const ForeverLiquidTypes[] = {')
    out += [f"  {{ {r['ID']}, {r['SoundBank']}, {r['MaterialID']} }}," for r in rows(csvdir, 'LiquidType')]
    out.append('};')
    return '\n'.join(out) + '\n'


def replace_body(src, signature, body):
    i = src.find(signature)
    if i < 0:
        raise SystemExit(f'Signatur nicht gefunden: {signature}')
    j = src.index('{', i)
    depth, k = 0, j
    while True:
        if src[k] == '{':
            depth += 1
        elif src[k] == '}':
            depth -= 1
            if depth == 0:
                break
        k += 1
    return src[:j] + '{\n' + body + '\n}' + src[k + 1:]


MAP_SYS = '''    printf("Read Map table (Forever, einkompiliert)...\\n");
    for (ForeverMapRow const& r : ForeverMaps)
    {
        MapEntry map;
        map.Id = r.Id; map.WdtFileDataId = r.Wdt; map.Name = r.Name; map.Directory = r.Dir;
        map_ids.push_back(map);
    }
    printf("Done! (" SZFMTD " maps loaded)\\n", map_ids.size());'''

LMAT = '''    for (ForeverLiquidMaterialRow const& r : ForeverLiquidMaterials)
    {
        LiquidMaterials[r.Id].Flags = static_cast<LiquidMaterialFlags>(r.Flags);
        LiquidMaterials[r.Id].LVF = r.LVF;
    }
    printf("Done! (" SZFMTD " LiquidMaterials loaded)\\n", LiquidMaterials.size());'''

LOBJ = '''    for (ForeverLiquidObjectRow const& r : ForeverLiquidObjects)
        LiquidObjects[r.Id].LiquidTypeID = r.LiquidTypeID;
    printf("Done! (" SZFMTD " LiquidObjects loaded)\\n", LiquidObjects.size());'''

LTYPE = '''    for (ForeverLiquidTypeRow const& r : ForeverLiquidTypes)
    {
        LiquidTypes[r.Id].SoundBank = r.SoundBank;
        LiquidTypes[r.Id].MaterialID = r.MaterialID;
    }
    printf("Done! (" SZFMTD " LiquidTypes loaded)\\n", LiquidTypes.size());'''

MAP_VMAP = '''    printf("Read Map table (Forever, einkompiliert)... ");
    for (ForeverMapRow const& r : ForeverMaps)
    {
        MapEntry& map = map_ids.emplace_back();
        map.Id = r.Id; map.WdtFileDataId = r.Wdt; map.ParentMapID = r.Parent; map.Name = r.Name; map.Directory = r.Dir;
    }
    printf("Done! (" SZFMTD " maps loaded)\\n", map_ids.size());'''

GO_SKIP = '''    printf("GameObject-Modelle übersprungen (Forever-Patch)\\n");
    std::string p = std::string(szWorkDirWmo) + "/temp_gameobject_models";
    if (FILE* f = fopen(p.c_str(), "wb")) { fwrite(VMAP::RAW_VMAP_MAGIC, 1, 8, f); fclose(f); }'''

LIQ_MMAP = '''    std::unordered_map<uint32, uint8> liquidData;
    for (ForeverLiquidTypeRow const& r : ForeverLiquidTypes)
        liquidData[r.Id] = r.SoundBank;
    return liquidData;'''

MAP_MMAP = '''    for (ForeverMapRow const& r : ForeverMaps)
    {
        MMAP::MapEntry& map = MMAP::sMapStore[r.Id];
        map.MapType = r.MapType; map.InstanceType = r.InstanceType; map.ParentMapID = r.Parent; map.Flags = r.Flags1;
    }'''


def patch_file(path, edits, inc='#include "ForeverTables.h"'):
    s = open(path, encoding='utf-8').read()
    if 'ForeverTables.h' in s:
        print('schon gepatcht:', path)
        return
    first = min(s.find(sig) for sig, _ in edits)
    for sig, body in edits:
        s = replace_body(s, sig, body)
    # Include nach dem letzten #include vor der ersten ersetzten Funktion
    m = [x for x in re.finditer(r'^#include .*$', s, re.M) if x.start() < first and '.txt' not in x.group(0)]
    pos = m[-1].end()
    s = s[:pos] + '\n' + inc + s[pos:]
    open(path, 'w', encoding='utf-8').write(s)
    print('gepatcht:', path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('tc'); ap.add_argument('csv'); ap.add_argument('--maps', default='0,1')
    a = ap.parse_args()
    maps = set(a.maps.split(','))
    h = header(a.csv, maps)
    t = os.path.join(a.tc, 'src', 'tools')
    for sub in ('map_extractor', 'vmap4_extractor', 'mmaps_generator'):
        open(os.path.join(t, sub, 'ForeverTables.h'), 'w').write(h)
    # CascLib öffnet lokale Dateien O_RDWR -> scheitert an schreibgeschützter WoW-Installation (EROFS)
    fs = os.path.join(a.tc, 'dep', 'CascLib', 'src', 'common', 'FileStream.cpp')
    src = open(fs).read()
    src = src.replace('int oflag = (dwStreamFlags & STREAM_FLAG_READ_ONLY) ? O_RDONLY : O_RDWR;', 'int oflag = O_RDONLY; // Forever-Patch: Installation nur lesen')
    open(fs, 'w').write(src)
    patch_file(os.path.join(t, 'map_extractor', 'System.cpp'), [
        ('void ReadMapDBC()', MAP_SYS), ('void ReadLiquidMaterialTable()', LMAT),
        ('void ReadLiquidObjectTable()', LOBJ), ('void ReadLiquidTypeTable()', LTYPE)])
    patch_file(os.path.join(t, 'vmap4_extractor', 'vmapexport.cpp'), [
        ('void ReadMapTable()', MAP_VMAP), ('void ReadLiquidMaterialTable()', LMAT.replace('        LiquidMaterials[r.Id].LVF = r.LVF;\n', '')),
        ('void ReadLiquidTypeTable()', LTYPE.replace('        LiquidTypes[r.Id].SoundBank = r.SoundBank;\n', ''))])
    patch_file(os.path.join(t, 'vmap4_extractor', 'gameobject_extract.cpp'), [('void ExtractGameobjectModels()', GO_SKIP)])
    patch_file(os.path.join(t, 'mmaps_generator', 'PathGenerator.cpp'), [
        ('std::unordered_map<uint32, uint8> LoadLiquid(', LIQ_MMAP), ('void LoadMap(', MAP_MMAP)])


if __name__ == '__main__':
    main()
