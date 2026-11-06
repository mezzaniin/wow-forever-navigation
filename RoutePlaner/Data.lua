local _, ns = ...

-- Koordinaten: {uiMapID, x, y (0-100), Bezeichnung}
-- Werte aus dem Gedächtnis (Classic) -> mit "/route punkt <key>" vor Ort korrigierbar.
ns.transports = {
    { key = "boot_auberdine_ruttheran", faction = "Alliance", kind = "boat", cost = 150,
      a = { 1439, 33.2, 40.3, "Steg Auberdine" },
      b = { 1438, 54.9, 96.8, "Steg Rut'theran" } },
    { key = "boot_auberdine_menethil", faction = "Alliance", kind = "boat", cost = 180,
      a = { 1439, 32.4, 43.8, "Steg Auberdine (Süd)" },
      b = { 1437, 4.6, 57.2, "Steg Menethil (Auberdine)" } },
    { key = "boot_menethil_theramore", faction = "Alliance", kind = "boat", cost = 180,
      a = { 1437, 5.0, 63.4, "Steg Menethil (Theramore)" },
      b = { 1445, 71.6, 56.4, "Steg Theramore" } },
    { key = "boot_beutebucht_ratschet", kind = "boat", cost = 180,
      a = { 1434, 25.9, 73.1, "Steg Beutebucht" },
      b = { 1413, 63.7, 38.6, "Steg Ratschet" } },
    { key = "portal_ruttheran_darnassus", faction = "Alliance", kind = "portal", cost = 10,
      a = { 1438, 55.9, 89.4, "Portal Rut'theran" },
      b = { 1457, 30.0, 41.0, "Portal Darnassus" } },
    { key = "tiefenbahn_sw_if", faction = "Alliance", kind = "tram", cost = 90,
      a = { 1453, 66.0, 34.0, "Tiefenbahn Sturmwind" },
      b = { 1455, 76.6, 51.1, "Tiefenbahn Eisenschmiede" } },
    { key = "zeppelin_durotar_tirisfal", faction = "Horde", kind = "zeppelin", cost = 180,
      a = { 1411, 50.8, 13.6, "Zeppelinturm Durotar (Tirisfal)" },
      b = { 1420, 60.7, 58.8, "Zeppelinturm Tirisfal (Orgrimmar)" } },
    { key = "zeppelin_durotar_gromgol", faction = "Horde", kind = "zeppelin", cost = 180,
      a = { 1411, 50.6, 12.6, "Zeppelinturm Durotar (Grom'gol)" },
      b = { 1434, 31.6, 29.2, "Zeppelinturm Grom'gol (Orgrimmar)" } },
    { key = "zeppelin_tirisfal_gromgol", faction = "Horde", kind = "zeppelin", cost = 180,
      a = { 1420, 61.9, 59.1, "Zeppelinturm Tirisfal (Grom'gol)" },
      b = { 1434, 31.4, 30.2, "Zeppelinturm Grom'gol (Tirisfal)" } },
}

-- Zauber, die von überall wirken
ns.teleports = {
    { key = "tp_mondlichtung", spell = 18960, cost = 15, name = "Teleportieren: Mondlichtung",
      to = { 1450, 44.1, 45.2, "Mondlichtung (Nachthafen)" } },
}

ns.kindText = {
    walk = "Laufen", boat = "Boot", zeppelin = "Zeppelin", tram = "Tiefenbahn",
    portal = "Portal", flight = "Flug", teleport = "Teleport",
}

ns.kindColor = {
    walk = { 1, 0.82, 0 }, boat = { 0.25, 0.6, 1 }, zeppelin = { 1, 0.45, 0.2 },
    flight = { 0.2, 1, 0.35 }, tram = { 0.8, 0.8, 0.8 }, portal = { 0.85, 0.4, 1 },
    teleport = { 0.85, 0.4, 1 },
}

-- Getrennte Landmassen innerhalb eines Kontinents (man kann nicht hinlaufen)
function ns.islandOf(mapID, x, y)
    if mapID == 1457 then return "teldrassil" end
    if mapID == 1438 then
        if y and y > 0.84 and x > 0.48 then return "ruttheran" end
        return "teldrassil"
    end
end

-- Stufenbereiche (Classic-Standard; Werte aus C_Map.GetMapLevels haben Vorrang)
ns.zoneLevels = {
    -- Kalimdor
    [1411] = { 1, 10 }, [1412] = { 1, 10 }, [1438] = { 1, 10 }, [1439] = { 10, 20 }, [1413] = { 10, 25 },
    [1442] = { 15, 27 }, [1440] = { 18, 30 }, [1441] = { 25, 35 }, [1443] = { 30, 40 }, [1445] = { 35, 45 },
    [1444] = { 40, 50 }, [1446] = { 40, 50 }, [1447] = { 45, 55 }, [1448] = { 48, 55 }, [1449] = { 48, 55 },
    [1451] = { 55, 60 }, [1452] = { 55, 60 }, [1450] = { 55, 60 },
    -- Östliche Königreiche
    [1429] = { 1, 10 }, [1426] = { 1, 10 }, [1420] = { 1, 10 }, [1436] = { 10, 20 }, [1432] = { 10, 20 },
    [1421] = { 10, 20 }, [1433] = { 15, 25 }, [1431] = { 18, 30 }, [1437] = { 20, 30 }, [1424] = { 20, 30 },
    [1416] = { 30, 40 }, [1417] = { 30, 40 }, [1434] = { 30, 45 }, [1418] = { 35, 45 }, [1435] = { 35, 45 },
    [1425] = { 40, 50 }, [1427] = { 43, 50 }, [1419] = { 45, 55 }, [1428] = { 50, 58 }, [1422] = { 51, 58 },
    [1423] = { 53, 60 }, [1430] = { 55, 60 },
}
