"""Local build editor: a small web page on 127.0.0.1 that edits a build through PoB's engine.

Started by `python pob_tester.py --edit [link]` (or edit.bat). Nothing is reachable from
other machines, and requests must carry the per-run token the page is opened with.
"""

from __future__ import annotations

import base64
import json
import secrets
import sys
import threading
import webbrowser
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pob_tester

PAGE = Path(__file__).resolve().parent / "web" / "editor.html"

# Page actions that map straight onto Lua functions in lua/editor.lua
LUA_ACTIONS = {
    "state": "pobtester_state",
    "undo": "pobtester_undo",
    "reset": "pobtester_reset",
    "skills": "pobtester_skills",
    "gem_names": "pobtester_gem_names",
    "search_nodes": "pobtester_search_nodes",
    "equip": "pobtester_edit_equip",
    "unequip": "pobtester_edit_unequip",
    "jewel": "pobtester_edit_jewel",
    "gem": "pobtester_edit_gem",
    "add_gem": "pobtester_edit_add_gem",
    "remove_gem": "pobtester_edit_remove_gem",
    "group": "pobtester_edit_group",
    "level": "pobtester_edit_level",
    "loadout": "pobtester_edit_loadout",
    "alloc": "pobtester_edit_alloc",
    "dealloc": "pobtester_edit_dealloc",
}
NO_ARG = {"state", "undo", "reset", "skills", "gem_names"}


def encode_code(xml: str) -> str:
    return base64.urlsafe_b64encode(zlib.compress(xml.encode("utf-8"), 9)).decode("ascii")


class Editor:
    def __init__(self, pob: pob_tester.PoB, level: int):
        self.pob = pob
        self.level = level
        self.builds: list[pob_tester.Build] = []
        self.current = None
        self.lock = threading.Lock()

    def load_source(self, source: str) -> dict:
        source = source.strip()
        if not source:
            return {"ok": False, "error": "paste a Mobalytics / pobb.in link or a PoB code"}
        try:
            self.builds = pob_tester.gather(source)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        picked = next((i for i, b in enumerate(self.builds) if b.selected), 0)
        return self.load_index(picked)

    def load_index(self, index: int) -> dict:
        if not 0 <= index < len(self.builds):
            return {"ok": False, "error": "no such variant"}
        b = self.builds[index]
        r = json.loads(self.pob._call("pobtester_load", b.kind, b.data, str(self.level)))
        if r.get("ok"):
            self.current = index
            r["title"] = b.label
            r["variants"] = [x.label for x in self.builds]
            r["variant"] = index
        return r

    def handle(self, action: str, body: dict) -> dict:
        with self.lock:
            if action == "load":
                return self.load_source(body.get("source", ""))
            if action == "variant":
                return self.load_index(int(body.get("index", 0)))
            if self.current is None and action not in ("gem_names",):
                return {"ok": False, "error": "load a build first"}
            if action == "export":
                r = json.loads(self.pob._call("pobtester_export"))
                return {"ok": True, "code": encode_code(r["xml"])}
            func = LUA_ACTIONS.get(action)
            if not func:
                return {"ok": False, "error": f"unknown action {action}"}
            try:
                if action in NO_ARG:
                    out = self.pob._call(func)
                else:
                    out = self.pob._call(func, json.dumps(body))
            except RuntimeError as e:
                return {"ok": False, "error": str(e), "log": self.pob.log()[-20:]}
            r = json.loads(out)
            if self.current is not None and "loadouts" in r:
                r["title"] = self.builds[self.current].label
                r["variants"] = [x.label for x in self.builds]
                r["variant"] = self.current
            return r


def make_handler(editor: Editor, token: str):
    page = PAGE.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split("?")[0] in ("/", "/index.html"):
                self._send(200, page, "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            # A custom header can't be sent cross-site without a CORS preflight, which we never allow
            if self.headers.get("X-Editor-Token") != token or not self.path.startswith("/api/"):
                self._send(403, b"forbidden", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                body = {}
            result = editor.handle(self.path[len("/api/"):], body if isinstance(body, dict) else {})
            self._send(200, json.dumps(result).encode("utf-8"), "application/json")

    return Handler


def run(pob: pob_tester.PoB, level: int, source: str | None, port: int = 0, open_browser: bool = True) -> None:
    editor = Editor(pob, level)
    token = secrets.token_urlsafe(16)
    server = HTTPServer(("127.0.0.1", port), make_handler(editor, token))
    url = f"http://127.0.0.1:{server.server_address[1]}/?t={token}"
    if source:
        url += "&load=" + base64.urlsafe_b64encode(source.encode()).decode()
    print(f"Build editor running at {url}")
    print("Leave this window open while you edit. Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
        sys.stdout.flush()
