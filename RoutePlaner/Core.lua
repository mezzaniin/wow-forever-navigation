local ADDON, ns = ...

local PREFIX = "|cff33ccffRoutePlaner:|r "
local function msg(text) print(PREFIX .. text) end
local errSeen = {}
local function safeCall(fn, ...)
    local ok, err = pcall(fn, ...)
    if not ok and not errSeen[tostring(err)] then
        errSeen[tostring(err)] = true
        msg("|cffff5555Fehler:|r " .. tostring(err))
    end
end

local db
local cdb   -- pro Charakter (Flugpunkte)
local goal            -- Zielknoten
local route           -- aktuelle Schritte
local transit         -- { step = ..., t0 = ... } während Boot/Zeppelin/Bahn
local arrowUid, arrowKey
local UPDATE_INTERVAL = 2
local ARRIVE_DIST = 15     -- Yards
local BOARD_DIST = 25
local EXIT_DIST = 200

---------------------------------------------------------------------------
-- Koordinaten
---------------------------------------------------------------------------
local function landOf(mapID, x, y, cont)
    local m = mapID
    for _ = 1, 6 do
        if not m or m == 0 then break end
        local isl = ns.islandOf(m, x, y)
        if isl then return isl end
        local info = C_Map.GetMapInfo(m)
        if not info then break end
        if info.mapType == Enum.UIMapType.Continent then
            local child = C_Map.GetMapInfoAtPosition(m, x, y)
            if child and child.mapID then
                local cx = ns.islandOf(child.mapID, nil, nil)
                -- Für Teldrassil/Rut'theran Kind-Koordinaten bestimmen
                if cx and child.mapID == 1438 then
                    local c, wp = C_Map.GetWorldPosFromMapPos(m, CreateVector2D(x, y))
                    if c and wp then
                        local _, lp = C_Map.GetMapPosFromWorldPos(c, wp, 1438)
                        if lp then cx = ns.islandOf(1438, lp.x, lp.y) end
                    end
                end
                if cx then return cx end
            end
            break
        end
        if info.mapType < Enum.UIMapType.Continent then break end
        -- Koordinaten in die Elternkarte umrechnen
        local c, wp = C_Map.GetWorldPosFromMapPos(m, CreateVector2D(x, y))
        m = info.parentMapID
        if c and wp and m and m ~= 0 then
            local _, pp = C_Map.GetMapPosFromWorldPos(c, wp, m)
            if pp then x, y = pp.x, pp.y end
        end
    end
    return "c" .. tostring(cont)
end

-- x, y in 0-1
local function makeNode(mapID, x, y, label)
    if not mapID or not x or not y then return end
    local cont, wp = C_Map.GetWorldPosFromMapPos(mapID, CreateVector2D(x, y))
    if not cont or not wp then return end
    local land
    if ns.Nav and ns.Nav.has(cont) then
        local comp = ns.Nav.comp(cont, wp.x, wp.y)
        land = comp and ("n" .. cont .. ":" .. comp) or ("x" .. cont .. ":" .. mapID)
    else
        land = landOf(mapID, x, y, cont)
    end
    return {
        mapID = mapID, x = x, y = y, label = label or "?",
        cont = cont, wx = wp.x, wy = wp.y, land = land,
    }
end

local function dist(a, b)
    if a.cont ~= b.cont then return math.huge end
    local dx, dy = a.wx - b.wx, a.wy - b.wy
    return math.sqrt(dx * dx + dy * dy)
end

local function playerNode()
    local m = C_Map.GetBestMapForUnit("player")
    if not m then return end
    local p = C_Map.GetPlayerMapPosition(m, "player")
    if not p then return end
    local x, y = p:GetXY()
    return makeNode(m, x, y, "Dein Standort")
end

local function endpoint(t, side)
    local fix = db.fix[t.key .. ":" .. side]
    local d = t[side]
    if fix then return makeNode(fix[1], fix[2], fix[3], d[4]) end
    return makeNode(d[1], d[2] / 100, d[3] / 100, d[4])
end

---------------------------------------------------------------------------
-- Gelernte Wege (selbst abgelaufen = begehbar)
---------------------------------------------------------------------------
local roads
local roadNodes, grid = {}, {}
local CELL, SNAP, STEP = 40, 12, 20

local function cellKey(c, cx, cy) return c .. ":" .. cx .. ":" .. cy end

local function addRoadNode(i, p)
    roadNodes[i] = { cont = p[1], wx = p[2], wy = p[3], land = p[4], label = "Weg", road = i }
    local k = cellKey(p[1], math.floor(p[2] / CELL), math.floor(p[3] / CELL))
    grid[k] = grid[k] or {}
    table.insert(grid[k], i)
end

local function initRoads()
    db.roads = db.roads or { pts = {}, adj = {} }
    roads = db.roads
    wipe(roadNodes); wipe(grid)
    for i, p in ipairs(roads.pts) do addRoadNode(i, p) end
end

