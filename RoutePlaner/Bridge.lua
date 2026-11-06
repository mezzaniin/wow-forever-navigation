local ADDON, ns = ...

-- Bridge zur Webkarte (Forever-Karte): zeichnet oben links eine Leiste farbiger Pixel,
-- die das Begleitprogramm (forever-karte, Linux) per Bildschirmaufnahme liest.
-- Kein Netzwerk, keine geschützten Funktionen – nur Lesen von Position/Routenzustand.
--
-- Leiste: 2 Kopfzellen (weiß, schwarz) + 54 Datenzellen à 3 Bit (R,G,B an/aus, MSB = R).
-- 20 Bytes (160 Bit + 2 Füllbits), MSB zuerst. Format-Version 1:
--   0      Magic 0xA7
--   1      Version (4 Bit) << 4 | Rahmentyp (4 Bit)
--   2-3    uiMapId   4-5 x (0..65535 = 0..1)   6-7 y
--   8-9    Blickrichtung (12 Bit, 0..4095 = 0..2π) << 4 | Flags (1 gültig, 2 Instanz, 4 Kampf, 8 Flug)
--   10     Sequenzzähler
--   11-17  Zusatz je Rahmentyp (rotierend, Position steht in jedem Rahmen):
--          1 Zustand: Bits(Fraktion 0-1: 1 Allianz/2 Horde, 2 Ziel, 3 Transit, 4 frei, 5-7 Teleports),
--                     Tempo*10, Routen-Hash (16), Schrittindex, Schrittanzahl, Anzahl Flugpunkte
--          2 Ziel:    uiMapId (16), x (16), y (16), Ziel-Zähler
--          3 Flug:    Blockindex, 3 TaxiNode-IDs (je 16; 0 = leer) – bekannte Flugpunkte, sortiert
--          4 Figur:   classID, raceID, Stufe, Talentpunkte Baum 1, 2, 3, Fraktion (1 Allianz/2 Horde)
--   18-19  CRC-16/CCITT-FALSE über Byte 0..17

local VERSION = 1
local MAGIC = 0xA7
local DATA_CELLS = 54
local CELLS = DATA_CELLS + 2
local floor = math.floor
local Bridge = {}
ns.Bridge = Bridge

---------------------------------------------------------------------------
-- Kodierung (reines Lua 5.1; wird auch außerhalb des Spiels getestet)
---------------------------------------------------------------------------
local function bxor(a, b)
    if bit and bit.bxor then return bit.bxor(a, b) end
    local r, p = 0, 1
    while a > 0 or b > 0 do
        local x, y = a % 2, b % 2
        if x ~= y then r = r + p end
        a, b, p = (a - x) / 2, (b - y) / 2, p * 2
    end
    return r
end

local CRC = {}
for i = 0, 255 do
    local c = i * 256
    for _ = 1, 8 do
        if c >= 32768 then c = bxor((c * 2) % 65536, 0x1021) else c = (c * 2) % 65536 end
    end
    CRC[i] = c
end

function Bridge.crc16(bytes, n)
    local c = 0xFFFF
    for i = 1, n or #bytes do
        c = bxor((c * 256) % 65536, CRC[bxor(floor(c / 256), bytes[i])])
    end
    return c
end

