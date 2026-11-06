local _, ns = ...

-- Wegsuche auf dem Navigationsraster (NavData.lua): A* über Frames verteilt
local Nav = {}
ns.Nav = Nav

local COST = { [97] = nil, [98] = 1, [99] = 6, [100] = 4 }   -- a=gesperrt b=frei c=Berg d=Wasser
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
    return COST[g.rows[r]:byte(c)]
end

local function toCell(g, wx, wy)
    return math.floor((g.xmax - wx) / g.cell) + 1, math.floor((g.ymax - wy) / g.cell) + 1
end

local function toWorld(g, r, c)
    return g.xmax - (r - 0.5) * g.cell, g.ymax - (c - 0.5) * g.cell
end

local function snap(g, r, c)
    if cost(g, r, c) then return r, c end
    for rad = 1, 10 do
        for dr = -rad, rad do
            for dc = -rad, rad do
                if math.abs(dr) == rad or math.abs(dc) == rad then
                    if cost(g, r + dr, c + dc) then return r + dr, c + dc end
                end
            end
        end
    end
end

-- Sichtlinie: nur über freie Zellen (oder Zellen, die nicht teurer sind als erlaubt)
local function los(g, r1, c1, r2, c2, maxc)
    local dr, dc = math.abs(r2 - r1), math.abs(c2 - c1)
    local sr, sc = r1 < r2 and 1 or -1, c1 < c2 and 1 or -1
    local err = dr - dc
    local r, c = r1, c1
    while true do
        local k = cost(g, r, c)
        if not k or k > maxc then return false end
        if r == r2 and c == c2 then return true end
        local e2 = 2 * err
        if e2 > -dc then err = err - dc; r = r + sr end
        if e2 < dr then err = err + dr; c = c + sc end
    end
end

local function smooth(g, cells)
    local out = { cells[1] }
    local i = 1
    while i < #cells do
        local a = cells[i]
        local maxc = math.max(1, cost(g, a[1], a[2]) or 1)
        local best = i + 1
        local limit = math.min(#cells, i + 80)
        for j = i + 2, limit do
            local b = cells[j]
            if los(g, a[1], a[2], b[1], b[2], maxc) then best = j end
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
    local function h(r, c)
        local dr, dc = math.abs(r - r2), math.abs(c - c2)
        return (dr + dc) + (SQ2 - 2) * math.min(dr, dc)
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
        table.insert(cells, 1, { math.floor((id - 1) / W) + 1, (id - 1) % W + 1 })
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

function Nav.has(cont) return ns.nav and ns.nav[cont] ~= nil end

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
    r, c = snap(g, r, c)
    if not r then return nil end
    local b = cr[r]:byte(c)
    if not b or b == 97 then return nil end
    return b - 97
end
