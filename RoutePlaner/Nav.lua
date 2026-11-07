local _, ns = ...

-- Wegsuche auf dem Navigationsraster (NavData.lua): A* über Frames verteilt
local Nav = {}
ns.Nav = Nav

-- a=gesperrt b=frei c=Berg d=Wasser e=Straße/Weg/Brücke (aus den ADT-Geländetexturen, NavData v2)
local ROAD = 0.6          -- Straßen bevorzugen: reine Straßenumwege bis ~1/0.6 (Zubringer abgezogen meist 30-40 %)
local COST = { [97] = nil, [98] = 1, [99] = 6, [100] = 4, [101] = ROAD }
local HMIN = ROAD         -- kleinste Zellkosten -> Heuristik bleibt zulässig (kürzester Weg exakt)
-- Gelernte Wege: selbst gelaufene Zellen sind begehbar (auch wo das Raster gesperrt/Berg/Wasser sagt)
-- und stark bevorzugt. Gleiche Regeln in der Webkarte (src/nav.js), Daten via Begleitprogramm.
local LEARN = 0.4
local LEARN_SEG = 80     -- längere Sprünge zwischen zwei Wegpunkten nicht rastern (Teleport, Ladebildschirm)
local LEARN_MAX = 200000 -- Zellen je Kontinent
local SQ2 = math.sqrt(2)
local decoded = {}

