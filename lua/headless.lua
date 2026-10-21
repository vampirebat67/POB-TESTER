-- Runs Path of Building (PoE2) without a window and reports DPS / defence numbers.
--
-- pob_tester.py loads this file into PoB's own LuaJIT (lua51.dll on Windows) after
-- changing the working directory to the PoB install folder, and sets these globals:
--   POBTESTER_LUA_DIR   folder holding this file
--   POBTESTER_POB_DIR   PoB install / src folder (also the working directory)
--   POBTESTER_USER_DIR  scratch folder PoB may treat as its settings folder
-- It then calls pobtester_init() once, then pobtester_eval(xml, wantSkills) per PoB build or
-- pobtester_eval_game_build(json, wantSkills, level) per in-game .build file. All return JSON strings.

local LUA_DIR = POBTESTER_LUA_DIR
local POB_DIR = POBTESTER_POB_DIR
local USER_DIR = POBTESTER_USER_DIR

-- PoB prints a lot while loading. Keep it out of the report but hold on to it for --verbose.
local logLines = { }
local realPrint = print
print = function(...)
	local parts = { }
	for i = 1, select("#", ...) do
		parts[i] = tostring((select(i, ...)))
	end
	logLines[#logLines + 1] = table.concat(parts, "\t")
end

package.path = POB_DIR .. "/lua/?.lua;" .. POB_DIR .. "/lua/?/init.lua;"
	.. POB_DIR .. "/../runtime/lua/?.lua;" .. POB_DIR .. "/../runtime/lua/?/init.lua;" .. package.path
package.cpath = POB_DIR .. "/?.dll;" .. POB_DIR .. "/../runtime/?.dll;" .. package.cpath

-- lua-utf8 ships as a DLL with PoB on Windows. Elsewhere (or if it fails to load) fall back
-- to plain string functions, which is enough for headless number formatting.
do
	local ok, mod = pcall(require, "lua-utf8")
	if not ok then
		package.loaded["lua-utf8"] = nil
		package.preload["lua-utf8"] = function()
			local u = { }
			for k, v in pairs(string) do
				u[k] = v
			end
			u.next = function(s, i, d)
				i = (i or 0) + (d or 1)
				if i < 1 or i > #s then
					return nil
				end
				return i
			end
			return u
		end
	end
end

dofile(LUA_DIR .. "/simplegraphic_stubs.lua")

function GetVirtualScreenSize()
	return 1920, 1080
end
function GetScriptPath()
	return POB_DIR
end
function GetRuntimePath()
	return POB_DIR
end
-- Point PoB's settings folder somewhere harmless so the real PoB settings are never touched.
function GetUserPath()
	return USER_DIR
end

__callbackTable__ = { }
function runCallback(name, ...)
	if __callbackTable__[name] then
		return __callbackTable__[name](...)
	elseif __mainObject__ and __mainObject__[name] then
		return __mainObject__[name](__mainObject__, ...)
	end
end

local l_require = require
function require(name)
	-- PoB only needs lcurl for downloads, which we never do from inside PoB
	if name == "lcurl.safe" then
		return
	end
	return l_require(name)
end

local dkjson

local function lastLog(n)
	local out = { }
	for i = math.max(1, #logLines - n + 1), #logLines do
		out[#out + 1] = logLines[i]
	end
	return out
end

function pobtester_init()
	-- PoB reads command-line args; an embedded Lua state has none
	arg = arg or { }
	dofile("Launch.lua")
	-- No update checks from a script
	launch.CheckForUpdate = function() end
	__mainObject__.continuousIntegrationMode = false

	runCallback("OnInit")
	runCallback("OnFrame")
	dkjson = require("dkjson")
	if __mainObject__.promptMsg then
		return dkjson.encode({ ok = false, error = tostring(__mainObject__.promptMsg), log = lastLog(20) })
	end
	return dkjson.encode({ ok = true, version = launch.versionNumber })
end

local headlineKeys = {
	"FullDPS", "FullDotDPS", "CombinedDPS", "TotalDPS", "TotalDot", "AverageHit", "AverageDamage",
	"Speed", "HitSpeed", "CritChance", "CritMultiplier", "HitChance",
	"Life", "LifeUnreserved", "EnergyShield", "Mana", "ManaUnreserved", "Spirit", "SpiritUnreserved",
	"Armour", "Evasion", "EvadeChance", "BlockChance", "SpellBlockChance",
	"PhysicalDamageReduction",
	"TotalEHP", "PhysicalMaximumHitTaken", "FireMaximumHitTaken", "ColdMaximumHitTaken",
	"LightningMaximumHitTaken", "ChaosMaximumHitTaken",
	"FireResist", "ColdResist", "LightningResist", "ChaosResist",
	"FireResistOverCap", "ColdResistOverCap", "LightningResistOverCap", "ChaosResistOverCap",
	"LifeRegenRecovery", "EnergyShieldRegenRecovery", "ManaRegenRecovery",
	"EffectiveMovementSpeedMod",
}

local function pick(output)
	local t = { }
	for _, k in ipairs(headlineKeys) do
		local v = output[k]
		if type(v) == "number" and v == v and v ~= math.huge and v ~= -math.huge then
			t[k] = v
		end
	end
	return t
end

local function groupLabel(group)
	if not group then
		return nil
	end
	return group.displayLabel or group.label
end

local function recalc()
	build.buildFlag = true
	runCallback("OnFrame")
end

local function startBuild(xmlText)
	logLines = { }
	__mainObject__.promptMsg = nil
	if GlobalCache and GlobalCache.cachedData then
		wipeGlobalCache()
	end
	__mainObject__.main:SetMode("BUILD", false, "pob-tester", xmlText)
	runCallback("OnFrame")
	build = __mainObject__.main.modes["BUILD"]
	if __mainObject__.promptMsg then
		return dkjson.encode({ ok = false, error = tostring(__mainObject__.promptMsg), log = lastLog(20) })
	end
end

-- DPS of every active skill in every enabled group, as if each one were the main skill.
-- Meta gems (Cast on Critical etc.) hold several active skills, so each one is tried.
local function skillTable()
	local skills = { }
	local origGroup = build.mainSocketGroup
	for index, group in ipairs(build.skillsTab.socketGroupList) do
		if group.enabled ~= false and group.displaySkillList and #group.displaySkillList > 0 then
			local origActive = group.mainActiveSkill
			local count = #group.displaySkillList
			for activeIndex = 1, count do
				local activeSkill = group.displaySkillList[activeIndex]
				local effect = activeSkill and activeSkill.activeEffect and activeSkill.activeEffect.grantedEffect
				build.mainSocketGroup = index
				group.mainActiveSkill = activeIndex
				recalc()
				local s = pick(build.calcsTab.mainOutput)
				local name = effect and effect.name or groupLabel(group)
				if count > 1 and groupLabel(group) and name ~= groupLabel(group) then
					name = name .. "  (" .. groupLabel(group) .. ")"
				end
				skills[#skills + 1] = {
					group = index,
					active = activeIndex,
					name = name,
					dps = s.CombinedDPS or s.TotalDPS or 0,
					hitDPS = s.TotalDPS,
					dotDPS = s.TotalDot,
					averageHit = s.AverageHit or s.AverageDamage,
					speed = s.Speed,
				}
			end
			group.mainActiveSkill = origActive
		end
	end
	build.mainSocketGroup = origGroup
	recalc()
	return skills
end

local function evaluateCurrent(wantSkills)
	local result = { }
	result.className = build.spec and build.spec.curClassName
	result.ascendancy = build.spec and build.spec.curAscendClassName
	if result.ascendancy == "None" then
		result.ascendancy = nil
	end
	result.level = build.characterLevel
	result.mainSocketGroup = build.mainSocketGroup
	result.mainSkill = groupLabel(build.skillsTab.socketGroupList[build.mainSocketGroup])
	result.stats = pick(build.calcsTab.mainOutput)

	local skillDPS = build.calcsTab.mainOutput.SkillDPS
	if type(skillDPS) == "table" then
		result.fullDPSSkills = { }
		for _, s in ipairs(skillDPS) do
			result.fullDPSSkills[#result.fullDPSSkills + 1] = { name = s.name, dps = s.dps, count = s.count, part = s.skillPart }
		end
	end

	if wantSkills then
		result.skills = skillTable()
		local best
		for _, sk in ipairs(result.skills) do
			if not best or sk.dps > best.dps then
				best = sk
			end
		end
		if best and best.dps > 0 then
			result.bestSkill = { name = best.name, dps = best.dps }
		end
	end
	return result
end

-- Loadouts are PoB's matching tree / item set / skill set triples (e.g. "Low Budget", "High Budget").
local function listLoadouts()
	local out = { }
	local ok = pcall(function()
		build:SyncLoadouts(true)
		for _, displayName in ipairs(build.controls.buildLoadouts.list) do
			if not displayName:find("^%^7%^7") then
				local loadout = build:GetLoadoutByName(displayName)
				if loadout and loadout.specId then
					out[#out + 1] = { name = displayName:gsub(" {.-}$", ""), loadout = loadout }
				end
			end
		end
	end)
	if not ok then
		return { }
	end
	return out
end

function pobtester_eval(xmlText, wantSkills)
	local err = startBuild(xmlText)
	if err then
		return err
	end
	local result = { ok = true, loadouts = { } }
	local loadouts = listLoadouts()
	if #loadouts <= 1 then
		local r = evaluateCurrent(wantSkills)
		r.loadout = loadouts[1] and loadouts[1].name
		result.loadouts[1] = r
	else
		for _, l in ipairs(loadouts) do
			build:SetActiveLoadout(l.loadout)
			recalc()
			local r = evaluateCurrent(wantSkills)
			r.loadout = l.name
			result.loadouts[#result.loadouts + 1] = r
		end
	end
	return dkjson.encode(result)
end

-- ---------------------------------------------------------------------------
-- Import of the game's BuildPlanner ".build" JSON (what Mobalytics exports when
-- a guide has no PoB code). That format has no gem levels, item rarities, quality
-- or character level, so those are filled in with reasonable defaults.
-- ---------------------------------------------------------------------------

local function inInterval(entry, level)
	local iv = entry.level_interval
	return not iv or ((iv[1] or 1) <= level and level <= (iv[2] or 100))
end

local function pickByLevel(entries, level)
	-- Among entries for the same slot, take the one meant for the highest level that still applies
	local best
	for _, e in ipairs(entries) do
		if inInterval(e, level) then
			local lo = e.level_interval and e.level_interval[1] or 1
			if not best or lo >= (best.level_interval and best.level_interval[1] or 1) then
				best = e
			end
		end
	end
	return best
end

local function slotNameFor(entry)
	local id, x = entry.inventory_id, entry.slot_x or 0
	if id == "Charm1" then
		return "Charm " .. (x + 1)
	end
	if id == "Flask1" then
		if x <= 1 then
			return "Flask " .. (x + 1)
		end
		return "Charm " .. (x - 1)
	end
	for pobName, m in pairs(data.buildFileInventorySlotMap) do
		if m.id == id and (m.slot_x or 0) == x then
			return pobName
		end
	end
end

local function numberless(line)
	return (line:gsub("%(%-?[%d%.]+%-%-?[%d%.]+%)", "#"):gsub("%-?[%d%.]+", "#"))
end

local function rareItemRaw(text)
	local lines = { }
	for line in text:gmatch("[^\r\n]+") do
		lines[#lines + 1] = line
	end
	local baseName = table.remove(lines, 1)
	local base = baseName and data.itemBases[baseName]
	if not base then
		return nil, baseName
	end
	local mods = { }
	for _, line in ipairs(lines) do
		mods[#mods + 1] = (line:gsub("^%s*%d+%.%s*", ""))
	end
	-- The export doesn't separate implicits. Use the base's implicit, taking the listed
	-- roll when one of the lines is that implicit.
	local implicits = { }
	if base.implicit then
		for imp in base.implicit:gmatch("[^\n]+") do
			local key = numberless(imp)
			local found
			for i, m in ipairs(mods) do
				if numberless(m) == key then
					found = table.remove(mods, i)
					break
				end
			end
			implicits[#implicits + 1] = found or imp
		end
	end
	local raw = { "Rarity: RARE", "Mobalytics Item", baseName, "Implicits: " .. #implicits }
	for _, l in ipairs(implicits) do
		raw[#raw + 1] = l
	end
	for _, l in ipairs(mods) do
		raw[#raw + 1] = l
	end
	return table.concat(raw, "\n")
end

local function findUnique(name)
	for key, item in pairs(main.uniqueDB.list) do
		if item.title == name or key:match("^(.-),") == name then
			return item
		end
	end
end

local gemByGameId
local function gemIdFor(gameId)
	if not gemByGameId then
		gemByGameId = { }
		for id, gem in pairs(data.gems) do
			if gem.gameId then
				gemByGameId[gem.gameId] = id
				gemByGameId[gem.gameId:gsub("/Gems?/", "/Gems/")] = gemByGameId[gem.gameId:gsub("/Gems?/", "/Gems/")] or id
			end
		end
	end
	return gemByGameId[gameId] or gemByGameId[(gameId:gsub("/Gems?/", "/Gems/"))]
end

local function gemInstance(gameId, warnings)
	local id = type(gameId) == "table" and gameId.id or gameId
	local gemId = id and gemIdFor(id)
	if not gemId then
		warnings[#warnings + 1] = "unknown gem " .. tostring(id)
		return nil
	end
	local gem = data.gems[gemId]
	return {
		gemId = gemId,
		nameSpec = gem.name,
		level = gem.naturalMaxLevel or 20,
		quality = 0,
		enabled = true,
		count = 1,
		enableGlobal1 = true,
		enableGlobal2 = true,
	}
end

function pobtester_eval_game_build(jsonText, wantSkills, levelText)
	local err = startBuild(nil)
	if err then
		return err
	end
	local level = tonumber(levelText) or 90
	local d = dkjson.decode(jsonText)
	if type(d) ~= "table" then
		return dkjson.encode({ ok = false, error = "could not read the build file" })
	end
	local warnings = { }

	-- Unique items are loaded in the background over a few frames
	for _ = 1, 200 do
		if not main.uniqueDB.loading then
			break
		end
		runCallback("OnFrame")
	end

	-- Passive tree
	local nodeByString = { }
	for id, node in pairs(build.spec.nodes) do
		if node.stringId then
			nodeByString[node.stringId] = id
		end
	end
	local hashList, weaponSets, missing = { }, { }, 0
	for _, p in ipairs(d.passives or { }) do
		local id = nodeByString[p.id]
		if id then
			hashList[#hashList + 1] = id
			if p.weapon_set then
				weaponSets[id] = p.weapon_set
			end
		else
			missing = missing + 1
		end
	end
	if missing > 0 then
		warnings[#warnings + 1] = missing .. " passive node(s) not found in PoB's tree"
	end
	build.spec:ImportFromNodeList(d.ascendancy, nil, nil, 0, hashList, weaponSets, { }, { })
	build.spec:AddUndoState()
	build.characterLevel = level
	build.characterLevelAutoMode = false

	-- Items
	local bySlot = { }
	for _, e in ipairs(d.inventory_slots or { }) do
		local slotName = slotNameFor(e)
		if slotName then
			bySlot[slotName] = bySlot[slotName] or { }
			table.insert(bySlot[slotName], e)
		end
	end
	for slotName, entries in pairs(bySlot) do
		local e = pickByLevel(entries, level)
		if e then
			local item
			if e.unique_name then
				local dbItem = findUnique(e.unique_name)
				if dbItem then
					item = new("Item"):Item(dbItem.raw)
				else
					warnings[#warnings + 1] = "unique not in PoB: " .. e.unique_name
				end
			elseif e.additional_text then
				local raw, badBase = rareItemRaw(e.additional_text)
				if raw then
					item = new("Item"):Item(raw)
				else
					warnings[#warnings + 1] = "unknown item base: " .. tostring(badBase)
				end
			end
			if item and item.base then
				item:NormaliseQuality()
				item:BuildModList()
				build.itemsTab:AddItem(item, true)
				if build.itemsTab.slots[slotName] then
					build.itemsTab.slots[slotName]:SetSelItemId(item.id)
				end
			end
		end
	end
	build.itemsTab:PopulateSlots()

	-- Skills: one socket group per skill, supports (and meta-gem payloads) in the same group
	for _, s in ipairs(d.skills or { }) do
		if inInterval(s, level) then
			local group = { label = "", enabled = true, gemList = { } }
			local activeGem = gemInstance(s, warnings)
			if activeGem then
				group.gemList[1] = activeGem
			end
			for _, sup in ipairs(s.support_skills or { }) do
				if type(sup) ~= "table" or inInterval(sup, level) then
					local g = gemInstance(sup, warnings)
					if g then
						group.gemList[#group.gemList + 1] = g
					end
				end
			end
			if #group.gemList > 0 then
				table.insert(build.skillsTab.socketGroupList, group)
				build.skillsTab:ProcessSocketGroup(group)
			end
		end
	end
	build.mainSocketGroup = 1
	recalc()

	local r = evaluateCurrent(true)
	-- There's no main skill in the export, so show the strongest one as main
	if r.skills and #r.skills > 0 then
		local best = r.skills[1]
		for _, sk in ipairs(r.skills) do
			if sk.dps > best.dps then
				best = sk
			end
		end
		build.mainSocketGroup = best.group
		build.skillsTab.socketGroupList[best.group].mainActiveSkill = best.active
		recalc()
		local skills = r.skills
		r = evaluateCurrent(false)
		r.skills, r.bestSkill = skills, { name = best.name, dps = best.dps }
		r.mainSkill = best.name
		r.mainSocketGroup = best.group
	end
	r.loadout = d.name
	r.assumed = "level " .. level .. ", gems at max natural level, no quality"
	r.warnings = warnings
	return dkjson.encode({ ok = true, loadouts = { r }, xml = build:SaveDB("pob-tester") })
end

function pobtester_log()
	return dkjson.encode(logLines)
end
