"""Local web app: `python3 -m bearing serve` → http://127.0.0.1:7777

Standard library only. Binds to localhost, and write actions require a
same-origin JSON request with an X-Bearing header so other websites open in
the browser can't trigger them.
"""

import json
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from . import DATA_DIR, correct, live, newproject, northstar, onboard
from .history import to_json

WEB_DIR = Path(__file__).resolve().parent / "web"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


def register(project: Path):
    """Remember where a project lives so the web app can find it."""
    out_dir = DATA_DIR / project.name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "project.json").write_text(json.dumps({"path": str(project)}))


def projects() -> list[dict]:
    found = []
    for meta in sorted(DATA_DIR.glob("*/project.json")):
        path = Path(json.loads(meta.read_text())["path"])
        star = meta.parent / "north_star.json"
        found.append({"name": meta.parent.name, "path": str(path),
                      "has_north_star": star.exists()})
    return found


def project_path(name: str) -> Path:
    meta = DATA_DIR / name / "project.json"
    if not re.fullmatch(r"[\w.\- ]+", name) or not meta.exists():
        raise LookupError(f"Unknown project {name!r}")
    return Path(json.loads(meta.read_text())["path"])


def project_payload(name: str) -> dict:
    path = project_path(name)
    status = onboard.read_status(path)
    if status and status["stage"] not in ("review", "done"):
        return {"name": name, "path": str(path), "onboarding": status}
    st = correct.load_state(path)
    rows = []
    for leg, row in zip(st["legs"], to_json(st["legs"])):
        row["prompts"] = len(leg.prompts)
        row["files_touched"] = len(leg.files_touched)
        row.update(entry=st["entries"].get(leg.number),
                   rating=st["ratings"].get(leg.number),
                   correction=st["corrections"].get(leg.number))
        rows.append(row)
    lock = st["out_dir"] / ".update.lock"
    return {"name": name, "path": str(path), "star": st["star"], "legs": rows,
            "updating": lock.exists()}


def leg_payload(name: str, number: int) -> dict:
    path = project_path(name)
    st = correct.load_state(path)
    leg = next((l for l in st["legs"] if l.number == number), None)
    if leg is None:
        raise LookupError(f"No leg {number}")
    deps = correct.dependents(leg, st["legs"], path)
    return {"commits": leg.commits and [{**c, "time": c["time"].isoformat()} for c in leg.commits],
            "later_commits": [{"sha": s, "subject": subj, "files": f} for s, subj, f in deps["commits"]],
            "later_legs": [n for n, _ in deps["legs"]]}


class Handler(BaseHTTPRequestHandler):
    server_version = "Bearing"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ALLOWED_HOSTS

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data):
        self._send(status, json.dumps(data).encode(), "application/json")

    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"error": "Forbidden host"})
        path = unquote(self.path.split("?", 1)[0])
        try:
            if path in ("/", "/index.html"):
                return self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/projects":
                return self._json(200, projects())
            if path == "/api/candidates":
                return self._json(200, onboard.candidates())
            if path == "/api/defaults":
                return self._json(200, {"parent": str(Path.home() / "Desktop")})
            if m := re.fullmatch(r"/api/p/([^/]+)", path):
                return self._json(200, project_payload(m.group(1)))
            if m := re.fullmatch(r"/api/p/([^/]+)/leg/(\d+)", path):
                return self._json(200, leg_payload(m.group(1), int(m.group(2))))
            self._json(404, {"error": "Not found"})
        except (LookupError, correct.CorrectionError) as exc:
            self._json(404, {"error": str(exc)})

    def do_POST(self):
        origin = self.headers.get("Origin")
        expected = f"http://{self.headers.get('Host')}"
        if (not self._host_ok() or self.headers.get("X-Bearing") != "1"
                or (origin and origin != expected)
                or not (self.headers.get("Content-Type") or "").startswith("application/json")):
            return self._json(403, {"error": "Forbidden"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "Bad JSON"})
        path = unquote(self.path)
        try:
            if m := re.fullmatch(r"/api/p/([^/]+)/correct", path):
                result = correct.apply_correction(project_path(m.group(1)), int(body["leg"]),
                                                  str(body["action"]), str(body.get("note", "")).strip())
                return self._json(200, result)
            if path == "/api/new/chat":
                messages = [{"role": str(m["role"]), "text": str(m["text"])}
                            for m in body.get("messages", [])]
                return self._json(200, newproject.chat(messages))
            if path == "/api/new/create":
                project = newproject.create(str(body["name"]), Path(str(body["parent"])),
                                            body["north_star"])
                return self._json(200, {"name": project.name, "path": str(project)})
            if path == "/api/add":
                project = Path(str(body["path"])).expanduser().resolve()
                onboard.start(project)
                return self._json(200, {"name": project.name})
            if m := re.fullmatch(r"/api/p/([^/]+)/confirm", path):
                project = project_path(m.group(1))
                out_dir = DATA_DIR / project.name
                star = northstar.load(out_dir / "north_star.json")
                if not star:
                    raise LookupError("No North Star to confirm yet.")
                star = northstar.confirm(star, {int(n) for n in body.get("reject", [])})
                (out_dir / "north_star.json").write_text(json.dumps(star, indent=2))
                (out_dir / "NORTH_STAR.md").write_text(northstar.render_markdown(star, project.name))
                onboard.set_status(project, "done", "Confirmed.")
                if body.get("hooks"):
                    live.install_hooks(project)
                live.spawn_update(project)  # rates every leg against the confirmed goals
                return self._json(200, {"message": "Confirmed. Rating every leg now."})
            if m := re.fullmatch(r"/api/p/([^/]+)/launch", path):
                project = project_path(m.group(1))
                st = correct.load_state(project)
                newproject.launch(project, first_session=not st["legs"])
                return self._json(200, {"message": "Opened Claude Code in Terminal."})
            if m := re.fullmatch(r"/api/p/([^/]+)/update", path):
                live.spawn_update(project_path(m.group(1)))
                return self._json(200, {"message": "Update started in the background."})
            self._json(404, {"error": "Not found"})
        except (LookupError, KeyError, ValueError, TypeError, RuntimeError,
                subprocess.SubprocessError, correct.CorrectionError) as exc:
            self._json(400, {"error": str(exc)})


def serve(port: int, open_browser: bool = False):
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        raise SystemExit(f"Port {port} is busy — Bearing may already be running at "
                         f"http://127.0.0.1:{port}. Or pick another: bearing serve --port 7778")
    url = f"http://127.0.0.1:{port}"
    print(f"Bearing is running at {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