local function nearRoads(n, radius)
    local out = {}
    if not roads then return out end
    local r = math.ceil(radius / CELL)
    local cx, cy = math.floor(n.wx / CELL), math.floor(n.wy / CELL)
    for dx = -r, r do
        for dy = -r, r do
            local list = grid[cellKey(n.cont, cx + dx, cy + dy)]
            if list then
                for _, i in ipairs(list) do
                    local rn = roadNodes[i]
                    if rn.land == n.land then
                        local d = dist(n, rn)
                        if d <= radius then out[#out + 1] = { i, d } end
                    end
                end
            end
        end
    end
    return out
end

local function linkRoad(a, b)
    if a == b then return end
    roads.adj[a] = roads.adj[a] or {}
    roads.adj[b] = roads.adj[b] or {}
    for _, x in ipairs(roads.adj[a]) do if x == b then return end end
    table.insert(roads.adj[a], b)
    table.insert(roads.adj[b], a)
end

local lastRec
local function recordStep()
    if not roads or db.record == false then return end
    if UnitOnTaxi("player") or (IsFlying and IsFlying()) or (IsSwimming and IsSwimming()) or transit then
        lastRec = nil; return
    end
    if (GetUnitSpeed("player") or 0) <= 0 then return end
    local pos = playerNode()
    if not pos then lastRec = nil; return end
    if lastRec and (lastRec.node.cont ~= pos.cont or dist(lastRec.node, pos) > 60) then lastRec = nil end

    local best, bd
    for _, e in ipairs(nearRoads(pos, SNAP)) do
        if not bd or e[2] < bd then best, bd = e[1], e[2] end
    end
    if best then
        if lastRec and lastRec.i ~= best then linkRoad(lastRec.i, best) end
        lastRec = { i = best, node = roadNodes[best] }
        return
    end
    if lastRec and dist(lastRec.node, pos) < STEP then return end
    local i = #roads.pts + 1
    roads.pts[i] = { pos.cont, pos.wx, pos.wy, pos.land }
    addRoadNode(i, roads.pts[i])
    if lastRec then linkRoad(lastRec.i, i) end
    lastRec = { i = i, node = roadNodes[i] }
end

-- Kartenposition für einen Knoten (für TomTom)
local function ensureMapPos(n)
    if n.mapID then return true end
    local m, p = C_Map.GetMapPosFromWorldPos(n.cont, CreateVector2D(n.wx, n.wy))
    if m and p then n.mapID, n.x, n.y = m, p.x, p.y; return true end
    return false
end

---------------------------------------------------------------------------
-- Routenplanung (Dijkstra mit Heap)
---------------------------------------------------------------------------
local OFFROAD_PENALTY = 1.6   -- Schätzung bis der Rasterweg berechnet ist

local function plan(start, target)
    local speed = db.speed or 7
    local keys, kadj = { start, target }, { {}, {} }
    local function addKey(n) keys[#keys + 1] = n; kadj[#keys] = {}; return #keys end
    local function kedge(i, j, cost, kind, name)
        table.insert(kadj[i], { to = j, cost = cost, kind = kind, name = name })
    end

    local faction = UnitFactionGroup("player")
    for _, t in ipairs(ns.transports) do
        local a, b = endpoint(t, "a"), endpoint(t, "b")
        if a and b and (not t.faction or t.faction == faction) then
            local ia, ib = addKey(a), addKey(b)
            kedge(ia, ib, t.cost, t.kind, t.key)
            kedge(ib, ia, t.cost, t.kind, t.key)
        end
    end
    for _, tp in ipairs(ns.teleports) do
        if IsPlayerSpell and IsPlayerSpell(tp.spell) then
            local n = makeNode(tp.to[1], tp.to[2] / 100, tp.to[3] / 100, tp.to[4])
            if n then kedge(1, addKey(n), tp.cost, "teleport", tp.name) end
        end
    end
    local fl = {}
    for _, f in pairs(cdb.flights) do
        local n = makeNode(f.m, f.x, f.y, "Flugmeister " .. (f.name or "?"))
        if n then fl[#fl + 1] = addKey(n) end
    end
    for _, i in ipairs(fl) do
        for _, j in ipairs(fl) do
            if i ~= j and keys[i].cont == keys[j].cont then
                kedge(i, j, 20 + dist(keys[i], keys[j]) / 20, "flight")
            end
        end
    end

    local K = #keys
    -- Luftlinie zwischen Schlüsselpunkten (Notlösung, rot)
    for i = 1, K do
        for j = 2, K do
            if i ~= j and keys[i].land == keys[j].land then
                local d = dist(keys[i], keys[j])
                kedge(i, j, d * (d < 60 and 1.2 or OFFROAD_PENALTY) / speed, "offroad")
            end
        end
    end
    -- Schlüsselpunkte an gelernte Wege anbinden
    local extra = {}
    for i = 1, K do
        for _, e in ipairs(nearRoads(keys[i], 120)) do
            local c = e[2] * 1.3 / speed
            if i ~= 1 then
                extra[e[1]] = extra[e[1]] or {}
                table.insert(extra[e[1]], { to = i, cost = c })
            end
            if i ~= 2 then kedge(i, K + e[1], c, "walk") end
        end
    end

    local function nodeOf(id) if id <= K then return keys[id] end return roadNodes[id - K] end

    local best, prev, done = { [1] = 0 }, {}, {}
    local heap = { { 0, 1 } }
    local function push(c, id)
        heap[#heap + 1] = { c, id }
        local i = #heap
        while i > 1 do
            local pi = math.floor(i / 2)
            if heap[pi][1] <= heap[i][1] then break end
            heap[pi], heap[i] = heap[i], heap[pi]; i = pi
        end
    end
    local function pop()
        local top = heap[1]
        local last = table.remove(heap)
        if #heap > 0 then
            heap[1] = last
            local i = 1
            while true do
                local l, r, m = 2 * i, 2 * i + 1, i
                if l <= #heap and heap[l][1] < heap[m][1] then m = l end
                if r <= #heap and heap[r][1] < heap[m][1] then m = r end
                if m == i then break end
                heap[m], heap[i] = heap[i], heap[m]; i = m
            end
        end
        return top
    end
    local function relax(u, c, v, cost, kind, name)
        local nc = c + cost
        if nc < (best[v] or math.huge) then
            best[v] = nc
            prev[v] = { from = u, kind = kind, name = name, cost = cost }
            push(nc, v)
        end
    end

    while #heap > 0 do
        local t = pop()
        local c, u = t[1], t[2]
        if not done[u] then
            done[u] = true
            if u == 2 then break end
            if u <= K then
                for _, e in ipairs(kadj[u]) do relax(u, c, e.to, e.cost, e.kind, e.name) end
            else
                local r = u - K
                local rn = roadNodes[r]
                for _, v in ipairs(roads.adj[r] or {}) do
                    relax(u, c, K + v, dist(rn, roadNodes[v]) / speed, "walk")
                end
                for _, e in ipairs(extra[r] or {}) do relax(u, c, e.to, e.cost, "walk") end
            end
        end
    end
    if not best[2] then return end

    local edges, cur = {}, 2
    while prev[cur] do
        local p = prev[cur]
        table.insert(edges, 1, { kind = p.kind, name = p.name, cost = p.cost, from = nodeOf(p.from), to = nodeOf(cur) })
        cur = p.from
    end
    -- Lauf-Abschnitte zu einem Schritt mit Polylinie zusammenfassen
    local steps = {}
    for _, e in ipairs(edges) do
        if e.kind == "walk" or e.kind == "offroad" then
            local last = steps[#steps]
            if last and last.kind == "walk" then
                last.to = e.to; last.cost = last.cost + e.cost
                table.insert(last.path, e.to); table.insert(last.off, e.kind == "offroad")
            else
                steps[#steps + 1] = { kind = "walk", from = e.from, to = e.to, cost = e.cost,
                    path = { e.from, e.to }, off = { e.kind == "offroad" } }
            end
        else
            steps[#steps + 1] = { kind = e.kind, from = e.from, to = e.to, cost = e.cost, name = e.name }
        end
    end
    return steps, best[2]
end

local function pathLen(st, pos)
    if not st.path then return dist(pos or st.from, st.to) end
    local sum, prevN = 0, pos or st.path[1]
    for i = 2, #st.path do sum = sum + dist(prevN, st.path[i]); prevN = st.path[i] end
    return sum
end

local function hasOffroad(st)
    for _, o in ipairs(st.off or {}) do if o then return true end end
    return false
end

---------------------------------------------------------------------------
-- Texte
---------------------------------------------------------------------------
local function yards(n) return string.format("%d m", math.floor(n + 0.5)) end
local function mins(sec) return string.format("%d min", math.max(1, math.ceil(sec / 60))) end

local function stepText(s)
    local k = s.kind
    if k == "walk" then
        return "Lauf zu " .. s.to.label
    elseif k == "boat" then
        return "Nimm das Boot: " .. s.from.label .. " -> " .. s.to.label
    elseif k == "zeppelin" then
        return "Nimm den Zeppelin: " .. s.from.label .. " -> " .. s.to.label
    elseif k == "tram" then
        return "Nimm die Tiefenbahn: " .. s.from.label .. " -> " .. s.to.label
    elseif k == "portal" then
        return "Geh durchs Portal: " .. s.from.label .. " -> " .. s.to.label
    elseif k == "flight" then
        return "Flieg: " .. s.from.label:gsub("^Flugmeister ", "") .. " -> " .. s.to.label:gsub("^Flugmeister ", "")
    elseif k == "teleport" then
        return "Wirke: " .. (s.name or "Teleport") .. " -> " .. s.to.label
    end
    return k
end

---------------------------------------------------------------------------
-- Schritt-Fenster (Details-Stil)
---------------------------------------------------------------------------
local ICON = {
    walk = "Interface\\Icons\\Ability_Rogue_Sprint",
    boat = "Interface\\Icons\\INV_Misc_Spyglass_03",
    zeppelin = "Interface\\Icons\\INV_Misc_EngGizmos_01",
    tram = "Interface\\Icons\\INV_Misc_Gear_01",
    portal = "Interface\\Icons\\Spell_Arcane_PortalDarnassus",
    teleport = "Interface\\Icons\\Spell_Arcane_TeleportMoonglade",
    flight = "Interface\\Icons\\Ability_Hunter_EagleEye",
    goal = "Interface\\Icons\\INV_Misc_Map_01",
    info = "Interface\\Icons\\INV_Misc_QuestionMark",
}
local ROW_H, TITLE_H = 20, 22
local FONT = STANDARD_TEXT_FONT

local frame = CreateFrame("Frame", "RoutePlanerFrame", UIParent, "BackdropTemplate")
frame:SetSize(300, 160)
frame:SetFrameStrata("MEDIUM")
frame:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8", edgeFile = "Interface\\Buttons\\WHITE8X8", edgeSize = 1 })
frame:SetBackdropColor(0.05, 0.05, 0.06, 0.88)
frame:SetBackdropBorderColor(0, 0, 0, 1)
frame:SetMovable(true); frame:SetResizable(true); frame:EnableMouse(true); frame:SetClampedToScreen(true)
if frame.SetResizeBounds then frame:SetResizeBounds(200, 60, 700, 700) end
frame:Hide()

local function savePos()
    local p, _, rp, x, y = frame:GetPoint()
    db.pos = { p, rp, x, y }
    db.size = { frame:GetWidth(), frame:GetHeight() }
end

-- Titelleiste
local bar = CreateFrame("Frame", nil, frame, "BackdropTemplate")
bar:SetPoint("TOPLEFT"); bar:SetPoint("TOPRIGHT"); bar:SetHeight(TITLE_H)
bar:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8" })
bar:SetBackdropColor(0.1, 0.1, 0.11, 1)
bar:EnableMouse(true)
bar:RegisterForDrag("LeftButton")
bar:SetScript("OnDragStart", function() if not db.locked then frame:StartMoving() end end)
bar:SetScript("OnDragStop", function() frame:StopMovingOrSizing(); savePos() end)

local tIcon = bar:CreateTexture(nil, "ARTWORK")
tIcon:SetSize(16, 16); tIcon:SetPoint("LEFT", 4, 0)
tIcon:SetTexture(ICON.goal); tIcon:SetTexCoord(0.08, 0.92, 0.08, 0.92)
local tText = bar:CreateFontString(nil, "OVERLAY")
tText:SetFont(FONT, 12, "OUTLINE"); tText:SetTextColor(1, 1, 1)
tText:SetPoint("LEFT", tIcon, "RIGHT", 6, 0); tText:SetPoint("RIGHT", -60, 0)
tText:SetJustifyH("LEFT"); tText:SetWordWrap(false)
tText:SetText("Route")
local tEta = bar:CreateFontString(nil, "OVERLAY")
tEta:SetFont(FONT, 11, "OUTLINE"); tEta:SetTextColor(0.7, 0.7, 0.7)
tEta:SetPoint("RIGHT", -24, 0)

local function smallButton(tex, anchorX, tip, onClick)
    local b = CreateFrame("Button", nil, bar)
    b:SetSize(14, 14); b:SetPoint("RIGHT", anchorX, 0)
    local t = b:CreateTexture(nil, "ARTWORK"); t:SetAllPoints(); t:SetTexture(tex)
    t:SetVertexColor(0.8, 0.8, 0.8)
    b:SetScript("OnEnter", function()
        t:SetVertexColor(1, 1, 1)
        GameTooltip:SetOwner(b, "ANCHOR_TOP"); GameTooltip:SetText(tip); GameTooltip:Show()
    end)
    b:SetScript("OnLeave", function() t:SetVertexColor(0.8, 0.8, 0.8); GameTooltip:Hide() end)
    b:SetScript("OnClick", onClick)
    return b
end
smallButton("Interface\\Buttons\\UI-StopButton", -5, "Fenster ausblenden (/route fenster)",
    function() frame:Hide(); db.hidden = true end)

-- Inhalt
local content = CreateFrame("Frame", nil, frame)
content:SetPoint("TOPLEFT", 1, -TITLE_H - 1); content:SetPoint("BOTTOMRIGHT", -1, 14)
content:SetClipsChildren(true)
local rows = {}

local function getRow(i)
    local r = rows[i]
    if r then return r end
    r = CreateFrame("Frame", nil, content)
    r:SetHeight(ROW_H - 1)
    r:SetPoint("TOPLEFT", 0, -(i - 1) * ROW_H); r:SetPoint("RIGHT")
    r.bg = r:CreateTexture(nil, "BACKGROUND"); r.bg:SetAllPoints()
    r.bg:SetTexture("Interface\\Buttons\\WHITE8X8")
    r.accent = r:CreateTexture(nil, "ARTWORK"); r.accent:SetWidth(3)
    r.accent:SetPoint("TOPLEFT"); r.accent:SetPoint("BOTTOMLEFT")
    r.accent:SetTexture("Interface\\Buttons\\WHITE8X8")
    r.icon = r:CreateTexture(nil, "ARTWORK"); r.icon:SetSize(ROW_H - 3, ROW_H - 3)
    r.icon:SetPoint("LEFT", 5, 0); r.icon:SetTexCoord(0.08, 0.92, 0.08, 0.92)
    r.right = r:CreateFontString(nil, "OVERLAY")
    r.right:SetFont(FONT, 11, "OUTLINE"); r.right:SetPoint("RIGHT", -6, 0)
    r.right:SetJustifyH("RIGHT")
    r.text = r:CreateFontString(nil, "OVERLAY")
    r.text:SetFont(FONT, 11, "OUTLINE")
    r.text:SetPoint("LEFT", r.icon, "RIGHT", 5, 0); r.text:SetPoint("RIGHT", r.right, "LEFT", -6, 0)
    r.text:SetJustifyH("LEFT"); r.text:SetWordWrap(false)
    rows[i] = r
    return r
end

-- Fuß: Größe ändern + fixieren
local grip = CreateFrame("Button", nil, frame)
grip:SetSize(12, 12); grip:SetPoint("BOTTOMRIGHT", -1, 1)
grip:SetNormalTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Up")
grip:SetHighlightTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Highlight")
grip:SetScript("OnMouseDown", function() if not db.locked then frame:StartSizing("BOTTOMRIGHT") end end)
grip:SetScript("OnMouseUp", function() frame:StopMovingOrSizing(); savePos() end)

local lockBtn = CreateFrame("Button", nil, frame)
lockBtn:SetSize(60, 12); lockBtn:SetPoint("BOTTOMRIGHT", grip, "BOTTOMLEFT", -4, 0)
lockBtn.t = lockBtn:CreateFontString(nil, "OVERLAY")
lockBtn.t:SetFont(FONT, 10, ""); lockBtn.t:SetPoint("RIGHT"); lockBtn.t:SetTextColor(0.5, 0.5, 0.5)
local function updateLock()
    lockBtn.t:SetText(db.locked and "gelöst" or "fixieren")
    grip:SetShown(not db.locked)
end
lockBtn:SetScript("OnClick", function() db.locked = not db.locked; updateLock() end)

-- rows: { kind=, text=, right=, active= }
local function setRows(title, eta, list)
    tText:SetText(title or "Route")
    tEta:SetText(eta or "")
    for i, d in ipairs(list) do
        local r = getRow(i)
        local col = ns.kindColor[d.kind] or { 0.6, 0.6, 0.6 }
        r.icon:SetTexture(ICON[d.kind] or ICON.info)
        r.accent:SetVertexColor(col[1], col[2], col[3], 1)
        if d.active then
            r.bg:SetVertexColor(col[1] * 0.45, col[2] * 0.45, col[3] * 0.45, 0.9)
            r.text:SetTextColor(1, 1, 1)
        else
            r.bg:SetVertexColor(0.13, 0.13, 0.14, 0.8)
            r.text:SetTextColor(0.75, 0.75, 0.75)
        end
        r.text:SetText((d.num and (d.num .. ". ") or "") .. (d.text or ""))
        r.right:SetText(d.right or "")
        r.right:SetTextColor(0.8, 0.8, 0.8)
        r:Show()
    end
    for i = #list + 1, #rows do rows[i]:Hide() end
    if not db.size then
        frame:SetHeight(TITLE_H + 16 + math.max(1, #list) * ROW_H)
    end
    if not db.hidden then frame:Show() end
end

local function setLines(header, lines)
    local list = {}
    for _, t in ipairs(lines) do list[#list + 1] = { kind = "info", text = t } end
    setRows("Route", header, list)
end

---------------------------------------------------------------------------
-- TomTom-Pfeil
---------------------------------------------------------------------------
local function clearArrow()
    if arrowUid and TomTom and TomTom.RemoveWaypoint then
        pcall(TomTom.RemoveWaypoint, TomTom, arrowUid)
    end
    arrowUid, arrowKey = nil, nil
end

local function setArrow(n, title)
    if not (TomTom and TomTom.AddWaypoint) then return end
    if not n then clearArrow(); return end
    local key = string.format("%d:%.4f:%.4f", n.mapID, n.x, n.y)
    if key == arrowKey then return end
    clearArrow()
    local ok, uid = pcall(TomTom.AddWaypoint, TomTom, n.mapID, n.x, n.y, {
        title = title, persistent = false, crazy = true, silent = true,
        from = ADDON, cleardistance = 0, arrivaldistance = 0,
    })
    if ok then arrowUid, arrowKey = uid, key end
end

---------------------------------------------------------------------------
-- Weltkarten-Overlay
---------------------------------------------------------------------------
local overlay, olLines, olText = nil, {}, {}

local function getOverlay()
    if overlay then return overlay end
    local canvas = WorldMapFrame and WorldMapFrame.ScrollContainer and WorldMapFrame.ScrollContainer.Child
    if not canvas then return end
    overlay = CreateFrame("Frame", nil, canvas)
    overlay:SetAllPoints(canvas)
    overlay:SetFrameLevel(canvas:GetFrameLevel() + 2000)
    overlay:EnableMouse(false)
    return overlay
end

local OFF_COL = { 1, 0.25, 0.25 }

local function clipBox(x1, y1, x2, y2)
    local t0, t1 = 0, 1
    local dx, dy = x2 - x1, y2 - y1
    local p = { -dx, dx, -dy, dy }
    local q = { x1, 1 - x1, y1, 1 - y1 }
    for i = 1, 4 do
        if p[i] == 0 then
            if q[i] < 0 then return end
        else
            local t = q[i] / p[i]
            if p[i] < 0 then if t > t1 then return end if t > t0 then t0 = t end
            else if t < t0 then return end if t < t1 then t1 = t end end
        end
    end
    return x1 + dx * t0, y1 + dy * t0, x1 + dx * t1, y1 + dy * t1
end

local function drawMap()
    local ov = getOverlay()
    if not ov then return end
    for _, l in ipairs(olLines) do l:Hide() end
    for _, t in ipairs(olText) do t:Hide() end
    if not route or not WorldMapFrame:IsShown() then return end

    local mapID = WorldMapFrame:GetMapID()
    if not mapID then return end
    local mcont, tl = C_Map.GetWorldPosFromMapPos(mapID, CreateVector2D(0, 0))
    local _, br = C_Map.GetWorldPosFromMapPos(mapID, CreateVector2D(1, 1))
    if not mcont or not tl or not br then return end
    local dX, dY = br.x - tl.x, br.y - tl.y
    if dX == 0 or dY == 0 then return end
    local w, h = ov:GetWidth(), ov:GetHeight()
    local k = 1
    if WorldMapFrame.ScrollContainer.GetCanvasScale then
        k = WorldMapFrame.ScrollContainer:GetCanvasScale() or 1
    end
    if k <= 0 then k = 1 end

    local function proj(n)
        if n.cont ~= mcont then return end
        return (n.wy - tl.y) / dY, (n.wx - tl.x) / dX
    end
    local li, ti = 0, 0
    local function line(a, b2, col, alpha, thick)
        local x1, y1 = proj(a)
        local x2, y2 = proj(b2)
        if not x1 or not x2 then return end
        x1, y1, x2, y2 = clipBox(x1, y1, x2, y2)
        if not x1 then return end
        li = li + 1
        local l = olLines[li]
        if not l then l = ov:CreateLine(nil, "OVERLAY"); olLines[li] = l end
        l:SetColorTexture(col[1], col[2], col[3], alpha)
        l:SetThickness(math.max(1, thick / k))
        l:SetStartPoint("TOPLEFT", ov, x1 * w, -y1 * h)
        l:SetEndPoint("TOPLEFT", ov, x2 * w, -y2 * h)
        l:Show()
    end
    local function label(n, text, col)
        local x, y = proj(n)
        if not x or x < 0 or x > 1 or y < 0 or y > 1 then return end
        ti = ti + 1
        local fs = olText[ti]
        if not fs then fs = ov:CreateFontString(nil, "OVERLAY"); olText[ti] = fs end
        fs:SetFont(STANDARD_TEXT_FONT, math.max(4, 13 / k), "OUTLINE")
        fs:SetTextColor(col[1], col[2], col[3])
        fs:ClearAllPoints(); fs:SetPoint("BOTTOM", ov, "TOPLEFT", x * w, -y * h + 4 / k)
        fs:SetText(text); fs:Show()
    end

    for i, s in ipairs(route) do
        local col = ns.kindColor[s.kind] or { 1, 1, 1 }
        if s.kind == "walk" and s.path then
            for j = 2, #s.path do
                if not s.off[j - 1] then line(s.path[j - 1], s.path[j], col, 0.95, 4) end
            end
        elseif s.kind == "boat" or s.kind == "zeppelin" or s.kind == "flight" then
            line(s.from, s.to, col, 0.9, 4)
        end
        if s.kind ~= "walk" then
            label(s.from, i .. ". " .. (ns.kindText[s.kind] or s.kind), col)
        end
    end
    if goal then label(goal, "Ziel", { 1, 0.2, 0.2 }) end
end

---------------------------------------------------------------------------
-- Stufenbereiche der Zonen auf der Weltkarte
---------------------------------------------------------------------------
local lvlText = {}
local function zoneLevels(m)
    if C_Map.GetMapLevels then
        local ok, minL, maxL = pcall(C_Map.GetMapLevels, m)
        if ok and minL and maxL and minL > 0 and maxL > 0 then return minL, maxL end
    end
    local l = ns.zoneLevels[m]
    if l then return l[1], l[2] end
end

local function levelColor(minL, maxL)
    local p = UnitLevel("player") or 1
    if p < minL - 2 then return 1, 0.25, 0.25          -- zu schwer
    elseif p < minL then return 1, 0.6, 0.2             -- knapp
    elseif p <= maxL then return 1, 0.85, 0.1           -- passt
    elseif p <= maxL + 5 then return 0.3, 0.9, 0.3      -- leicht
    end
    return 0.6, 0.6, 0.6                                -- grau
end

local function drawLevels()
    local ov = getOverlay()
    if not ov then return end
    for _, t in ipairs(lvlText) do t:Hide() end
    if db and db.levels == false then return end
    if not WorldMapFrame:IsShown() then return end
    local mapID = WorldMapFrame:GetMapID()
    local info = mapID and C_Map.GetMapInfo(mapID)
    if not info then return end
    local w, h = ov:GetWidth(), ov:GetHeight()
    local k = 1
    if WorldMapFrame.ScrollContainer.GetCanvasScale then k = WorldMapFrame.ScrollContainer:GetCanvasScale() or 1 end
    if k <= 0 then k = 1 end
    local i = 0
    local function put(x, y, text, size, r, g, b)
        i = i + 1
        local fs = lvlText[i]
        if not fs then fs = ov:CreateFontString(nil, "OVERLAY"); lvlText[i] = fs end
        fs:SetFont(STANDARD_TEXT_FONT, math.max(4, size / k), "OUTLINE")
        fs:SetTextColor(r, g, b)
        fs:ClearAllPoints(); fs:SetPoint("CENTER", ov, "TOPLEFT", x * w, -y * h)
        fs:SetText(text); fs:Show()
    end
    if info.mapType == Enum.UIMapType.Continent then
        for _, z in ipairs(C_Map.GetMapChildrenInfo(mapID, Enum.UIMapType.Zone) or {}) do
            local minL, maxL = zoneLevels(z.mapID)
            if minL then
                local l, r, t, b = C_Map.GetMapRectOnMap(z.mapID, mapID)
                if l and r and t and b then
                    local cr, cg, cb = levelColor(minL, maxL)
                    put((l + r) / 2, (t + b) / 2, z.name .. "\n" .. minL .. " - " .. maxL, 12, cr, cg, cb)
                end
            end
        end
    else
        local minL, maxL = zoneLevels(mapID)
        if minL then
            local cr, cg, cb = levelColor(minL, maxL)
            put(0.5, 0.06, "Stufe " .. minL .. " - " .. maxL, 18, cr, cg, cb)
        end
    end
end

local mapHooked = false
local buildSearchUI
local function initMap()
    if mapHooked or not WorldMapFrame then return end
    mapHooked = true
    local function redraw() safeCall(drawMap); safeCall(drawLevels) end
    WorldMapFrame:HookScript("OnShow", redraw)
    if WorldMapFrame.OnMapChanged then hooksecurefunc(WorldMapFrame, "OnMapChanged", redraw) end
    if WorldMapFrame.OnCanvasScaleChanged then hooksecurefunc(WorldMapFrame, "OnCanvasScaleChanged", redraw) end
    if buildSearchUI then buildSearchUI() end
end

---------------------------------------------------------------------------
-- Minimap-Overlay
---------------------------------------------------------------------------
-- Sichtradius (Yards) je Zoomstufe, falls Minimap:GetViewRadius fehlt
local MM_OUTDOOR = { [0] = 233 + 1 / 3, 200, 166 + 2 / 3, 133 + 1 / 3, 100, 66 + 2 / 3 }

local mmFrame = CreateFrame("Frame", nil, Minimap)
mmFrame:SetAllPoints(Minimap)
mmFrame:SetFrameLevel(Minimap:GetFrameLevel() + 5)
mmFrame:EnableMouse(false)
local mmLines = {}

local function clipToCircle(x1, y1, x2, y2, r)
    local dx, dy = x2 - x1, y2 - y1
    local a = dx * dx + dy * dy
    if a < 1e-6 then return end
    local b = 2 * (x1 * dx + y1 * dy)
    local c = x1 * x1 + y1 * y1 - r * r
    local disc = b * b - 4 * a * c
    if disc <= 0 then return end
    local s = math.sqrt(disc)
    local t0 = math.max(0, (-b - s) / (2 * a))
    local t1 = math.min(1, (-b + s) / (2 * a))
    if t0 >= t1 then return end
    return x1 + dx * t0, y1 + dy * t0, x1 + dx * t1, y1 + dy * t1
end

local mmElapsed = 0
local function mmUpdate()
    local li = 0
    local pos = route and not UnitOnTaxi("player") and playerNode()
    if pos then
        local radius = Minimap.GetViewRadius and Minimap:GetViewRadius()
            or MM_OUTDOOR[Minimap:GetZoom()] or 200
        local half = Minimap:GetWidth() / 2
        local px = half / radius
        local rot = GetCVar("rotateMinimap") == "1" and GetPlayerFacing and GetPlayerFacing()
        local cs, sn = 1, 0
        if rot then cs, sn = math.cos(-rot), math.sin(-rot) end
        -- Weltkoordinaten: x = Norden, y = Westen
        local function rel(n)
            local e, no = -(n.wy - pos.wy) * px, (n.wx - pos.wx) * px
            return e * cs - no * sn, e * sn + no * cs
        end
        local function seg(a, b2, col, alpha, thick)
            if a.cont ~= pos.cont or b2.cont ~= pos.cont then return end
            local x1, y1 = rel(a)
            local x2, y2 = rel(b2)
            local cx1, cy1, cx2, cy2 = clipToCircle(x1, y1, x2, y2, half * 0.94)
            if not cx1 then return end
            li = li + 1
            local l = mmLines[li]
            if not l then l = mmFrame:CreateLine(nil, "OVERLAY"); mmLines[li] = l end
            l:SetColorTexture(col[1], col[2], col[3], alpha)
            l:SetThickness(thick)
            l:SetStartPoint("CENTER", mmFrame, cx1, cy1)
            l:SetEndPoint("CENTER", mmFrame, cx2, cy2)
            l:Show()
        end
        for idx, s in ipairs(route) do
            local col = ns.kindColor[s.kind] or { 1, 1, 1 }
            if s.kind == "walk" and s.path then
                for j = 2, #s.path do
                    local a = (idx == 1 and j == 2) and pos or s.path[j - 1]
                    if not s.off[j - 1] then seg(a, s.path[j], col, 0.95, 3) end
                end
            elseif s.kind == "boat" or s.kind == "zeppelin" or s.kind == "flight" then
                seg(s.from, s.to, col, 0.95, 3)
            else
                break   -- nach Portal/Teleport/Bahn nicht weiterzeichnen
            end
        end
    end
    for i = li + 1, #mmLines do mmLines[i]:Hide() end
end
mmFrame:SetScript("OnUpdate", function(_, el)
    mmElapsed = mmElapsed + el
    if mmElapsed < 0.05 then return end
    mmElapsed = 0
    safeCall(mmUpdate)
end)

---------------------------------------------------------------------------
-- Hauptschleife
---------------------------------------------------------------------------
local function stopRoute(text)
    goal, route, transit = nil, nil, nil
    clearArrow(); drawMap(); frame:Hide()
    if text then msg(text) end
end

local update
local playerNav
local function navSegment(a, b, fromPlayer)
    if a.cont ~= b.cont or not ns.Nav or not ns.Nav.has(a.cont) then return nil, "nodata" end
    local tk = string.format("%d:%d:%d", b.cont, math.floor(b.wx / 30), math.floor(b.wy / 30))
    if fromPlayer and playerNav and playerNav.tk == tk then
        local bi, bd
        for i, p in ipairs(playerNav.pts) do
            local dx, dy = p[1] - a.wx, p[2] - a.wy
            local d = dx * dx + dy * dy
            if not bd or d < bd then bi, bd = i, d end
        end
        if bd and bd < 45 * 45 then
            local out = { { a.wx, a.wy } }
            for i = bi + 1, #playerNav.pts do out[#out + 1] = playerNav.pts[i] end
            if #out < 2 then out[2] = { b.wx, b.wy } end
            return out, "ok"
        end
    end
    local pts, st = ns.Nav.find(a.cont, a.wx, a.wy, b.wx, b.wy, function() safeCall(update) end)
    if pts and fromPlayer then playerNav = { tk = tk, pts = pts } end
    return pts, st
end

-- rote Luftlinien durch berechnete Wege (Navigationsraster) ersetzen
local function refine(steps)
    local pending = false
    for si, st in ipairs(steps) do
        if st.kind == "walk" and st.path then
            local np, no = { st.path[1] }, {}
            for j = 1, #st.path - 1 do
                local a, b = st.path[j], st.path[j + 1]
                local pts, status
                if st.off[j] then
                    pts, status = navSegment(a, b, si == 1 and j == 1)
                    if status == "pending" then pending = true end
                end
                if pts and #pts >= 2 then
                    for k = 2, #pts do
                        local q = pts[k]
                        np[#np + 1] = (k == #pts) and b or { cont = a.cont, wx = q[1], wy = q[2], land = a.land, label = "Weg" }
                        no[#no + 1] = false
                    end
                else
                    np[#np + 1] = b
                    no[#no + 1] = st.off[j]
                end
            end
            st.path, st.off = np, no
        end
    end
    return pending
end

function update()
    if not goal then return end
    if UnitOnTaxi("player") then
        setRows("Route: " .. (goal.label or "Ziel"), "im Flug", {
            { kind = "flight", text = "Im Flug - Route wird nach der Landung neu berechnet", active = true } })
        return
    end
    local pos = playerNode()
    if not pos then return end

    if dist(pos, goal) < ARRIVE_DIST then
        stopRoute("Ziel erreicht!")
        return
    end

    if transit then
        local s = transit.step
        if dist(pos, s.to) < EXIT_DIST or GetTime() - transit.t0 > 600 then
            transit = nil
        else
            setRows("Route: " .. (goal.label or "Ziel"), "unterwegs", {
                { kind = s.kind, num = 1, text = stepText(s), right = "", active = true },
                { kind = "info", text = "Warte bzw. bleib an Bord bis " .. s.to.label },
            })
            setArrow(s.to, s.to.label)
            return
        end
    end

    local steps, total = plan(pos, goal)
    if not steps then
        route = nil; clearArrow(); drawMap()
        setRows("Route: " .. (goal.label or "Ziel"), "", {
            { kind = "info", text = "|cffff5555Keine Route gefunden|r" },
            { kind = "info", text = "Fehlt ein Flugpunkt/Boot? /route hilfe" } })
        return
    end
    -- winzige Laufschritte vorne weglassen
    while #steps > 1 and steps[1].kind == "walk" and pathLen(steps[1]) < BOARD_DIST do
        table.remove(steps, 1)
    end
    local navPending = refine(steps)
    route = steps

    local first = steps[1]
    if (first.kind == "boat" or first.kind == "zeppelin" or first.kind == "tram" or first.kind == "portal")
        and dist(pos, first.from) < BOARD_DIST then
        transit = { step = first, t0 = GetTime() }
    end

    if first.kind == "walk" then
        local target = first.to
        for j = 2, #first.path do
            if dist(pos, first.path[j]) > 20 then target = first.path[j]; break end
        end
        if ensureMapPos(target) then setArrow(target, first.to.label) end
    elseif first.kind == "teleport" then
        clearArrow()
    else
        setArrow(first.from, first.from.label)
    end

    local list = {}
    for i, st in ipairs(steps) do
        local right
        if st.kind == "walk" then
            right = (hasOffroad(st) and (navPending and "..." or "|cffff5555?|r") or yards(pathLen(st, i == 1 and pos or nil)))
        else
            right = "~" .. mins(st.cost)
        end
        list[#list + 1] = { kind = st.kind, num = i, text = stepText(st), right = right, active = (i == 1) }
    end
    list[#list + 1] = { kind = "goal", text = "Ziel: " .. (goal.label or "?"), right = "" }
    if not cdb.seen then
        list[#list + 1] = { kind = "flight", text = "Tipp: Öffne einmal die Flugkarte bei einem Flugmeister" }
    end
    if navPending then
        list[#list + 1] = { kind = "info", text = "Berechne Weg um Berge/Wasser ..." }
    else
        for _, st in ipairs(steps) do
            if hasOffroad(st) then
                list[#list + 1] = { kind = "info", text = "|cffff5555Für einen Abschnitt wurde kein begehbarer Weg gefunden|r" }
                break
            end
        end
    end
    setRows("Route: " .. (goal.label or "Ziel"), "ca. " .. mins(total), list)
    if WorldMapFrame and WorldMapFrame:IsShown() then drawMap() end
end

local function setGoal(n)
    if not n then msg("Ungültiges Ziel."); return end
    goal, transit = n, nil
    db.hidden = false
    msg("Neues Ziel: " .. (n.label or "?"))
    safeCall(update)
end

local function goalFromUserWaypoint()
    if not (C_Map.HasUserWaypoint and C_Map.HasUserWaypoint()) then return false end
    local wp = C_Map.GetUserWaypoint()
    if not wp then return false end
    local mi = C_Map.GetMapInfo(wp.uiMapID)
    setGoal(makeNode(wp.uiMapID, wp.position.x, wp.position.y,
        string.format("%s %.0f, %.0f", mi and mi.name or "Karte", wp.position.x * 100, wp.position.y * 100)))
    return true
end

---------------------------------------------------------------------------
-- Suche auf der Weltkarte
---------------------------------------------------------------------------
local function mapName(m)
    local i = m and C_Map.GetMapInfo(m)
    return i and i.name or "?"
end

local function searchPlaces(q, out)
    for _, cont in ipairs({ 1414, 1415 }) do
        for _, info in ipairs(C_Map.GetMapChildrenInfo(cont, nil, true) or {}) do
            if info.name and info.name:lower():find(q, 1, true)
                and info.mapType and info.mapType >= Enum.UIMapType.Zone then
                out[#out + 1] = { text = info.name, sub = "Gebiet", mapID = info.mapID, x = 0.5, y = 0.5, prio = 1 }
            end
        end
        if C_TaxiMap and C_TaxiMap.GetTaxiNodesForMap then
            local ok, nodes = pcall(C_TaxiMap.GetTaxiNodesForMap, cont)
            if ok and nodes then
                for _, n in ipairs(nodes) do
                    local myF = UnitFactionGroup("player") == "Horde" and 1 or 2
                    local fOk = not n.faction or n.faction == 0 or n.faction == myF
                    if fOk and n.name and n.position and n.name:lower():find(q, 1, true) then
                        local known = cdb.flights[n.nodeID] ~= nil
                        out[#out + 1] = { text = n.name, sub = known and "Flugpunkt" or "Flugpunkt - noch nicht entdeckt", mapID = cont,
                            x = n.position.x, y = n.position.y, prio = 2 }
                    end
                end
            end
        end
    end
    local faction = UnitFactionGroup("player")
    for _, t in ipairs(ns.transports) do
        for _, side in ipairs((not t.faction or t.faction == faction) and { "a", "b" } or {}) do
            local d = t[side]
            if d[4]:lower():find(q, 1, true) then
                out[#out + 1] = { text = d[4], sub = ns.kindText[t.kind], mapID = d[1], x = d[2] / 100, y = d[3] / 100, prio = 2 }
            end
        end
    end
end

-- nächster Fundort aus einer Questie-Spawnliste {[areaId] = {{x,y},...}}
local function nearestSpawn(ZDB, spawns, pos, label)
    if not spawns then return end
    local bm, bx, by, bd
    for areaId, list in pairs(spawns) do
        local m = ZDB:GetUiMapIdByAreaId(areaId)
        if m and type(list) == "table" then
            for _, c in ipairs(list) do
                if c[1] and c[1] > 0 then
                    local x, y = c[1] / 100, c[2] / 100
                    local d = math.huge
                    if pos then
                        local cont, wp = C_Map.GetWorldPosFromMapPos(m, CreateVector2D(x, y))
                        if cont == pos.cont and wp then
                            d = math.sqrt((wp.x - pos.wx) ^ 2 + (wp.y - pos.wy) ^ 2)
                        end
                    end
                    if not bm or d < bd then bm, bx, by, bd = m, x, y, d end
                end
            end
        end
    end
    if bm then return makeNode(bm, bx, by, label), bd end
end

local function questie()
    if not QuestieLoader then return end
    local ok, QDB = pcall(QuestieLoader.ImportModule, QuestieLoader, "QuestieDB")
    local ok2, ZDB = pcall(QuestieLoader.ImportModule, QuestieLoader, "ZoneDB")
    if not (ok and ok2 and QDB and ZDB) then return end
    local ok3, L = pcall(QuestieLoader.ImportModule, QuestieLoader, "l10n")
    return QDB, ZDB, ok3 and L or nil
end

local function inQuestLog(id)
    if C_QuestLog and C_QuestLog.GetLogIndexForQuestID then
        return C_QuestLog.GetLogIndexForQuestID(id) ~= nil
    elseif GetQuestLogIndexByID then
        return (GetQuestLogIndexByID(id) or 0) > 0
    end
end

local function searchNPCs(q, out, pos)
    local QDB, ZDB, L = questie()
    if not QDB then return end

    -- Dungeons (Eingänge)
    local okD, dungeons = pcall(ZDB.GetDungeons, ZDB)
    if okD and type(dungeons) == "table" then
        for _, d in pairs(dungeons) do
            local en = d[1]
            local loc = en
            if L then local okL, t = pcall(L, en); if okL and type(t) == "string" then loc = t end end
            if en and (loc:lower():find(q, 1, true) or en:lower():find(q, 1, true)) and type(d[4]) == "table" then
                local spawns = {}
                for _, e in ipairs(d[4]) do
                    spawns[e[1]] = spawns[e[1]] or {}
                    table.insert(spawns[e[1]], { e[2], e[3] })
                end
                local n, dd = nearestSpawn(ZDB, spawns, pos, loc)
                if n then out[#out + 1] = { text = loc, sub = "Dungeon-Eingang", node = n, d = dd, prio = 2 } end
            end
        end
    end

    -- Quests: Annahme, Abgabe und (wenn im Questlog) Questziele
    if QDB.QuestPointers and QDB.QueryQuestSingle then
        local nq = 0
        for id in pairs(QDB.QuestPointers) do
            local name = QDB.QueryQuestSingle(id, "name")
            if name and name:lower():find(q, 1, true) then
                nq = nq + 1
                if nq > 25 then break end
                local lvl = QDB.QueryQuestSingle(id, "questLevel")
                local title = name .. (lvl and lvl > 0 and (" [" .. lvl .. "]") or "")
                local active = inQuestLog(id)
                local function addFrom(list, role)
                    if type(list) ~= "table" then return end
                    for _, npcId in ipairs(list[1] or {}) do
                        local nn = QDB.QueryNPCSingle(npcId, "name")
                        local n, dd = nearestSpawn(ZDB, QDB.QueryNPCSingle(npcId, "spawns"), pos, nn or title)
                        if n then out[#out + 1] = { text = title, sub = role .. ": " .. (nn or "?"), node = n, d = dd, prio = 3 }; return end
                    end
                    for _, objId in ipairs(list[2] or {}) do
                        local on = QDB.QueryObjectSingle and QDB.QueryObjectSingle(objId, "name")
                        local n, dd = nearestSpawn(ZDB, QDB.QueryObjectSingle and QDB.QueryObjectSingle(objId, "spawns"), pos, on or title)
                        if n then out[#out + 1] = { text = title, sub = role .. ": " .. (on or "Objekt"), node = n, d = dd, prio = 3 }; return end
                    end
                end
                if not active then addFrom(QDB.QueryQuestSingle(id, "startedBy"), "Annahme") end
                addFrom(QDB.QueryQuestSingle(id, "finishedBy"), "Abgabe")
                if active then
                    local obj = QDB.QueryQuestSingle(id, "objectives")
                    if type(obj) == "table" then
                        for _, c in ipairs(obj[1] or {}) do
                            local nn = QDB.QueryNPCSingle(c[1], "name")
                            local n, dd = nearestSpawn(ZDB, QDB.QueryNPCSingle(c[1], "spawns"), pos, nn or title)
                            if n then out[#out + 1] = { text = title, sub = "Questziel: " .. (nn or "?"), node = n, d = dd, prio = 3 } end
                        end
                        for _, c in ipairs(obj[2] or {}) do
                            local on = QDB.QueryObjectSingle and QDB.QueryObjectSingle(c[1], "name")
                            local n, dd = nearestSpawn(ZDB, QDB.QueryObjectSingle and QDB.QueryObjectSingle(c[1], "spawns"), pos, on or title)
                            if n then out[#out + 1] = { text = title, sub = "Questziel: " .. (on or "Objekt"), node = n, d = dd, prio = 3 } end
                        end
                    end
                end
            end
        end
    end

    -- NPCs (Name oder Titel)
    if QDB.NPCPointers and QDB.QueryNPCSingle then
        local found = 0
        for id in pairs(QDB.NPCPointers) do
            local name = QDB.QueryNPCSingle(id, "name")
            local sub = QDB.QueryNPCSingle(id, "subName")
            if (name and name:lower():find(q, 1, true)) or (sub and sub:lower():find(q, 1, true)) then
                local n, dd = nearestSpawn(ZDB, QDB.QueryNPCSingle(id, "spawns"), pos, name)
                if n then
                    out[#out + 1] = { text = name or "?", sub = sub, node = n, d = dd, prio = 4 }
                    found = found + 1
                    if found >= 40 then break end
                end
            end
        end
    end

    -- Objekte (Briefkasten, Erzadern, Questobjekte ...)
    if QDB.ObjectPointers and QDB.QueryObjectSingle then
        local found = 0
        for id in pairs(QDB.ObjectPointers) do
            local name = QDB.QueryObjectSingle(id, "name")
            if name and name:lower():find(q, 1, true) then
                local n, dd = nearestSpawn(ZDB, QDB.QueryObjectSingle(id, "spawns"), pos, name)
                if n then
                    out[#out + 1] = { text = name, sub = "Objekt", node = n, d = dd, prio = 5 }
                    found = found + 1
                    if found >= 20 then break end
                end
            end
        end
    end
end

local search = {}

local function runSearch(text)
    local q = strtrim(text or ""):lower()
    local out = {}
    if #q >= 2 then
        local pos = playerNode()
        searchPlaces(q, out)
        if #q >= 3 then safeCall(searchNPCs, q, out, pos) end
        table.sort(out, function(a, b)
            if a.prio ~= b.prio then return a.prio < b.prio end
            return (a.d or 0) < (b.d or 0)
        end)
    end
    search.results = out
    search.show(out)
end

local function pickResult(r)
    if not r then return end
    local n = r.node or makeNode(r.mapID, r.x, r.y, r.text)
    if not n then msg("Für diesen Treffer gibt es keine Koordinaten."); return end
    n.label = r.text
    setGoal(n)
    if WorldMapFrame and WorldMapFrame.SetMapID then WorldMapFrame:SetMapID(n.mapID) end
    search.box:ClearFocus()
    search.list:Hide()
end

function buildSearchUI()
    if search.box or not WorldMapFrame or not WorldMapFrame.ScrollContainer then return end
    local parent = WorldMapFrame.ScrollContainer
    local ok, box = pcall(CreateFrame, "EditBox", "RoutePlanerSearchBox", parent, "SearchBoxTemplate")
    if not ok then box = CreateFrame("EditBox", "RoutePlanerSearchBox", parent, "InputBoxTemplate") end
    box:SetSize(240, 22)
    box:SetPoint("TOPLEFT", parent, "TOPLEFT", 14, -10)
    box:SetFrameLevel(parent:GetFrameLevel() + 2500)
    box:SetAutoFocus(false)
    if box.Instructions then box.Instructions:SetText("Ort, Dungeon, Quest, NPC ...") end

    local list = CreateFrame("Frame", nil, box, "BackdropTemplate")
    list:SetPoint("TOPLEFT", box, "BOTTOMLEFT", -6, -2)
    list:SetWidth(380)
    list:SetBackdrop({
        bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
        tile = true, tileSize = 16, edgeSize = 14,
        insets = { left = 3, right = 3, top = 3, bottom = 3 },
    })
    list:SetBackdropColor(0, 0, 0, 0.9)
    list:Hide()
    list.buttons = {}

    search.box, search.list = box, list
    search.show = function(out)
        local shown = math.min(#out, 20)
        for i = 1, shown do
            local b = list.buttons[i]
            if not b then
                b = CreateFrame("Button", nil, list)
                b:SetSize(368, 18)
                b:SetPoint("TOPLEFT", 6, -6 - (i - 1) * 18)
                b:SetHighlightTexture("Interface\\QuestFrame\\UI-QuestTitleHighlight", "ADD")
                b.text = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
                b.text:SetPoint("LEFT", 4, 0); b.text:SetPoint("RIGHT", -4, 0)
                b.text:SetJustifyH("LEFT"); b.text:SetWordWrap(false)
                b:SetScript("OnClick", function(self) pickResult(self.result) end)
                list.buttons[i] = b
            end
            local r = out[i]
            local zone = mapName(r.node and r.node.mapID or r.mapID)
            local extra = r.sub and r.sub ~= "" and (" |cffaaaaaa<" .. r.sub .. ">|r") or ""
            local where = (r.prio == 1) and "" or (" |cff888888- " .. zone .. "|r")
            if r.d and r.d < math.huge then where = where .. string.format(" |cff888888(%d m)|r", math.floor(r.d)) end
            b.text:SetText(r.text .. extra .. where)
            b.result = r
            b:Show()
        end
        for i = shown + 1, #list.buttons do list.buttons[i]:Hide() end
        if shown == 0 then
            list:SetShown(#strtrim(box:GetText() or "") >= 2)
            list:SetHeight(30)
            if not list.empty then
                list.empty = list:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
                list.empty:SetPoint("TOPLEFT", 10, -9)
                list.empty:SetText("Keine Treffer")
            end
            list.empty:Show()
        else
            if list.empty then list.empty:Hide() end
            list:SetHeight(shown * 18 + 12)
            list:Show()
        end
    end

    local timer
    box:HookScript("OnTextChanged", function(self, userInput)
        if not userInput then return end
        if timer then timer:Cancel() end
        timer = C_Timer.NewTimer(0.3, function() safeCall(runSearch, self:GetText()) end)
    end)
    box:HookScript("OnEnterPressed", function()
        if search.results and search.results[1] then pickResult(search.results[1]) end
    end)
    box:HookScript("OnEscapePressed", function(self) self:ClearFocus(); list:Hide() end)
    WorldMapFrame:HookScript("OnHide", function() box:ClearFocus(); list:Hide() end)
end

---------------------------------------------------------------------------
-- Flugpunkte merken
---------------------------------------------------------------------------
local function recordFlights()
    if not (C_TaxiMap and C_TaxiMap.GetAllTaxiNodes and GetTaxiMapID) then return end
    local m = GetTaxiMapID()
    if not m then return end
    local ok, nodes = pcall(C_TaxiMap.GetAllTaxiNodes, m)
    if not ok or not nodes then return end
    local unreachable = Enum.FlightPathState and Enum.FlightPathState.Unreachable or 2
    local n = 0
    for _, node in ipairs(nodes) do
        if node.state ~= unreachable and node.position then
            cdb.flights[node.nodeID] = { m = m, x = node.position.x, y = node.position.y, name = node.name }
            n = n + 1
        end
    end
    cdb.seen = true
end

---------------------------------------------------------------------------
-- Slash-Befehle
---------------------------------------------------------------------------
local function findZone(name)
    name = name:lower()
    local found
    for _, cont in ipairs({ 1414, 1415 }) do
        local kids = C_Map.GetMapChildrenInfo(cont, nil, true) or {}
        for _, info in ipairs(kids) do
            local n = info.name:lower()
            if n == name then return info.mapID end
            if not found and n:sub(1, #name) == name then found = info.mapID end
        end
    end
    return found
end

local function help()
    msg("Befehle:")
    print("  |cffffff00Strg+Klick auf die Weltkarte|r - Kartenmarkierung setzen = Route dorthin")
    print("  |cffffff00/route|r - Route zur Kartenmarkierung")
    print("  |cffffff00/route 45,2 60,1|r - Ziel in der aktuellen Zone")
    print("  |cffffff00/route Darnassus 40 50|r - Ziel in einer bestimmten Zone")
    print("  |cffffff00Suchfeld oben links auf der Weltkarte|r - Ort/Stadt/NPC suchen, Klick = Route")
    print("  |cffffff00/route suche Angellehrer|r - Karte öffnen und suchen")
    print("  |cffffff00/route stop|r - Route beenden")
    print("  |cffffff00/route stufen|r - Stufenbereiche der Zonen auf der Karte ein/aus")
    print("  |cffffff00/route wege [an|aus|löschen]|r - selbst gelaufene Wege (zusätzlich zur Kartenauswertung)")
    print("  |cffffff00/route fenster|r - Schritt-Fenster ein/aus")
    print("  |cffffff00/route tempo 7|r - Laufgeschwindigkeit (7 = zu Fuß, 14 = Reittier)")
    print("  |cffffff00/route punkte|r - Boote/Portale auflisten")
    print("  |cffffff00/route punkt <key:a|b>|r - Punkt auf deine aktuelle Position korrigieren")
    print("  Flugpunkte lernt das Addon automatisch, wenn du die Flugkarte beim Flugmeister öffnest.")
end

SLASH_ROUTEPLANER1 = "/route"
SLASH_ROUTEPLANER2 = "/rp"
SlashCmdList.ROUTEPLANER = function(input)
    input = strtrim(input or "")
    local cmd, rest = input:match("^(%S*)%s*(.-)$")
    cmd = (cmd or ""):lower()

    if cmd == "" then
        if not goalFromUserWaypoint() then help() end
    elseif cmd == "hilfe" or cmd == "help" then
        help()
    elseif cmd == "suche" then
        initMap()
        if not WorldMapFrame:IsShown() then ToggleWorldMap() end
        if search.box then search.box:SetText(rest); search.box:SetFocus(); runSearch(rest) end
    elseif cmd == "wege" then
        local sub = rest:lower()
        if sub == "aus" then db.record = false; msg("Wege-Aufzeichnung aus.")
        elseif sub == "an" then db.record = true; msg("Wege-Aufzeichnung an.")
        elseif sub == "löschen" or sub == "loeschen" then
            db.roads = { pts = {}, adj = {} }; initRoads(); msg("Gelernte Wege gelöscht.")
        else
            msg(string.format("%d Wegpunkte gelernt, Aufzeichnung %s.", #roads.pts, db.record == false and "aus" or "an"))
        end
    elseif cmd == "stufen" then
        db.levels = (db.levels == false)
        msg("Stufen auf der Karte: " .. (db.levels and "an" or "aus"))
        safeCall(drawLevels)
    elseif cmd == "stop" then
        stopRoute("Route beendet.")
    elseif cmd == "fenster" or cmd == "liste" then
        db.hidden = frame:IsShown()
        if db.hidden then frame:Hide() elseif goal then frame:Show(); update() end
    elseif cmd == "tempo" then
        local v = tonumber((rest:gsub(",", ".")))
        if v and v > 0 then db.speed = v; msg("Geschwindigkeit: " .. v); update() else msg("Tempo: " .. (db.speed or 7)) end
    elseif cmd == "punkte" then
        for _, t in ipairs(ns.transports) do
            print(string.format("  %s:a = %s   |   %s:b = %s%s", t.key, t.a[4], t.key, t.b[4],
                (t.faction and t.faction ~= UnitFactionGroup("player")) and "  |cff888888(andere Fraktion)|r" or ""))
        end
        local n = 0
        for _ in pairs(cdb.flights) do n = n + 1 end
        print("  Bekannte Flugpunkte: " .. n)
    elseif cmd == "punkt" then
        local key, side = rest:match("^(%S+):([ab])$")
        local pos = playerNode()
        if not key or not pos then msg("Benutzung: /route punkt <key>:a|b  (Liste: /route punkte)"); return end
        local okKey = false
        for _, t in ipairs(ns.transports) do if t.key == key then okKey = true end end
        if not okKey then msg("Unbekannter Punkt: " .. key); return end
        db.fix[key .. ":" .. side] = { pos.mapID, pos.x, pos.y }
        msg(string.format("%s:%s gespeichert (%.1f, %.1f).", key, side, pos.x * 100, pos.y * 100))
        update()
    else
        -- Koordinaten: "/route 45,2 60,1" oder "/route Zone 45 60"
        local a, b = input:match("^([%d%.,]+)%s+([%d%.,]+)$")
        local zoneID
        if not a then
            local zn
            zn, a, b = input:match("^(.-)%s+([%d%.,]+)%s+([%d%.,]+)$")
            if zn then zoneID = findZone(zn) end
            if zn and not zoneID then msg("Zone nicht gefunden: " .. zn); return end
        else
            zoneID = C_Map.GetBestMapForUnit("player")
        end
        local x, y = tonumber((a or ""):gsub(",", ".") .. ""), tonumber((b or ""):gsub(",", ".") .. "")
        if not zoneID or not x or not y then help(); return end
        local mi = C_Map.GetMapInfo(zoneID)
        setGoal(makeNode(zoneID, x / 100, y / 100, string.format("%s %.0f, %.0f", mi and mi.name or "?", x, y)))
    end
end

---------------------------------------------------------------------------
-- Events
---------------------------------------------------------------------------
local ev = CreateFrame("Frame")
ev:RegisterEvent("ADDON_LOADED")
ev:RegisterEvent("TAXIMAP_OPENED")
ev:RegisterEvent("USER_WAYPOINT_UPDATED")
ev:RegisterEvent("PLAYER_ENTERING_WORLD")
ev:SetScript("OnEvent", function(_, e, arg)
    if e == "ADDON_LOADED" and arg == "Blizzard_WorldMap" then
        initMap()
    elseif e == "ADDON_LOADED" and arg == ADDON then
        RoutePlanerDB = RoutePlanerDB or {}
        db = RoutePlanerDB
        db.flights = nil   -- alt: accountweit, jetzt pro Charakter
        RoutePlanerChar = RoutePlanerChar or {}
        cdb = RoutePlanerChar
        cdb.flights = cdb.flights or {}
        db.fix = db.fix or {}
        db.speed = db.speed or 7
        if db.size then frame:SetSize(db.size[1], db.size[2]) end
        updateLock()
        if db.pos then
            frame:ClearAllPoints()
            frame:SetPoint(db.pos[1], UIParent, db.pos[2], db.pos[3], db.pos[4])
        else
            frame:SetPoint("TOPRIGHT", UIParent, "TOPRIGHT", -220, -240)
        end
        initRoads()
        C_Timer.NewTicker(UPDATE_INTERVAL, function() safeCall(update) end)
        C_Timer.NewTicker(1, function() safeCall(recordStep) end)
        initMap()
    elseif e == "TAXIMAP_OPENED" then
        recordFlights()
    elseif e == "USER_WAYPOINT_UPDATED" then
        if db and db.autoPin ~= false then goalFromUserWaypoint() end
    elseif e == "PLAYER_ENTERING_WORLD" then
        initMap()
        if goal then update() end
    end
end)
