-- Build editing on top of headless.lua: load a build once, then change items, gems,
-- passives and jewels and read the recalculated numbers after each change.
-- Every pobtester_edit_* / pobtester_load call returns the full state as JSON.

local P = POBTESTER

local history = { }   -- build XML before each edit, for undo
local baselineXml     -- the build as loaded, for reset
local loadNotes = { }

local function J(t)
	return P.json().encode(t)
end

local function fail(msg)
	return J({ ok = false, error = msg })
end

local function stripColours(s)
	return (s:gsub("%^x%x%x%x%x%x%x", ""):gsub("%^%d", ""))
end

local function modLines(item, list)
	local out = { }
	for _, modLine in ipairs(item[list] or { }) do
		local ok, text = pcall(itemLib.formatModLine, modLine)
		if ok and text then
			out[#out + 1] = stripColours(text)
		end
	end
	return out
end

local function itemInfo(item)
	if not item then
		return nil
	end
	local ok, raw = pcall(item.BuildRaw, item)
	return {
		id = item.id,
		name = item.title or item.name,
		base = item.baseName,
		rarity = item.rarity,
		type = item.type,
		enchants = modLines(item, "enchantModLines"),
		runes = modLines(item, "runeModLines"),
		implicits = modLines(item, "implicitModLines"),
		explicits = modLines(item, "explicitModLines"),
		raw = ok and raw or nil,
	}
end

local function mainSkillName()
	local group = build.skillsTab.socketGroupList[build.mainSocketGroup]
	if not group then
		return nil
	end
	local active = group.displaySkillList and group.displaySkillList[group.mainActiveSkill or 1]
	local effect = active and active.activeEffect and active.activeEffect.grantedEffect
	return effect and effect.name or P.groupLabel(group)
end

local function points()
	local used, ascUsed, _, sockets, ws1, ws2 = build.spec:CountAllocNodes()
	local o = build.calcsTab.mainOutput or { }
	return {
		used = used - math.min(ws1 or 0, ws2 or 0),
		max = 99 + (build.maxWeaponSets or 0) + (o.ExtraPoints or 0),
		ascendancy = ascUsed,
		ascendancyMax = 8,
		weaponSet1 = ws1,
		weaponSet2 = ws2,
	}
end

local function state()
	local spec = build.spec
	local s = {
		ok = true,
		className = spec.curClassName,
		ascendancy = spec.curAscendClassName ~= "None" and spec.curAscendClassName or nil,
		level = build.characterLevel,
		mainSkill = mainSkillName(),
		mainSocketGroup = build.mainSocketGroup,
		stats = P.pick(build.calcsTab.mainOutput),
		canUndo = #history > 0,
		notes = loadNotes,
		points = points(),
	}

	s.loadouts = { }
	for _, l in ipairs(P.listLoadouts()) do
		local lo = l.loadout
		s.loadouts[#s.loadouts + 1] = {
			name = l.name,
			active = lo.specId == build.treeTab.activeSpec and lo.itemSetId == build.itemsTab.activeItemSetId
				and lo.skillSetId == build.skillsTab.activeSkillSetId,
		}
	end

	-- Gear (jewel sockets are listed separately)
	s.items = { }
	for _, slot in ipairs(build.itemsTab.orderedSlots) do
		if not slot.nodeId and slot.slotName and not slot.slotName:match("^Jewel") then
			local shown = not slot.IsShown or slot:IsShown()
			local item = build.itemsTab.items[slot.selItemId]
			if shown or item then
				s.items[#s.items + 1] = { slot = slot.slotName, item = itemInfo(item) }
			end
		end
	end

	-- Jewel sockets on the tree
	s.jewels = { }
	for nodeId, slot in pairs(build.itemsTab.sockets) do
		local node = spec.nodes[nodeId]
		if node then
			local allocated = spec.allocNodes[nodeId] ~= nil
			local item = build.itemsTab.items[slot.selItemId]
			if allocated or item then
				s.jewels[#s.jewels + 1] = {
					node = nodeId,
					name = node.dn,
					allocated = allocated,
					item = itemInfo(item),
				}
			end
		end
	end
	table.sort(s.jewels, function(a, b) return a.node < b.node end)

	-- Skills
	s.groups = { }
	for gi, group in ipairs(build.skillsTab.socketGroupList) do
		local g = {
			index = gi,
			label = P.groupLabel(group),
			enabled = group.enabled ~= false,
			slot = group.slot,
			source = group.source and tostring(group.source) or nil,
			mainActive = group.mainActiveSkill or 1,
			gems = { },
			actives = { },
		}
		for i, gem in ipairs(group.gemList or { }) do
			g.gems[#g.gems + 1] = {
				index = i,
				name = gem.nameSpec,
				level = gem.level,
				quality = gem.quality,
				enabled = gem.enabled ~= false,
				support = gem.gemData and gem.gemData.grantedEffect and gem.gemData.grantedEffect.support or false,
				maxLevel = gem.gemData and gem.gemData.naturalMaxLevel,
				error = gem.errMsg,
			}
		end
		for ai, active in ipairs(group.displaySkillList or { }) do
			local effect = active.activeEffect and active.activeEffect.grantedEffect
			g.actives[#g.actives + 1] = { index = ai, name = effect and effect.name or "?" }
		end
		s.groups[#s.groups + 1] = g
	end

	-- Passives worth listing by name
	s.passives = { }
	local smallCount = 0
	for id, node in pairs(spec.allocNodes) do
		if node.type == "Notable" or node.type == "Keystone" or node.ascendancyName and node.type ~= "AscendClassStart" then
			s.passives[#s.passives + 1] = {
				id = id,
				name = node.dn,
				type = node.ascendancyName and "Ascendancy" or node.type,
				stats = node.sd,
			}
		elseif node.type == "Normal" then
			smallCount = smallCount + 1
		end
	end
	table.sort(s.passives, function(a, b)
		if a.type ~= b.type then
			return a.type < b.type
		end
		return (a.name or "") < (b.name or "")
	end)
	s.smallPassives = smallCount
	return s
end

local function snapshot()
	history[#history + 1] = build:SaveDB("pob-tester")
	if #history > 50 then
		table.remove(history, 1)
	end
end

local function done(extra)
	P.recalc()
	local s = state()
	if extra then
		for k, v in pairs(extra) do
			s[k] = v
		end
	end
	return J(s)
end

-- Run an edit with an undo snapshot; if it fails, roll back to the snapshot.
local function edit(fn)
	snapshot()
	local ok, res = pcall(fn)
	if not ok or type(res) == "string" then
		local xml = table.remove(history)
		if not ok then
			P.startBuild(xml)
		end
		return fail(ok and res or ("edit failed: " .. tostring(res)))
	end
	return done(res)
end

function pobtester_load(kind, data, levelText)
	history = { }
	loadNotes = { }
	local err
	if kind == "game" then
		local _, _, warnings
		err, _, warnings = P.loadGameBuild(data, levelText)
		for _, w in ipairs(warnings or { }) do
			loadNotes[#loadNotes + 1] = w
		end
		if not err then
			loadNotes[#loadNotes + 1] = "No PoB code in this guide: rebuilt from the game's build file (level "
				.. (tonumber(levelText) or 90) .. ", gems at max level, no quality)"
		end
	else
		err = P.startBuild(data)
	end
	if err then
		return err
	end
	-- If the author's main skill does no damage (e.g. a buff), switch to the strongest skill
	local o = build.calcsTab.mainOutput
	if kind == "game" or not ((o.CombinedDPS or 0) > 0 or (o.FullDPS or 0) > 0) then
		local before = mainSkillName()
		local _, best = P.useBestSkill()
		if best and best.dps > 0 and kind ~= "game" then
			loadNotes[#loadNotes + 1] = "Main skill " .. tostring(before) .. " does no damage, so "
				.. tostring(best.name) .. " is used as main skill"
		end
	end
	baselineXml = build:SaveDB("pob-tester")
	return J(state())
end

function pobtester_state()
	return J(state())
end

function pobtester_undo()
	local xml = table.remove(history)
	if not xml then
		return fail("nothing to undo")
	end
	local err = P.startBuild(xml)
	if err then
		return err
	end
	return done()
end

function pobtester_reset()
	if not baselineXml then
		return fail("no build loaded")
	end
	history = { }
	local err = P.startBuild(baselineXml)
	if err then
		return err
	end
	return done()
end

function pobtester_export()
	return J({ ok = true, xml = build:SaveDB("pob-tester") })
end

-- Every skill's DPS as main skill (slower: one recalculation per skill)
function pobtester_skills()
	return J({ ok = true, skills = P.skillTable() })
end

-- ---------------------------------------------------------------------------
-- Edits. Each takes a JSON string of arguments.
-- ---------------------------------------------------------------------------

local function args(text)
	local a = P.json().decode(text or "{}")
	return type(a) == "table" and a or { }
end

local function equipItem(slotName, raw, isJewel)
	local item = P.makeItem(raw)
	if not item or not item.base then
		return "PoB couldn't read that item. Copy it in game with Ctrl+C (or Ctrl+Alt+C) and paste the whole text."
	end
	local slot = isJewel and build.itemsTab.sockets[slotName] or build.itemsTab.slots[slotName]
	if not slot then
		return "unknown slot " .. tostring(slotName)
	end
	local ok, valid = pcall(build.itemsTab.IsItemValidForSlot, build.itemsTab, item, slot.slotName)
	if ok and valid == false then
		return (item.name or "That item") .. " can't go in " .. slot.slotName
	end
	item:BuildModList()
	build.itemsTab:AddItem(item, true)
	slot:SetSelItemId(item.id)
	build.itemsTab:PopulateSlots()
	build.itemsTab:AddUndoState()
end

function pobtester_edit_equip(text)
	local a = args(text)
	return edit(function()
		return equipItem(a.slot, a.raw or "", false)
	end)
end

function pobtester_edit_unequip(text)
	local a = args(text)
	return edit(function()
		local slot = build.itemsTab.slots[a.slot]
		if not slot then
			return "unknown slot " .. tostring(a.slot)
		end
		slot:SetSelItemId(0)
		build.itemsTab:PopulateSlots()
	end)
end

function pobtester_edit_jewel(text)
	local a = args(text)
	local nodeId = tonumber(a.node)
	return edit(function()
		if not build.spec.allocNodes[nodeId] then
			return "allocate that jewel socket on the tree first"
		end
		if a.raw and a.raw:match("%S") then
			return equipItem(nodeId, a.raw, true)
		end
		local slot = build.itemsTab.sockets[nodeId]
		if slot then
			slot:SetSelItemId(0)
		end
	end)
end

local function getGroup(index)
	return build.skillsTab.socketGroupList[tonumber(index) or 0]
end

local function processGroup(group)
	build.skillsTab:ProcessSocketGroup(group)
	build.skillsTab:AddUndoState()
end

function pobtester_edit_gem(text)
	local a = args(text)
	return edit(function()
		local group = getGroup(a.group)
		local gem = group and group.gemList[tonumber(a.gem) or 0]
		if not gem then
			return "no such gem"
		end
		if a.level then
			gem.level = math.max(1, math.floor(tonumber(a.level) or gem.level))
		end
		if a.quality then
			gem.quality = math.max(0, math.floor(tonumber(a.quality) or gem.quality))
		end
		if a.enabled ~= nil then
			gem.enabled = a.enabled and true or false
		end
		processGroup(group)
	end)
end

function pobtester_edit_add_gem(text)
	local a = args(text)
	return edit(function()
		local errMsg, gemData = build.skillsTab:FindSkillGem(a.name or "")
		if not gemData then
			return errMsg or ("no gem called " .. tostring(a.name))
		end
		local group = getGroup(a.group)
		if not group then
			group = { label = "", enabled = true, gemList = { } }
			table.insert(build.skillsTab.socketGroupList, group)
		end
		table.insert(group.gemList, {
			nameSpec = gemData.name,
			gemId = gemData.id,
			level = tonumber(a.level) or gemData.naturalMaxLevel or 20,
			quality = tonumber(a.quality) or 0,
			enabled = true,
			count = 1,
			enableGlobal1 = true,
			enableGlobal2 = true,
		})
		processGroup(group)
	end)
end

function pobtester_edit_remove_gem(text)
	local a = args(text)
	return edit(function()
		local group = getGroup(a.group)
		if not group or not group.gemList[tonumber(a.gem) or 0] then
			return "no such gem"
		end
		table.remove(group.gemList, tonumber(a.gem))
		if #group.gemList == 0 then
			table.remove(build.skillsTab.socketGroupList, tonumber(a.group))
			if build.mainSocketGroup > #build.skillsTab.socketGroupList then
				build.mainSocketGroup = math.max(1, #build.skillsTab.socketGroupList)
			end
		else
			processGroup(group)
		end
	end)
end

function pobtester_edit_group(text)
	local a = args(text)
	return edit(function()
		local group = getGroup(a.group)
		if not group then
			return "no such skill group"
		end
		if a.enabled ~= nil then
			group.enabled = a.enabled and true or false
		end
		if a.main then
			build.mainSocketGroup = tonumber(a.group)
			group.mainActiveSkill = tonumber(a.active) or group.mainActiveSkill or 1
		end
		processGroup(group)
	end)
end

function pobtester_edit_level(text)
	local a = args(text)
	return edit(function()
		local level = tonumber(a.level)
		if not level or level < 1 or level > 100 then
			return "level must be 1 to 100"
		end
		build.characterLevel = math.floor(level)
		build.characterLevelAutoMode = false
		build.configTab:BuildModList()
	end)
end

function pobtester_edit_loadout(text)
	local a = args(text)
	return edit(function()
		for _, l in ipairs(P.listLoadouts()) do
			if l.name == a.name then
				build:SetActiveLoadout(l.loadout)
				P.recalc()
				-- Skill sets differ per loadout, so the main skill may now be a buff
				local o = build.calcsTab.mainOutput
				if not ((o.CombinedDPS or 0) > 0 or (o.FullDPS or 0) > 0) then
					P.useBestSkill()
				end
				return
			end
		end
		return "no loadout called " .. tostring(a.name)
	end)
end

-- Passive tree

local function nodeSummary(node)
	return {
		id = node.id,
		name = node.dn,
		type = node.ascendancyName and "Ascendancy" or node.type,
		ascendancy = node.ascendancyName,
		stats = node.sd,
		allocated = build.spec.allocNodes[node.id] ~= nil,
		-- points needed to reach it from the current tree (including itself)
		cost = node.alloc and 0 or (node.path and #node.path or nil),
	}
end

local searchable = { Normal = true, Notable = true, Keystone = true, Socket = true }

function pobtester_search_nodes(text)
	local a = args(text)
	local q = (a.query or ""):lower()
	if #q < 2 then
		return J({ ok = true, nodes = { } })
	end
	local hits = { }
	for _, node in pairs(build.spec.nodes) do
		local t = node.type
		if node.dn and searchable[t]
			and (not node.ascendancyName or node.ascendancyName == build.spec.curAscendClassName) then
			local hay = node.dn:lower()
			local inName = hay:find(q, 1, true)
			local inStats = false
			if not inName and a.stats then
				for _, line in ipairs(node.sd or { }) do
					if line:lower():find(q, 1, true) then
						inStats = true
						break
					end
				end
			end
			if inName or inStats then
				hits[#hits + 1] = { node = node, score = (inName and 0 or 1) + (t == "Normal" and 2 or 0) }
			end
		end
	end
	table.sort(hits, function(x, y)
		if x.score ~= y.score then
			return x.score < y.score
		end
		local cx, cy = x.node.path and #x.node.path or 999, y.node.path and #y.node.path or 999
		if cx ~= cy then
			return cx < cy
		end
		return x.node.dn < y.node.dn
	end)
	local out = { }
	for i = 1, math.min(#hits, tonumber(a.limit) or 40) do
		out[i] = nodeSummary(hits[i].node)
	end
	return J({ ok = true, nodes = out })
end

function pobtester_edit_alloc(text)
	local a = args(text)
	return edit(function()
		local node = build.spec.nodes[tonumber(a.node) or 0]
		if not node then
			return "no such passive"
		end
		if node.alloc then
			return
		end
		local before = build.spec:CountAllocNodes()
		build.spec:AllocNode(node)
		if not node.alloc then
			return "PoB can't path to " .. tostring(node.dn) .. " from your tree"
		end
		build.spec:AddUndoState()
		return { changed = build.spec:CountAllocNodes() - before }
	end)
end

function pobtester_edit_dealloc(text)
	local a = args(text)
	return edit(function()
		local node = build.spec.nodes[tonumber(a.node) or 0]
		if not node or not node.alloc then
			return "that passive isn't allocated"
		end
		local before = build.spec:CountAllocNodes()
		build.spec:DeallocNode(node)
		build.spec:AddUndoState()
		return { changed = build.spec:CountAllocNodes() - before }
	end)
end

-- Names for the "add gem" box
function pobtester_gem_names()
	local names, seen = { }, { }
	for _, gem in pairs(data.gems) do
		if gem.name and not seen[gem.name] then
			seen[gem.name] = true
			local support = gem.grantedEffect and gem.grantedEffect.support or false
			names[#names + 1] = { name = gem.name, support = support }
		end
	end
	table.sort(names, function(a, b) return a.name < b.name end)
	return J({ ok = true, gems = names })
end
