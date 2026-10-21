#!/usr/bin/env python3
"""Quick DPS / effective hit pool check for Path of Exile 2 builds, without opening PoB.

Give it a Mobalytics build link, a pobb.in / poe.ninja / pastebin link, a raw PoB code,
or a file containing one. It finds the Path of Building code, runs it through your
installed Path of Building (PoE2) calculation engine with no window, and prints the
numbers PoB would show in its sidebar.

    python pob_tester.py https://mobalytics.gg/poe-2/builds/some-build
    python pob_tester.py --skills eNrtfWt...
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
LUA_DIR = HERE / "lua"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Finding PoB codes
# ---------------------------------------------------------------------------

# PoB codes are zlib data in URL-safe base64, so they start with "eN" (best compression) or "eJ".
CODE_RE = re.compile(r"e[NJ][A-Za-z0-9_\-+/]{200,}={0,2}")
POBBIN_RE = re.compile(r"pobb\.in/(?:u/[\w-]+/)?([\w-]{6,})", re.I)
NINJA_RE = re.compile(r"poe\.ninja/(poe2/)?pob/(?:raw/)?([\w-]{4,})", re.I)
PASTEBIN_RE = re.compile(r"pastebin\.com/(?:raw/)?(\w{6,})", re.I)


def fetch(url: str, timeout: float = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def decode_code(code: str) -> str:
    """PoB export code -> build XML."""
    code = code.strip().replace("+", "-").replace("/", "_")
    code += "=" * (-len(code) % 4)
    data = base64.urlsafe_b64decode(code)
    xml = zlib.decompress(data).decode("utf-8", errors="replace")
    if "<PathOfBuilding" not in xml:
        raise ValueError("decoded data is not a Path of Building build")
    return xml


def unescape_page(text: str) -> str:
    """Undo the escaping that JSON blobs and HTML put around codes and links."""
    text = html.unescape(text)
    text = text.replace("\\u002F", "/").replace("\\u002f", "/").replace("\\/", "/")
    return text


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


def resolve_link(url: str) -> list[tuple[str, str]]:
    """A pobb.in / poe.ninja / pastebin link -> [(label, code)]."""
    m = POBBIN_RE.search(url)
    if m:
        return [(f"pobb.in/{m.group(1)}", fetch(f"https://pobb.in/{m.group(1)}/raw").strip())]
    m = NINJA_RE.search(url)
    if m:
        paths = [f"https://poe.ninja/poe2/pob/raw/{m.group(2)}", f"https://poe.ninja/pob/raw/{m.group(2)}"]
        for p in paths:
            try:
                text = fetch(p).strip()
                if codes_in_text(text):
                    return [(f"poe.ninja/{m.group(2)}", codes_in_text(text)[0])]
            except Exception:
                continue
        raise ValueError(f"could not download poe.ninja build {m.group(2)}")
    m = PASTEBIN_RE.search(url)
    if m:
        return [(f"pastebin/{m.group(1)}", fetch(f"https://pastebin.com/raw/{m.group(1)}").strip())]
    raise ValueError(f"not a PoB share link: {url}")


def codes_from_page(url: str, save_html: Path | None = None) -> list[tuple[str, str]]:
    """Any web page (Mobalytics build guides included) -> every PoB code it contains or links to."""
    page = fetch(url)
    if save_html:
        save_html.write_text(page, encoding="utf-8")
    text = unescape_page(page)

    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label: str, code: str) -> None:
        if code not in seen:
            seen.add(code)
            results.append((label, code))

    for code in codes_in_text(text):
        add("embedded code", code)

    links = []
    for rx, fmt in ((POBBIN_RE, "https://pobb.in/{}"), (PASTEBIN_RE, "https://pastebin.com/{}")):
        for m in rx.finditer(text):
            link = fmt.format(m.group(1))
            if link not in links:
                links.append(link)
    for m in NINJA_RE.finditer(text):
        link = f"https://poe.ninja/{m.group(1) or ''}pob/{m.group(2)}"
        if link not in links:
            links.append(link)
    for link in links:
        try:
            for label, code in resolve_link(link):
                add(label, code)
        except Exception as e:  # one dead link shouldn't sink the rest
            print(f"  (skipped {link}: {e})", file=sys.stderr)
    return results


def gather(source: str, save_html: Path | None) -> list[tuple[str, str]]:
    """One command-line argument -> [(label, xml)]."""
    path = Path(source)
    if len(source) < 260 and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "<PathOfBuilding" in text:
            return [(path.name, text)]
        codes = codes_in_text(text)
        if not codes:
            raise ValueError(f"no PoB code found in {path}")
        return [(f"{path.name} #{i + 1}" if len(codes) > 1 else path.name, decode_code(c)) for i, c in enumerate(codes)]

    if source.startswith(("http://", "https://")):
        host = urllib.parse.urlparse(source).netloc.lower()
        if any(h in host for h in ("pobb.in", "poe.ninja", "pastebin.com")):
            pairs = resolve_link(source)
        else:
            pairs = codes_from_page(source, save_html)
            if not pairs:
                hint = " (saved the page to --save-html for a look)" if save_html else " (try --save-html page.html to inspect it)"
                raise ValueError(f"no Path of Building code found on {source}{hint}")
        return [(label, decode_code(code)) for label, code in pairs]

    return [("pasted code", decode_code(source))]


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

    def evaluate(self, xml: str, skills: bool = False) -> dict:
        return json.loads(self._call("pobtester_eval", xml, skills))

    def log(self) -> list[str]:
        return json.loads(self._call("pobtester_log"))


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


def report(label: str, r: dict, show_skills: bool) -> str:
    if not r.get("ok"):
        return f"== {label} ==\n  PoB could not load this build: {r.get('error')}\n"
    s = r["stats"]
    who = " ".join(x for x in (r.get("ascendancy") or r.get("className"), f"lvl {r.get('level')}") if x)
    lines = [f"== {label} ==", f"  {who}   main skill: {r.get('mainSkill') or '-'}"]

    dps = []
    if s.get("FullDPS"):
        dps.append(f"Full DPS {short(s['FullDPS'])}")
    main = s.get("CombinedDPS") or s.get("TotalDPS")
    dps.append(f"Main skill DPS {short(main)}")
    if s.get("TotalDot"):
        dps.append(f"DoT {short(s['TotalDot'])}")
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
        for sk in sorted(r["skills"], key=lambda x: -(x.get("dps") or 0)):
            mark = "*" if sk["index"] == r.get("mainSocketGroup") else " "
            lines.append(f"   {mark}{short(sk.get('dps')):>9}  {sk.get('name')}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("builds", nargs="+", help="Mobalytics / pobb.in / poe.ninja link, PoB code, or file")
    ap.add_argument("--pob-dir", type=Path, help="Path of Building (PoE2) folder (default: auto-detect)")
    ap.add_argument("--lua-lib", type=Path, help="LuaJIT shared library to use instead of PoB's lua51.dll")
    ap.add_argument("--skills", action="store_true", help="also show DPS of every skill group")
    ap.add_argument("--json", action="store_true", help="print raw results as JSON")
    ap.add_argument("--save-html", type=Path, help="save the fetched web page here (for debugging)")
    ap.add_argument("--save-xml", type=Path, help="save each build's PoB XML into this folder")
    ap.add_argument("--verbose", action="store_true", help="show PoB's own log output")
    args = ap.parse_args(argv)

    # Resolve file paths before PoB changes the working directory
    for attr in ("save_html", "save_xml", "lua_lib", "pob_dir"):
        if getattr(args, attr):
            setattr(args, attr, getattr(args, attr).resolve())
    builds = [str(Path(b).resolve()) if not b.startswith(("http", "eN", "eJ")) and Path(b).exists() else b for b in args.builds]

    work: list[tuple[str, str]] = []
    for src in builds:
        try:
            pairs = gather(src, args.save_html)
        except Exception as e:
            print(f"!! {src}: {e}", file=sys.stderr)
            continue
        name = src if len(src) <= 90 else src[:40] + "..."
        for label, xml in pairs:
            work.append((label if len(builds) == 1 else f"{name} | {label}", xml))
    if not work:
        return 1

    if args.save_xml:
        args.save_xml.mkdir(parents=True, exist_ok=True)
        for i, (_, xml) in enumerate(work, 1):
            (args.save_xml / f"build_{i}.xml").write_text(xml, encoding="utf-8")

    pob_dir = args.pob_dir or default_pob_dir()
    if pob_dir is None:
        print("Could not find Path of Building (PoE2). Pass --pob-dir.", file=sys.stderr)
        return 1
    t0 = time.time()
    pob = PoB(pob_dir, args.lua_lib)
    if not args.json:
        print(f"PoB {pob.version} loaded in {time.time() - t0:.1f}s, {len(work)} build(s)\n")

    results = []
    for label, xml in work:
        r = pob.evaluate(xml, skills=args.skills)
        r["label"] = label
        results.append(r)
        if args.verbose:
            print("\n".join(pob.log()), file=sys.stderr)
        if not args.json:
            print(report(label, r, args.skills))
    if args.json:
        print(json.dumps(results, indent=2))
    return 0 if all(r.get("ok") for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
