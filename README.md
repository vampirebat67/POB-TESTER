# POB-TESTER

Quick DPS and effective hit pool (EHP) check for Path of Exile 2 builds, without opening Path of Building.

Paste a Mobalytics build link (or a pobb.in / poe.ninja / pastebin link, or a raw PoB code). The script finds the build's Path of Building code, runs it through **your installed Path of Building (PoE2)** with no window, and prints the numbers PoB would show:

```
== embedded code ==
  Stormweaver lvl 92   main skill: Arc
  DPS      Full DPS 1.23M   Main skill DPS 812.4K
           avg hit 34.5K   4.21/s   crit 45.2% x2.90
  EHP      45.6K   life 1,234   ES 5,678   mana 1,100   spirit 100
  Max hit  phys 9.8K   fire 21.0K   cold 21.0K   light 21.0K   chaos 6.1K
  Res      75/75/75  chaos -12   armour 0   evasion 2,300   block 0%
```

Because it uses PoB's own calculation engine, the numbers match what PoB shows for the same build and config.

## Requirements

- Windows with **Path of Building Community (PoE2)** installed (it is found automatically in `%APPDATA%`).
- **64-bit Python 3.9+**. No extra packages needed.

## Usage

```
python pob_tester.py https://mobalytics.gg/poe-2/builds/some-build
python pob_tester.py --skills https://pobb.in/abc123
python pob_tester.py build.xml
run.bat <link>           (same thing, double-click friendly)
```

Several builds can be passed at once; PoB loads once (about 5 seconds) and each build after that is quick.

| Option | What it does |
| --- | --- |
| `--skills` | Also show the DPS of every skill group, as if each one were the main skill |
| `--json` | Print raw results as JSON |
| `--pob-dir PATH` | Use a PoB install somewhere other than `%APPDATA%` (or set `POB_DIR`) |
| `--save-html FILE` | Save the downloaded web page, for debugging link parsing |
| `--save-xml DIR` | Save each build's PoB XML |
| `--verbose` | Show PoB's own log output |

## Notes

- If a Mobalytics build has several variants (leveling, endgame...), every PoB code found on the page is tested.
- Numbers depend on the build author's PoB config (enemy type, charges, buffs and so on), exactly like in PoB.
- Nothing is written to your PoB folder or PoB settings.
- On Linux/macOS you can point `--lua-lib` at a LuaJIT shared library (v2.1 from July 2026 or newer) and `--pob-dir` at a PathOfBuilding-PoE2 git checkout.

`lua/simplegraphic_stubs.lua` is adapted from Path of Building Community (PoE2), MIT licensed; see `THIRD_PARTY_LICENSE.md`.
