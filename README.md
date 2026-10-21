# POB-TESTER

Quick DPS and effective hit pool (EHP) check for Path of Exile 2 builds, without opening Path of Building.

Paste a Mobalytics build link (or a pobb.in / poe.ninja / pastebin link, or a raw PoB code). The script runs the build through **your installed Path of Building (PoE2)** with no window and prints the numbers PoB would show, for every loadout in the build:

```
== Demon Mommy Temu Temporalis Arc Comet (INSANE TECH)  [High Budget] ==
  Infernalist lvl 96   main skill: Demon Form
  DPS      main skill 0   best skill 529.3K (Arc  (Cast on Critical, Arc))
           avg hit 0   0.04/s   crit 0.0% x4.74
  EHP      17.0K   life 1,753   ES 6,095   mana 406   spirit 284
  Max hit  phys 8,990   fire 40.4K   cold 31.7K   light 31.7K   chaos 6,708
  Res      81/75/75  chaos 18   armour 1,048   evasion 18   block 0%

Summary
  build                                                  DPS       EHP    life      ES
  Demon Mommy ... [Low Budget]                        204.2K     13.8K   1,683   5,595
  Demon Mommy ... [Medium Budget]                     397.6K     17.7K   1,755   6,616
  Demon Mommy ... [High Budget]                       529.3K     17.0K   1,753   6,095
```

"Main skill" is whatever the author set as main in PoB. Often that's a buff like Demon Form, so the script also tries every skill as the main skill and shows the **best skill**.

## How it gets the build

- **Mobalytics:** the build pages are behind a Cloudflare check, so the script asks Mobalytics' own data API instead. If the guide has a PoB code (usually a pobb.in link), that's used, and each PoB loadout (e.g. Low / Medium / High Budget) is tested.
- **Guides with no PoB code:** the script tests the in-game Build Planner file Mobalytics generates for each variant. That file has no gem levels, item quality or character level, so it assumes level 90 (change with `--level`), gems at their normal max level and default item rolls. Treat those numbers as rough.
- pobb.in, poe.ninja and pastebin links, raw codes, and `.xml` / `.build` files work too.

## Requirements

- Windows with **Path of Building Community (PoE2)** installed (it is found automatically in `%APPDATA%`).
- **64-bit Python 3.9+**. No extra packages needed. `run.bat` finds `python`, the `py` launcher, or a Python installed by uv.

## Usage

```
python pob_tester.py https://mobalytics.gg/poe-2/builds/some-build
python pob_tester.py --skills https://pobb.in/abc123
python pob_tester.py build.xml
run.bat <link>           (same thing, double-click friendly)
```

Several builds can be passed at once. PoB loads once (about 4 seconds); each loadout then takes a few seconds, mostly for trying every skill.

| Option | What it does |
| --- | --- |
| `--skills` | List the DPS of every skill, as if each one were the main skill |
| `--fast` | Skip trying every skill (faster, but no "best skill") |
| `--level N` | Character level for guides with no PoB code (default 90) |
| `--json` | Print raw results as JSON |
| `--pob-dir PATH` | Use a PoB install somewhere other than `%APPDATA%` (or set `POB_DIR`) |
| `--save-xml DIR` | Save each build as a PoB XML file you can open in PoB |
| `--verbose` | Show PoB's own log output |

## Notes

- Numbers depend on the build author's PoB config (enemy type, charges, buffs and so on), exactly like in PoB.
- Nothing is written to your PoB folder or PoB settings.
- On Linux/macOS you can point `--lua-lib` at a LuaJIT shared library (v2.1 from July 2026 or newer) and `--pob-dir` at a PathOfBuilding-PoE2 git checkout.

`lua/simplegraphic_stubs.lua` is adapted from Path of Building Community (PoE2), MIT licensed; see `THIRD_PARTY_LICENSE.md`.
