#!/usr/bin/env python3
"""Quick DPS / effective hit pool check for Path of Exile 2 builds, without opening PoB.

Give it a Mobalytics build link, a pobb.in / poe.ninja / pastebin link, a raw PoB code,
or a file containing one. It runs the build through your installed Path of Building
(PoE2) calculation engine with no window and prints the numbers PoB would show.

    python pob_tester.py https://mobalytics.gg/poe-2/builds/some-build
    python pob_tester.py --skills https://pobb.in/abc123
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
LUA_DIR = HERE / "lua"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Getting builds
# ---------------------------------------------------------------------------

# PoB codes are zlib data in URL-safe base64, so they start with "eN" (best compression) or "eJ".
CODE_RE = re.compile(r"e[NJ][A-Za-z0-9_\-+/]{200,}={0,2}")
POBBIN_RE = re.compile(r"pobb\.in/(?:u/[\w-]+/)?([\w-]{6,})", re.I)
NINJA_RE = re.compile(r"poe\.ninja/(poe2/)?pob/(?:raw/)?([\w-]{4,})", re.I)
PASTEBIN_RE = re.compile(r"pastebin\.com/(?:raw/)?(\w{6,})", re.I)
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

MOBALYTICS_API = "https://mobalytics.gg/api/poe-2/v1/graphql/query"


@dataclass
class Build:
    label: str
    kind: str  # "xml" (a PoB build) or "game" (the game's BuildPlanner .build JSON)
    data: str
    selected: bool = False  # the variant picked in the link


def fetch(url: str, timeout: float = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def decode_code(code: str) -> str:
    """PoB export code -> build XML."""
    code = code.strip().replace("+", "-").replace("/", "_")
    code += "=" * (-len(code) % 4)
    xml = zlib.decompress(base64.urlsafe_b64decode(code)).decode("utf-8", errors="replace")
    if "<PathOfBuilding" not in xml:
        raise ValueError("decoded data is not a Path of Building build")
    return xml


def codes_in_text(text: str) -> list[str]:
    found = []
    for m in CODE_RE.finditer(text):
        code = m.group(0)
        try:
            decode_code(code)
        except Exception:
            continue
        if code not in found:
            found.append(code)
    return found


def pob_code_from_share(text: str) -> str:
    """A pobb.in / poe.ninja / pastebin link, or a raw code -> PoB code."""
    text = text.strip()
    m = POBBIN_RE.search(text)
    if m:
        return fetch(f"https://pobb.in/{m.group(1)}/raw").strip()
    m = NINJA_RE.search(text)
    if m:
        for url in (f"https://poe.ninja/poe2/pob/raw/{m.group(2)}", f"https://poe.ninja/pob/raw/{m.group(2)}"):
            try:
                codes = codes_in_text(fetch(url))
                if codes:
                    return codes[0]
            except Exception:
                continue
        raise ValueError(f"could not download poe.ninja build {m.group(2)}")
    m = PASTEBIN_RE.search(text)
    if m:
        return fetch(f"https://pastebin.com/raw/{m.group(1)}").strip()
    codes = codes_in_text(text)
    if codes:
        return codes[0]
    raise ValueError(f"not a PoB code or share link: {text[:80]}")


# Mobalytics pages sit behind a Cloudflare challenge, but its GraphQL API does not.

def mobalytics_query(query: str) -> dict:
    body = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        MOBALYTICS_API,
        data=body,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    if out.get("errors"):
        raise ValueError("Mobalytics API error: " + "; ".join(e.get("message", "?") for e in out["errors"]))
    return out["data"]


DOC_FIELDS = """error data { id data { name pobCode buildVariants { values { id } } }
  content { __typename ... on NgfDocumentCmWidgetContentVariantsV1 { data { childrenVariants { id title } } } } }"""


def mobalytics_builds(url: str) -> list[Build]:
    parts = urllib.parse.urlparse(url)
    path = [p for p in parts.path.split("/") if p]
    if "builds" not in path or path.index("builds") == len(path) - 1:
        raise ValueError("not a Mobalytics build link")
    key = path[path.index("builds") + 1]
    if UUID_RE.match(key):
        field, arg = "userGeneratedDocumentById", f"id:{json.dumps(key)}"
    else:
        field, arg = "userGeneratedDocumentBySlug", f'slug:{json.dumps(key)}, type:"builds"'
    res = mobalytics_query(f"{{ poe2 {{ documents {{ {field}(input:{{{arg}}}) {{ {DOC_FIELDS} }} }} }} }}")
    doc = res["poe2"]["documents"][field]
    if not doc or doc.get("error") or not doc.get("data"):
        raise ValueError(f"Mobalytics couldn't find that build ({(doc or {}).get('error')})")
    doc_id = doc["data"]["id"]
    info = doc["data"]["data"] or {}
    name = info.get("name") or key

    titles = {}
    for widget in doc["data"].get("content") or []:
        for v in ((widget.get("data") or {}).get("childrenVariants") or []):
            titles[v["id"]] = v.get("title")
    variant_ids = [v["id"] for v in ((info.get("buildVariants") or {}).get("values") or [])]
    picked = None
    for value in urllib.parse.parse_qs(parts.query).values():
        for v in value:
            if v.startswith("activeVariantId,"):
                picked = v.split(",", 1)[1]

    print(f"Mobalytics: {name}", file=sys.stderr)
    if info.get("pobCode"):
        # One PoB code; its loadouts usually match the guide's variants
        return [Build(name, "xml", decode_code(pob_code_from_share(info["pobCode"])))]

    # No PoB code: test the in-game build file Mobalytics generates for each variant
    builds = []
    for vid in variant_ids:
        q = (
            f"{{ poe2 {{ documents {{ userGeneratedDocumentById(input:{{id:{json.dumps(doc_id)}}}) "
            f"{{ error errorMessage data {{ exportToGame(variantId:{json.dumps(vid)}) }} }} }} }} }}"
        )
        try:
            out = mobalytics_query(q)["poe2"]["documents"]["userGeneratedDocumentById"]
            game_json = (out.get("data") or {}).get("exportToGame")
            if not game_json:
                raise ValueError(out.get("errorMessage") or out.get("error") or "empty export")
        except Exception as e:
            print(f"  (variant {titles.get(vid, vid)}: {e})", file=sys.stderr)
            continue
        builds.append(Build(f"{name}  [{titles.get(vid) or vid}]", "game", game_json, selected=vid == picked))
    if not builds:
        raise ValueError("this Mobalytics build has no PoB code and no exportable variants")
    return builds


def gather(source: str) -> list[Build]:
    """One command-line argument -> builds to test."""
    path = Path(source)
    if len(source) < 260 and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "<PathOfBuilding" in text:
            return [Build(path.name, "xml", text)]
        if text.lstrip().startswith("{") and '"passives"' in text:
            return [Build(path.name, "game", text)]
        return [Build(path.name, "xml", decode_code(pob_code_from_share(text)))]

    if source.startswith(("http://", "https://")):
        host = urllib.parse.urlparse(source).netloc.lower()
        if "mobalytics.gg" in host:
            builds = mobalytics_builds(source)
        else:
            builds = [Build(source, "xml", decode_code(pob_code_from_share(source)))]
    else:
        builds = [Build("pasted code", "xml", decode_code(source))]
    return builds



# ---------------------------------------------------------------------------
# Running PoB's engine through its own LuaJIT
# ---------------------------------------------------------------------------

LUA_GLOBALSINDEX = -10002


def default_pob_dir() -> Path | None:
    candidates = []
    if os.environ.get("POB_DIR"):
        candidates.append(Path(os.environ["POB_DIR"]))
    if os.environ.get("APPDATA"):
        candidates.append(Path(os.environ["APPDATA"]) / "Path of Building Community (PoE2)")
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        if os.environ.get(env):
            candidates.append(Path(os.environ[env]) / "Path of Building Community (PoE2)")
    for c in candidates:
        if (c / "Launch.lua").exists():
            return c
    return None


class PoB:
    """One LuaJIT state with PoB loaded. Loading takes a few seconds; each build after that is quick."""

    def __init__(self, pob_dir: Path, lua_lib: Path | None = None):
        pob_dir = pob_dir.resolve()
        if (pob_dir / "src" / "Launch.lua").exists():  # a git checkout rather than an install
            pob_dir = pob_dir / "src"
        if not (pob_dir / "Launch.lua").exists():
            raise SystemExit(f"Path of Building not found in {pob_dir} (use --pob-dir)")
        self.pob_dir = pob_dir

        if lua_lib is None:
            for name in ("lua51.dll", "../runtime/lua51.dll"):
                if (pob_dir / name).exists():
                    lua_lib = (pob_dir / name).resolve()
                    break
        if lua_lib is None:
            raise SystemExit("Could not find PoB's lua51.dll. On Linux/macOS pass --lua-lib path/to/libluajit.so")
        if sys.platform == "win32":
            os.add_dll_directory(str(lua_lib.parent))
            if ctypes.sizeof(ctypes.c_void_p) != 8:
                raise SystemExit("PoB is 64-bit, so this needs 64-bit Python.")
        lib = ctypes.CDLL(str(lua_lib))
        lib.luaL_newstate.restype = ctypes.c_void_p
        lib.luaL_openlibs.argtypes = [ctypes.c_void_p]
        lib.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
        lib.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]
        lib.lua_tolstring.restype = ctypes.c_void_p
        lib.lua_settop.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.lua_pushlstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        lib.lua_pushboolean.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.lua_setfield.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p]
        lib.lua_getfield.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p]
        self.lib = lib
        self.L = lib.luaL_newstate()
        lib.luaL_openlibs(self.L)

        # PoB opens its data files with relative paths
        os.chdir(pob_dir)
        user_dir = Path(tempfile.gettempdir()) / "pob-tester-user"
        self._set_string("POBTESTER_LUA_DIR", LUA_DIR.as_posix())
        self._set_string("POBTESTER_POB_DIR", pob_dir.as_posix())
        self._set_string("POBTESTER_USER_DIR", user_dir.as_posix())
        script = (LUA_DIR / "headless.lua").read_bytes()
        self._run_chunk(script, b"@headless.lua", nresults=0)
        info = json.loads(self._call("pobtester_init"))
        if not info.get("ok"):
            raise SystemExit("PoB failed to start:\n" + info.get("error", "") + "\n" + "\n".join(info.get("log", [])))
        self.version = info.get("version")

    def _set_string(self, name: str, value: str) -> None:
        data = value.encode("utf-8")
        self.lib.lua_pushlstring(self.L, data, len(data))
        self.lib.lua_setfield(self.L, LUA_GLOBALSINDEX, name.encode())

    def _error(self) -> str:
        size = ctypes.c_size_t()
        ptr = self.lib.lua_tolstring(self.L, -1, ctypes.byref(size))
        msg = ctypes.string_at(ptr, size.value).decode("utf-8", errors="replace") if ptr else "unknown Lua error"
        self.lib.lua_settop(self.L, 0)
        return msg

    def _run_chunk(self, code: bytes, name: bytes, nresults: int) -> None:
        if self.lib.luaL_loadbuffer(self.L, code, len(code), name) != 0:
            raise RuntimeError(self._error())
        if self.lib.lua_pcall(self.L, 0, nresults, 0) != 0:
            raise RuntimeError(self._error())

    def _call(self, func: str, *args) -> str:
        """Call a global Lua function with string/bool args; it returns one string."""
        self.lib.lua_settop(self.L, 0)
        self.lib.lua_getfield(self.L, LUA_GLOBALSINDEX, func.encode())
        for a in args:
            if isinstance(a, bool):
                self.lib.lua_pushboolean(self.L, int(a))
            else:
                data = str(a).encode("utf-8")
                self.lib.lua_pushlstring(self.L, data, len(data))
        if self.lib.lua_pcall(self.L, len(args), 1, 0) != 0:
            raise RuntimeError(self._error())
        size = ctypes.c_size_t()
        ptr = self.lib.lua_tolstring(self.L, -1, ctypes.byref(size))
        out = ctypes.string_at(ptr, size.value).decode("utf-8", errors="replace")
        self.lib.lua_settop(self.L, 0)
        return out


    def evaluate(self, build: Build, skills: bool, level: int) -> dict:
        if build.kind == "game":
            return json.loads(self._call("pobtester_eval_game_build", build.data, skills, str(level)))
        return json.loads(self._call("pobtester_eval", build.data, skills))

    def log(self) -> list[str]:
        try:
            return json.loads(self._call("pobtester_log"))
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def short(n: float | None) -> str:
    if n is None:
        return "-"
    a = abs(n)
    if a >= 1e9:
        return f"{n / 1e9:.2f}B"
    if a >= 1e6:
        return f"{n / 1e6:.2f}M"
    if a >= 1e4:
        return f"{n / 1e3:.1f}K"
    if a >= 100 or float(n).is_integer():
        return f"{n:,.0f}"
    return f"{n:.1f}"


def report_loadout(r: dict, show_skills: bool) -> list[str]:
    s = r["stats"]
    who = " ".join(x for x in (r.get("ascendancy") or r.get("className"), f"lvl {r.get('level')}") if x)
    lines = [f"  {who}   main skill: {r.get('mainSkill') or '-'}"]

    dps = []
    if s.get("FullDPS"):
        dps.append(f"Full DPS {short(s['FullDPS'])}")
    dps.append(f"main skill {short(s.get('CombinedDPS') or s.get('TotalDPS'))}")
    best = r.get("bestSkill")
    if best and best["dps"] > (s.get("CombinedDPS") or 0) * 1.001:
        dps.append(f"best skill {short(best['dps'])} ({best['name']})")
    lines.append("  DPS      " + "   ".join(dps))
    hit = [f"avg hit {short(s.get('AverageHit') or s.get('AverageDamage'))}", f"{s.get('Speed', 0):.2f}/s"]
    if "CritChance" in s:
        hit.append(f"crit {s['CritChance']:.1f}% x{s.get('CritMultiplier', 0):.2f}")
    if "HitChance" in s and s["HitChance"] < 100:
        hit.append(f"hit chance {s['HitChance']:.0f}%")
    lines.append("           " + "   ".join(hit))
    lines.append(
        "  EHP      "
        + f"{short(s.get('TotalEHP'))}   life {short(s.get('Life'))}   ES {short(s.get('EnergyShield'))}"
        + f"   mana {short(s.get('Mana'))}   spirit {short(s.get('Spirit'))}"
    )
    lines.append(
        "  Max hit  "
        + "   ".join(
            f"{n} {short(s.get(k + 'MaximumHitTaken'))}"
            for n, k in (("phys", "Physical"), ("fire", "Fire"), ("cold", "Cold"), ("light", "Lightning"), ("chaos", "Chaos"))
        )
    )
    res = "/".join(f"{s.get(k + 'Resist', 0):.0f}" for k in ("Fire", "Cold", "Lightning"))
    lines.append(
        f"  Res      {res}  chaos {s.get('ChaosResist', 0):.0f}"
        + f"   armour {short(s.get('Armour'))}   evasion {short(s.get('Evasion'))}"
        + f"   block {s.get('BlockChance', 0):.0f}%"
    )
    if r.get("fullDPSSkills"):
        lines.append("  Full DPS is made of:")
        for sk in r["fullDPSSkills"]:
            count = f" x{sk['count']}" if sk.get("count", 1) != 1 else ""
            lines.append(f"    {short(sk.get('dps')):>9}  {sk.get('name')}{count}")
    if show_skills and r.get("skills"):
        lines.append("  Each skill as main skill:")
        damaging = [sk for sk in r["skills"] if sk.get("dps")]
        for sk in sorted(damaging, key=lambda x: -x["dps"]):
            is_main = sk["group"] == r.get("mainSocketGroup") and sk.get("active", 1) == r.get("mainActiveSkill", 1)
            mark = "*" if is_main else " "
            lines.append(f"   {mark}{short(sk['dps']):>9}  {sk.get('name')}")
        if len(damaging) < len(r["skills"]):
            lines.append(f"    ({len(r['skills']) - len(damaging)} skills with no damage hidden)")
    if r.get("assumed"):
        lines.append(f"  Assumed: {r['assumed']} (the guide has no PoB code)")
    for w in r.get("warnings") or []:
        lines.append(f"  ! {w}")
    return lines


def report(build: Build, r: dict, show_skills: bool) -> str:
    if not r.get("ok"):
        return f"== {build.label} ==\n  PoB could not load this build: {r.get('error')}\n"
    out = []
    loadouts = r["loadouts"]
    for lo in loadouts:
        title = build.label
        if lo.get("loadout") and len(loadouts) > 1:
            title += f"  [{lo['loadout']}]"
        if build.selected:
            title += "  (variant in your link)"
        out.append(f"== {title} ==")
        out += report_loadout(lo, show_skills)
        out.append("")
    return "\n".join(out)


def fit(label: str, width: int) -> str:
    """Shorten a label to width, keeping the [variant] part at the end readable."""
    if len(label) <= width:
        return label
    m = re.search(r"\s*(\[[^\]]*\])\s*$", label)
    tail = m.group(1) if m else ""
    if not tail:
        return label[: width - 3] + "..."
    head = label[: m.start()].strip()
    room = max(width - len(tail) - 4, 12)
    if len(tail) > width - room - 4:
        tail = tail[: width - room - 8] + "...]"
    return head[:room] + "... " + tail


def summary(rows: list[tuple[str, dict]]) -> str:
    """One line per build/loadout, for comparing variants at a glance."""
    lines = ["Summary", f"  {'build':<48} {'DPS':>9} {'EHP':>9} {'life':>7} {'ES':>7}"]
    for label, lo in rows:
        s = lo["stats"]
        dps = max(s.get("FullDPS") or 0, s.get("CombinedDPS") or 0, (lo.get("bestSkill") or {}).get("dps") or 0)
        name = fit(label, 48)
        lines.append(f"  {name:<48} {short(dps):>9} {short(s.get('TotalEHP')):>9} {short(s.get('Life')):>7} {short(s.get('EnergyShield')):>7}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("builds", nargs="*", help="Mobalytics / pobb.in / poe.ninja link, PoB code, or file")
    ap.add_argument("--edit", action="store_true", help="open the build editor in your browser (optionally with a build)")
    ap.add_argument("--port", type=int, default=0, help="port for --edit (default: any free port)")
    ap.add_argument("--skills", action="store_true", help="list the DPS of every skill")
    ap.add_argument("--fast", action="store_true", help="skip per-skill DPS (no 'best skill')")
    ap.add_argument("--level", type=int, default=90, help="character level for guides without a PoB code (default 90)")
    ap.add_argument("--pob-dir", type=Path, help="Path of Building (PoE2) folder (default: auto-detect)")
    ap.add_argument("--lua-lib", type=Path, help="LuaJIT shared library to use instead of PoB's lua51.dll")
    ap.add_argument("--json", action="store_true", help="print raw results as JSON")
    ap.add_argument("--save-xml", type=Path, help="save each build as a PoB XML file in this folder (open them in PoB)")
    ap.add_argument("--verbose", action="store_true", help="show PoB's own log output")
    args = ap.parse_args(argv)

    # Resolve file paths before PoB changes the working directory
    for attr in ("save_xml", "lua_lib", "pob_dir"):
        if getattr(args, attr):
            setattr(args, attr, getattr(args, attr).resolve())
    if args.edit:
        pob_dir = args.pob_dir or default_pob_dir()
        if pob_dir is None:
            print("Could not find Path of Building (PoE2). Pass --pob-dir.", file=sys.stderr)
            return 1
        source = args.builds[0] if args.builds else None
        if source and len(source) < 260 and Path(source).exists():
            source = str(Path(source).resolve())
        import editor_server

        print("Starting Path of Building...")
        pob = PoB(pob_dir, args.lua_lib)
        editor_server.run(pob, args.level, source, port=args.port)
        return 0
    if not args.builds:
        ap.error("give at least one build link, code or file (or use --edit)")

    sources = [str(Path(b).resolve()) if len(b) < 260 and not b.startswith("http") and Path(b).exists() else b for b in args.builds]

    work: list[Build] = []
    for src in sources:
        try:
            found = gather(src)
        except Exception as e:
            print(f"!! {src[:100]}: {e}", file=sys.stderr)
            continue
        if len(sources) > 1 and len(found) > 1 and not src.startswith("http"):
            for b in found:
                b.label = f"{Path(src).name} | {b.label}"
        work += found
    if not work:
        return 1

    pob_dir = args.pob_dir or default_pob_dir()
    if pob_dir is None:
        print("Could not find Path of Building (PoE2). Pass --pob-dir.", file=sys.stderr)
        return 1
    t0 = time.time()
    pob = PoB(pob_dir, args.lua_lib)
    if not args.json:
        print(f"PoB {pob.version} loaded in {time.time() - t0:.1f}s\n")

    results, rows = [], []
    for i, build in enumerate(work, 1):
        try:
            r = pob.evaluate(build, skills=not args.fast, level=args.level)
        except RuntimeError as e:
            # A Lua error in one build shouldn't stop the others
            r = {"ok": False, "error": str(e)}
            print("\n".join(pob.log()[-40:]), file=sys.stderr)
        if args.verbose:
            print("\n".join(pob.log()), file=sys.stderr)
        if args.save_xml:
            args.save_xml.mkdir(parents=True, exist_ok=True)
            xml = r.pop("xml", None) or (build.data if build.kind == "xml" else None)
            if xml:
                safe = re.sub(r'[\\/:*?"<>|]+', "-", fit(build.label, 80))
                (args.save_xml / f"{i:02d} {safe}.xml").write_text(xml, encoding="utf-8")
        r.pop("xml", None)
        r["label"] = build.label
        results.append(r)
        if r.get("ok"):
            for lo in r["loadouts"]:
                rows.append((build.label + (f"  [{lo['loadout']}]" if lo.get("loadout") and len(r["loadouts"]) > 1 else ""), lo))
        if not args.json:
            print(report(build, r, args.skills))
    if args.json:
        print(json.dumps(results, indent=2))
    elif len(rows) > 1:
        print(summary(rows))
    return 0 if all(r.get("ok") for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
