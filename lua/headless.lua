-- Runs Path of Building (PoE2) without a window and reports DPS / defence numbers.
--
-- pob_tester.py loads this file into PoB's own LuaJIT (lua51.dll on Windows) after
-- changing the working directory to the PoB install folder, and sets these globals:
--   POBTESTER_LUA_DIR   folder holding this file
--   POBTESTER_POB_DIR   PoB install / src folder (also the working directory)
--   POBTESTER_USER_DIR  scratch folder PoB may treat as its settings folder
-- It then calls pobtester_init() once and pobtester_eval(xml, wantSkills) per build.
-- Both return JSON strings.

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

function pobtester_eval(xmlText, wantSkills)
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

	local result = { ok = true }
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
		-- DPS for every enabled skill group, as if each one were the main skill
		result.skills = { }
		local original = build.mainSocketGroup
		for index, group in ipairs(build.skillsTab.socketGroupList) do
			if group.enabled ~= false and group.displaySkillList and #group.displaySkillList > 0 then
				build.mainSocketGroup = index
				recalc()
				local o = build.calcsTab.mainOutput
				local s = pick(o)
				result.skills[#result.skills + 1] = {
					index = index,
					name = groupLabel(group),
					dps = s.CombinedDPS or s.TotalDPS,
					hitDPS = s.TotalDPS,
					dotDPS = s.TotalDot,
					averageHit = s.AverageHit,
					speed = s.Speed,
				}
			end
		end
		build.mainSocketGroup = original
		recalc()
	end

	return dkjson.encode(result)
end

function pobtester_log()
	return dkjson.encode(logLines)
end