function Bridge.crc16str(s)
    local t = {}
    for i = 1, #s do t[i] = s:byte(i) end
    return Bridge.crc16(t, #t)
end

local function clamp(v, lo, hi) v = floor(v or 0); if v < lo then return lo elseif v > hi then return hi end return v end
local function u16(t, v) v = clamp(v, 0, 65535); t[#t + 1] = floor(v / 256); t[#t + 1] = v % 256 end
local function frac16(v) return floor((v or 0) * 65535 + 0.5) end

--- 20 Bytes aus Position + Zusatz (7 Bytes) bauen.
function Bridge.bytes(ftype, map, x, y, facing, flags, seq, ext)
    local tau = 2 * math.pi
    local f = (facing or 0) - floor((facing or 0) / tau) * tau
    local fi = floor(f / tau * 4096 + 0.5) % 4096
    local b = { MAGIC, VERSION * 16 + ftype }
    u16(b, map); u16(b, frac16(x)); u16(b, frac16(y))
    u16(b, fi * 16 + clamp(flags, 0, 15))
    b[#b + 1] = floor(seq or 0) % 256
    for i = 1, 7 do b[#b + 1] = clamp(ext and ext[i] or 0, 0, 255) end
    local c = Bridge.crc16(b, 18)
    b[19] = floor(c / 256); b[20] = c % 256
    return b
end

--- Zellcodes 0..7 (56 Stück inkl. Kopf).
function Bridge.cells(b)
    local out = { 7, 0 }
    local acc, nacc = 0, 0
    for i = 1, 20 do
        acc = acc * 256 + b[i]; nacc = nacc + 8
        while nacc >= 3 do
            local div = 2 ^ (nacc - 3)
            local v = floor(acc / div)
            out[#out + 1] = v; acc = acc - v * div; nacc = nacc - 3
        end
    end
    out[#out + 1] = acc * 2 ^ (3 - nacc)   -- 2 Restbits + Füllbit
    return out
end

---------------------------------------------------------------------------
-- Routenzustand
---------------------------------------------------------------------------
--- Kennung der Route: CRC-16 über "art>ziel-id;" je Schritt (gleich berechnet in der Webkarte).
function Bridge.routeHash(steps)
    if not steps or #steps == 0 then return 0 end
    local parts = {}
    for i, st in ipairs(steps) do parts[i] = (st.kind or "?") .. ">" .. ((st.to and st.to.id) or "?") end
    return Bridge.crc16str(table.concat(parts, ";"))
end

local goalSerial, lastGoal = 0, nil
local flightIds, flightChunk = {}, 0

local function state()
    if not ns.getState then return end
    return ns.getState()
end

local function extState()
    local goal, route, transit, db, cdb = state()
    local bits = 0
    local fac = UnitFactionGroup and UnitFactionGroup("player")
    if fac == "Alliance" then bits = 1 elseif fac == "Horde" then bits = 2 end
    if goal then bits = bits + 4 end
    if transit then bits = bits + 8 end
    for i, tp in ipairs(ns.teleports or {}) do
        if i <= 3 and IsPlayerSpell and IsPlayerSpell(tp.spell) then bits = bits + 2 ^ (4 + i) end
    end
    local hash = goal and Bridge.routeHash(route) or 0
    local total = (goal and ns.stepTotal) or 0
    local idx = (goal and route and total > 0) and (total - #route + 1) or 0
    return { bits, clamp(((db and db.speed) or 7) * 10, 0, 255), floor(hash / 256), hash % 256,
        clamp(idx, 0, 255), clamp(total, 0, 255), clamp(#flightIds, 0, 255) }
end

local function extGoal()
    local goal = state()
    if goal ~= lastGoal then lastGoal = goal; goalSerial = (goalSerial + 1) % 256 end
    if not goal or not goal.mapID then return { 0, 0, 0, 0, 0, 0, goalSerial } end
    local mx, gx, gy = clamp(goal.mapID, 0, 65535), frac16(goal.x), frac16(goal.y)
    return { floor(mx / 256), mx % 256, floor(gx / 256), gx % 256, floor(gy / 256), gy % 256, goalSerial }
end

local function talentPoints(i)
    -- robust: Ränge der einzelnen Talente aufsummieren (Rückgabeformat von GetTalentTabInfo ist je Client anders)
    local sum = 0
    if GetNumTalents and GetTalentInfo then
        for t = 1, (GetNumTalents(i) or 0) do
            local _, _, _, _, rank = GetTalentInfo(i, t)
            sum = sum + (tonumber(rank) or 0)
        end
    end
    if sum > 0 or not GetTalentTabInfo then return sum end
    local r = { GetTalentTabInfo(i) }
    local v = (type(r[1]) == "number" and r[5]) or r[3]
    return tonumber(v) or 0
end

local function extCharacter()
    local _, _, classId = UnitClass("player")
    local _, _, raceId = UnitRace("player")
    local fac = UnitFactionGroup and UnitFactionGroup("player")
    return { clamp(classId, 0, 255), clamp(raceId, 0, 255), clamp(UnitLevel("player"), 0, 255),
        clamp(talentPoints(1), 0, 255), clamp(talentPoints(2), 0, 255), clamp(talentPoints(3), 0, 255),
        fac == "Alliance" and 1 or fac == "Horde" and 2 or 0 }
end

local function refreshFlights()
    local _, _, _, _, cdb = state()
    wipe(flightIds)
    for id in pairs((cdb and cdb.flights) or {}) do
        if type(id) == "number" then flightIds[#flightIds + 1] = id end
    end
    table.sort(flightIds)
end

local function extFlights()
    local chunks = math.max(1, math.ceil(#flightIds / 3))
    flightChunk = flightChunk % chunks
    local e = { flightChunk }
    for k = 1, 3 do
        local id = flightIds[flightChunk * 3 + k] or 0
        e[#e + 1] = floor(id / 256); e[#e + 1] = id % 256
    end
    flightChunk = flightChunk + 1
    return e
end

---------------------------------------------------------------------------
-- Anzeige
---------------------------------------------------------------------------
local frame = CreateFrame("Frame", "RoutePlanerBridgeStrip", nil)   -- ohne UIParent: bleibt bei Alt+Z sichtbar
frame:SetFrameStrata("TOOLTIP")
frame:SetFrameLevel(10000)
frame:EnableMouse(false)
frame:SetScale(1)
frame:Hide()
local tex = {}
local bdb
local seq, tick, elapsed = 0, 0, 0
local ROTATION = { 1, 2, 1, 3, 1, 4 }

local function physicalHeight()
    if GetPhysicalScreenSize then
        local _, h = GetPhysicalScreenSize()
        if h and h > 0 then return h end
    end
    for _, cvar in ipairs({ "gxWindowedResolution", "gxFullscreenResolution", "gxResolution" }) do
        local v = GetCVar and GetCVar(cvar)
        local h = v and tonumber(string.match(v, "%d+x(%d+)"))
        if h and h > 0 then return h end
    end
    return 768
end

-- UI-Einheiten pro physischem Pixel für diesen Frame
function Bridge.pixelUnit()
    return ((GetScreenHeight and GetScreenHeight() or 768) / physicalHeight()) / (frame:GetEffectiveScale() or 1)
end

local function layout()
    local s = math.max(1, floor(bdb.cell or 4)) * Bridge.pixelUnit()
    frame:ClearAllPoints()
    frame:SetPoint("TOPLEFT", nil, "TOPLEFT", 0, 0)
    frame:SetSize(s * CELLS, s)
    for i = 1, CELLS do
        local t = tex[i]
        if not t then
            t = frame:CreateTexture(nil, "OVERLAY")
            if t.SetSnapToPixelGrid then t:SetSnapToPixelGrid(false) end
            if t.SetTexelSnappingBias then t:SetTexelSnappingBias(0) end
            tex[i] = t
        end
        t:ClearAllPoints()
        t:SetPoint("TOPLEFT", frame, "TOPLEFT", (i - 1) * s, 0)
        t:SetSize(s, s)
    end
end

function Bridge.readPosition()
    local flags, x, y = 0, 0, 0
    local map = C_Map.GetBestMapForUnit("player")
    if map then
        local pos = C_Map.GetPlayerMapPosition(map, "player")
        if pos then
            if pos.GetXY then x, y = pos:GetXY() else x, y = pos.x, pos.y end
            if x and y and (x > 0 or y > 0) then flags = 1 else x, y = 0, 0 end
        end
    end
    if IsInInstance and IsInInstance() then flags = flags + 2 end
    if UnitAffectingCombat and UnitAffectingCombat("player") then flags = flags + 4 end
    if UnitOnTaxi and UnitOnTaxi("player") then flags = flags + 8 end
    return map or 0, x or 0, y or 0, (GetPlayerFacing and GetPlayerFacing()) or 0, flags
end

function Bridge.frameBytes(ftype)
    local map, x, y, facing, flags = Bridge.readPosition()
    seq = (seq + 1) % 256
    local ext = ftype == 1 and extState() or ftype == 2 and extGoal() or ftype == 4 and extCharacter() or extFlights()
    return Bridge.bytes(ftype, map, x, y, facing, flags, seq, ext)
end

local function paint()
    tick = tick + 1
    if tick % 50 == 1 then refreshFlights() end
    local codes = Bridge.cells(Bridge.frameBytes(ROTATION[(tick - 1) % #ROTATION + 1]))
    for i = 1, CELLS do
        local c = codes[i]
        tex[i]:SetColorTexture(c >= 4 and 1 or 0, (c % 4) >= 2 and 1 or 0, c % 2, 1)
    end
end
Bridge.paint = paint

frame:SetScript("OnUpdate", function(_, dt)
    elapsed = elapsed + dt
    if elapsed < (bdb and bdb.interval or 0.1) then return end
    elapsed = 0
    local ok, err = pcall(paint)
    if not ok and not Bridge.err then Bridge.err = err; print("|cff33ccffRoutePlaner:|r Webkarte-Leiste: " .. tostring(err)) end
end)

local function setEnabled(on)
    bdb.enabled = on and true or false
    if bdb.enabled then layout(); refreshFlights(); paint(); frame:Show() else frame:Hide() end
end

local function msg(s) print("|cff33ccffRoutePlaner:|r " .. s) end

local ev = CreateFrame("Frame")
ev:RegisterEvent("ADDON_LOADED")
ev:RegisterEvent("PLAYER_ENTERING_WORLD")
ev:RegisterEvent("UI_SCALE_CHANGED")
ev:RegisterEvent("DISPLAY_SIZE_CHANGED")
ev:RegisterEvent("TAXIMAP_OPENED")
ev:SetScript("OnEvent", function(_, e, arg)
    if e == "ADDON_LOADED" then
        if arg ~= ADDON then return end
        RoutePlanerDB = RoutePlanerDB or {}
        RoutePlanerDB.web = RoutePlanerDB.web or {}
        bdb = RoutePlanerDB.web
        if bdb.enabled == nil then bdb.enabled = false end   -- erst mit /route web an
        bdb.cell = bdb.cell or 4
    elseif bdb then
        if e == "PLAYER_ENTERING_WORLD" then setEnabled(bdb.enabled)
        elseif e == "TAXIMAP_OPENED" then refreshFlights()
        elseif bdb.enabled then layout() end
    end
end)

-- /route web …  (übrige /route-Befehle gehen an Core.lua)
local core = SlashCmdList.ROUTEPLANER
SlashCmdList.ROUTEPLANER = function(input)
    local cmd, rest = strtrim(input or ""):match("^(%S*)%s*(.-)$")
    if (cmd or ""):lower() ~= "web" then return core(input) end
    local sub, arg = (rest or ""):lower():match("^(%S*)%s*(.-)$")
    if sub == "" then
        setEnabled(not bdb.enabled); msg("Webkarte-Leiste " .. (bdb.enabled and "|cff00ff00an|r" or "|cffff5555aus|r"))
    elseif sub == "an" or sub == "on" then setEnabled(true); msg("Webkarte-Leiste an")
    elseif sub == "aus" or sub == "off" then setEnabled(false); msg("Webkarte-Leiste aus")
    elseif sub == "größe" or sub == "groesse" or sub == "size" then
        local n = tonumber(arg)
        if n then bdb.cell = math.max(1, math.min(16, floor(n))); if bdb.enabled then layout() end end
        msg("Zellgröße " .. bdb.cell .. " px")
    else
        local map, x, y, facing, flags = Bridge.readPosition()
        local goal, route = state()
        msg(string.format("Webkarte-Leiste %s · Karte %d %.1f/%.1f · Blick %.0f° · Flags %d", bdb.enabled and "an" or "aus",
            map, x * 100, y * 100, math.deg(facing), flags))
        msg(string.format("Zelle %d px · Leiste %d×%d px · Bildschirm %d px hoch · Flugpunkte %d · Route %s (Hash %04X)",
            bdb.cell, bdb.cell * CELLS, bdb.cell, physicalHeight(), #flightIds,
            goal and ((route and #route or 0) .. " Schritte") or "keine", goal and Bridge.routeHash(route) or 0))
        if flags % 2 == 0 then msg("|cffff8800Keine Position verfügbar (Instanz?)|r") end
    end
end