local function grid(cont)
    local g = decoded[cont]
    if g then return g end
    local d = ns.nav and ns.nav[cont]
    if not d then return end
    local rows = {}
    for i, r in ipairs(d.rows) do
        local parts = {}
        for ch, n in r:gmatch("(%a)(%d+)") do parts[#parts + 1] = string.rep(ch, tonumber(n)) end
        rows[i] = table.concat(parts)
    end
    g = { rows = rows, w = d.w, h = d.h, xmax = d.xmax, ymax = d.ymax, cell = d.cell }
    decoded[cont] = g
    Nav.applyLearned(cont)
    return g
end

local compDecoded = {}
local function compGrid(cont)
    local g = compDecoded[cont]
    if g then return g end
    local d = ns.nav and ns.nav[cont]
    if not d or not d.comp then return end
    local rows = {}
    for i, r in ipairs(d.comp) do
        local parts = {}
        for ch, n in r:gmatch("(%a)(%d+)") do parts[#parts + 1] = string.rep(ch, tonumber(n)) end
        rows[i] = table.concat(parts)
    end
    compDecoded[cont] = rows
    return rows
end

local function cost(g, r, c)
    if r < 1 or c < 1 or r > g.h or c > g.w then return nil end
    local L = g.learned
    if L and L[(r - 1) * g.w + c] then return LEARN end
    return COST[g.rows[r]:byte(c)]
end

-- Rasterklasse ohne gelernte Wege (für zusammenhängende Bereiche)
local function staticCost(g, r, c)
    if r < 1 or c < 1 or r > g.h or c > g.w then return nil end
    return COST[g.rows[r]:byte(c)]
end

local function toCell(g, wx, wy)
    return math.floor((g.xmax - wx) / g.cell) + 1, math.floor((g.ymax - wy) / g.cell) + 1
end

local function toWorld(g, r, c)
    return g.xmax - (r - 0.5) * g.cell, g.ymax - (c - 0.5) * g.cell
end

local function snap(g, r, c, fn)
    fn = fn or cost
    if fn(g, r, c) then return r, c end
    for rad = 1, 10 do
        for dr = -rad, rad do
            for dc = -rad, rad do
                if math.abs(dr) == rad or math.abs(dc) == rad then
                    if fn(g, r + dr, c + dc) then return r + dr, c + dc end
                end
            end
        end
    end
end

-- kleinste Zellkosten dieses Rasters (Heuristik/Glätten)
local function hmin(g) return g.learned and math.min(LEARN, HMIN) or HMIN end

-- Sichtlinie: nur über Zellen, die nicht teurer sind als erlaubt; liefert auch die Kosten der Linie
local function los(g, r1, c1, r2, c2, maxc)
    local dr, dc = math.abs(r2 - r1), math.abs(c2 - c1)
    local sr, sc = r1 < r2 and 1 or -1, c1 < c2 and 1 or -1
    local err = dr - dc
    local r, c = r1, c1
    local sum = 0
    while true do
        local k = cost(g, r, c)
        if not k or k > maxc then return false end
        if r == r2 and c == c2 then return true, sum end
        local e2 = 2 * err
        local mr, mc = false, false
        if e2 > -dc then err = err - dc; r = r + sr; mr = true end
        if e2 < dr then err = err + dr; c = c + sc; mc = true end
        local k2 = cost(g, r, c)
        if not k2 then return false end
        sum = sum + k2 * ((mr and mc) and SQ2 or 1)
    end
end

-- Glätten: Abkürzung nur, wenn die Sichtlinie nicht teurer ist als der A*-Weg (sonst würde sie
-- Straßenstücke abschneiden); ohne Straßen (HMIN = 1) wie bisher nur die Sichtlinien-Regel
local function smooth(g, cells)
    local HM = hmin(g)
    local out = { cells[1] }
    local i = 1
    while i < #cells do
        local a = cells[i]
        -- Sichtlinie nur über Zellen, die nicht teurer sind als die Startzelle (auf der Straße: nur Straße)
        local maxc = cost(g, a[1], a[2]) or 1
        local best = i + 1
        local limit = math.min(#cells, i + 80)
        for j = i + 2, limit do
            local b = cells[j]
            local ok, lc = los(g, a[1], a[2], b[1], b[2], maxc)
            if ok and (HM >= 1 or lc <= b[3] - a[3] + 1e-9) then best = j end
        end
        out[#out + 1] = cells[best]
        i = best
    end
    return out
end

-- A* als Coroutine
local function astar(cont, ax, ay, bx, by)
    local g = grid(cont)
    if not g then return nil end
    local r1, c1 = toCell(g, ax, ay)
    local r2, c2 = toCell(g, bx, by)
    r1, c1 = snap(g, r1, c1)
    r2, c2 = snap(g, r2, c2)
    if not r1 or not r2 then return nil end
    local W = g.w
    local start, goalId = (r1 - 1) * W + c1, (r2 - 1) * W + c2
    local gs, came, closed = { [start] = 0 }, {}, {}
    local heap = { { 0, start } }
    local function push(f, id)
        heap[#heap + 1] = { f, id }
        local i = #heap
        while i > 1 do
            local p = math.floor(i / 2)
            if heap[p][1] <= heap[i][1] then break end
            heap[p], heap[i] = heap[i], heap[p]; i = p
        end
    end
    local function pop()
        local top = heap[1]
        local last = table.remove(heap)
        if #heap > 0 then
            heap[1] = last
            local i = 1
            while true do
                local l, rr, m = 2 * i, 2 * i + 1, i
                if l <= #heap and heap[l][1] < heap[m][1] then m = l end
                if rr <= #heap and heap[rr][1] < heap[m][1] then m = rr end
                if m == i then break end
                heap[m], heap[i] = heap[i], heap[m]; i = m
            end
        end
        return top
    end
    local HM = hmin(g)
    local function h(r, c)
        local dr, dc = math.abs(r - r2), math.abs(c - c2)
        return ((dr + dc) + (SQ2 - 2) * math.min(dr, dc)) * HM
    end
    local n = 0
    while #heap > 0 do
        local t = pop()
        local id = t[2]
        if id == goalId then break end
        if not closed[id] then
            closed[id] = true
            local r, c = math.floor((id - 1) / W) + 1, (id - 1) % W + 1
            local gid = gs[id]
            for dr = -1, 1 do
                for dc = -1, 1 do
                    if dr ~= 0 or dc ~= 0 then
                        local nr, nc = r + dr, c + dc
                        local k = cost(g, nr, nc)
                        if k and (dr == 0 or dc == 0 or (cost(g, r + dr, c) and cost(g, r, c + dc))) then
                            local nid = (nr - 1) * W + nc
                            local ng = gid + k * ((dr ~= 0 and dc ~= 0) and SQ2 or 1)
                            if ng < (gs[nid] or math.huge) then
                                gs[nid] = ng; came[nid] = id
                                push(ng + h(nr, nc), nid)
                            end
                        end
                    end
                end
            end
            n = n + 1
            if n % 2000 == 0 then coroutine.yield() end
            if n > 600000 then return nil end
        end
    end
    if not gs[goalId] then return nil end
    local cells, id = {}, goalId
    while id do
        table.insert(cells, 1, { math.floor((id - 1) / W) + 1, (id - 1) % W + 1, gs[id] })
        id = came[id]
    end
    cells = smooth(g, cells)
    local pts = {}
    for i, rc in ipairs(cells) do
        local wx, wy = toWorld(g, rc[1], rc[2])
        pts[i] = { wx, wy }
    end
    -- exakte Endpunkte
    pts[1] = { ax, ay }; pts[#pts] = { bx, by }
    return pts
end

-- Jobs + Cache
local cache, jobs = {}, {}
local runner = CreateFrame("Frame")
runner:Hide()
runner:SetScript("OnUpdate", function(self)
    local t0 = debugprofilestop()
    while jobs[1] and debugprofilestop() - t0 < 6 do
        local j = jobs[1]
        local ok, res = coroutine.resume(j.co)
        if not ok then
            cache[j.key] = { status = "fail", err = res }
            table.remove(jobs, 1)
        elseif coroutine.status(j.co) == "dead" then
            cache[j.key] = res and { status = "ok", pts = res } or { status = "fail" }
            table.remove(jobs, 1)
            if j.cb then j.cb() end
        end
    end
    if not jobs[1] then self:Hide() end
end)

local function key(cont, ax, ay, bx, by)
    return string.format("%d:%d:%d:%d:%d", cont, math.floor(ax / 30), math.floor(ay / 30), math.floor(bx / 30), math.floor(by / 30))
end

local function clearAll()
    for k in pairs(cache) do cache[k] = nil end
    for i = #jobs, 1, -1 do jobs[i] = nil end
end

--- Straßen bevorzugen (an: Straße kostet ROAD, aus: wie freies Gelände; Brücken bleiben begehbar)
function Nav.setRoads(on)
    local c = on and ROAD or 1
    if COST[101] ~= c then COST[101] = c; HMIN = on and ROAD or 1; clearAll() end
end
function Nav.roads() return COST[101] ~= 1 end

function Nav.has(cont) return ns.nav and ns.nav[cont] ~= nil end

---------------------------------------------------------------------------
-- Gelernte Wege ins Raster (Linie zwischen zwei verbundenen Wegpunkten, Bresenham)
---------------------------------------------------------------------------
local learnedSrc   -- { pts = {{cont, wx, wy, land}, ...}, adj = {[i] = {j, ...}} } (RoutePlanerDB.roads)

local function markLine(g, ax, ay, bx, by)
    local L = g.learned
    if not L then L = { n = 0 }; g.learned = L end
    local r1, c1 = toCell(g, ax, ay)
    local r2, c2 = toCell(g, bx, by)
    local dx, dy = ax - bx, ay - by
    if dx * dx + dy * dy > LEARN_SEG * LEARN_SEG then r2, c2 = r1, c1 end
    local dr, dc = math.abs(r2 - r1), math.abs(c2 - c1)
    local sr, sc = r1 < r2 and 1 or -1, c1 < c2 and 1 or -1
    local err = dr - dc
    local r, c = r1, c1
    while true do
        if r >= 1 and c >= 1 and r <= g.h and c <= g.w and L.n < LEARN_MAX then
            local id = (r - 1) * g.w + c
            if not L[id] then L[id] = true; L.n = L.n + 1 end
        end
        if r == r2 and c == c2 then break end
        local e2 = 2 * err
        local mr, mc = false, false
        if e2 > -dc then err = err - dc; r = r + sr; mr = true end
        if e2 < dr then err = err + dr; c = c + sc; mc = true end
        -- diagonale Schritte 4-verbunden machen (A* verbietet Eckenschneiden)
        if mr and mc and r >= 1 and c - sc >= 1 and r <= g.h and c - sc <= g.w and L.n < LEARN_MAX then
            local id = (r - 1) * g.w + (c - sc)
            if not L[id] then L[id] = true; L.n = L.n + 1 end
        end
    end
end

-- alle gelernten Wege eines Kontinents ins (bereits dekodierte) Raster übernehmen
function Nav.applyLearned(cont)
    local g = decoded[cont]
    if not g then return end
    g.learned = nil
    local src = learnedSrc
    if not src or not src.pts then return end
    local pts, adj = src.pts, src.adj or {}
    for i, p in ipairs(pts) do
        if p[1] == cont then
            markLine(g, p[2], p[3], p[2], p[3])
            for _, j in ipairs(adj[i] or {}) do
                local q = pts[j]
                if j > i and q and q[1] == cont then markLine(g, p[2], p[3], q[2], q[3]) end
            end
        end
    end
end

--- Gelernte Wege setzen (RoutePlanerDB.roads); Raster werden beim Dekodieren bzw. sofort aktualisiert
function Nav.setLearned(src)
    learnedSrc = src
    for cont in pairs(decoded) do Nav.applyLearned(cont) end
    clearAll()
end

--- neuer gelernter Abschnitt während des Spielens (wirkt auf neue Berechnungen, Cache bleibt)
function Nav.learnSegment(cont, ax, ay, bx, by)
    local g = decoded[cont]
    if g then markLine(g, ax, ay, bx, by) end
end

--- Anzahl gelernter Rasterzellen (für /route wege)
function Nav.learnedCells(cont)
    local g = grid(cont)
    return g and g.learned and g.learned.n or 0
end

-- liefert pts (Liste {wx,wy}) | nil, status
function Nav.find(cont, ax, ay, bx, by, cb)
    if not Nav.has(cont) then return nil, "nodata" end
    local k = key(cont, ax, ay, bx, by)
    local c = cache[k]
    if c then return c.pts, c.status end
    for _, j in ipairs(jobs) do if j.key == k then return nil, "pending" end end
    jobs[#jobs + 1] = { key = k, cb = cb, co = coroutine.create(function() return astar(cont, ax, ay, bx, by) end) }
    runner:Show()
    return nil, "pending"
end

function Nav.busy() return jobs[1] ~= nil end

-- Zusammenhängender Bereich (Komponente) an einer Weltposition, nil = außerhalb
function Nav.comp(cont, wx, wy)
    local g = grid(cont)
    local cr = compGrid(cont)
    if not g or not cr then return nil end
    local r, c = toCell(g, wx, wy)
    r, c = snap(g, r, c, staticCost)
    if not r then return nil end
    local b = cr[r]:byte(c)
    if not b or b == 97 then return nil end
    return b - 97
end
