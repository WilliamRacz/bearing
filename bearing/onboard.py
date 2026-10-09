"""Find my bearing: bring an existing project into Bearing in one step.

Reads the project's Claude Code history, writes the Logbook and drafts a
North Star, recording progress in onboard.json so the web app can show it.
The owner then reviews the draft and confirms it, which rates every leg.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

from . import DATA_DIR, northstar
from .history import read_past, transcript_dir
from .logbook import build_logbook, render_markdown


def status_path(project: Path) -> Path:
    return DATA_DIR / project.name / "onboard.json"


def read_status(project: Path) -> dict | None:
    path = status_path(project)
    return json.loads(path.read_text()) if path.exists() else None


def set_status(project: Path, stage: str, message: str, **extra):
    path = status_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"stage": stage, "message": message,
                                "updated": time.time(), **extra}))


def check_project(project: Path) -> str | None:
    """Return a plain-English problem, or None if the folder can be added."""
    if not project.is_dir():
        return f"{project} isn't a folder."
    if (DATA_DIR / project.name / "north_star.json").exists():
        return f"Bearing already tracks a project called {project.name}."
    if not transcript_dir(project).exists():
        return ("Bearing couldn't find any Claude Code sessions for this folder. Open the exact "
                "folder you run Claude Code in, or use New project to start fresh.")
    return None


def run(project: Path, model: str | None = None):
    """The whole pipeline. Runs in the background; progress goes to onboard.json."""
    out_dir = DATA_DIR / project.name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "project.json").write_text(json.dumps({"path": str(project)}))
    try:
        set_status(project, "reading", "Reading your Claude Code history…")
        legs = read_past(project)
        if not legs:
            raise RuntimeError("No Claude Code sessions with any work in them were found.")

        set_status(project, "logbook", f"Writing the Logbook: 0 of {len(legs)} legs…",
                   done=0, total=len(legs))
        entries, _ = build_logbook(
            legs, project.name, out_dir / "logbook.json", None, False, model,
            progress=lambda done, total: set_status(
                project, "logbook", f"Writing the Logbook: {done} of {total} legs…",
                done=done, total=total))
        (out_dir / "LOGBOOK.md").write_text(render_markdown(legs, entries, project.name))
        if len(entries) < len(legs):
            print(f"{len(legs) - len(entries)} legs have no entry (no conversation or failed).")

        set_status(project, "north_star", "Drafting your North Star from the Logbook…")
        star = northstar.draft(project, legs, entries, model, out_dir)
        (out_dir / "north_star.json").write_text(json.dumps(star, indent=2))
        (out_dir / "NORTH_STAR.md").write_text(northstar.render_markdown(star, project.name))
        set_status(project, "review", "Your North Star is ready to review.")
    except Exception as exc:  # surface any failure to the web app
        set_status(project, "error", f"Something went wrong: {exc}")
        raise


def start(project: Path):
    """Kick off run() in a background process and return immediately."""
    from .live import bearing_env  # avoid an import cycle at module load
    problem = check_project(project)
    if problem:
        raise ValueError(problem)
    set_status(project, "reading", "Starting…")
    out_dir = DATA_DIR / project.name
    (out_dir / "project.json").write_text(json.dumps({"path": str(project)}))
    log = open(out_dir / "update.log", "a")
    subprocess.Popen([sys.executable, "-m", "bearing", "add", str(project)],
                     cwd=out_dir, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                     start_new_session=True, env=bearing_env())


def candidates(limit: int = 20) -> list[dict]:
    """Folders where Claude Code has been used, most recent first, not yet in Bearing."""
    from .history import CLAUDE_PROJECTS
    tracked = {json.loads(p.read_text())["path"] for p in DATA_DIR.glob("*/project.json")}
    found = {}
    for tdir in CLAUDE_PROJECTS.glob("*"):
        files = list(tdir.glob("*.jsonl"))
        if not files:
            continue
        cwd = None
        for f in files:
            with open(f) as fh:
                for _, line in zip(range(50), fh):
                    try:
                        cwd = json.loads(line).get("cwd")
                    except json.JSONDecodeError:
                        continue
                    if cwd:
                        break
            if cwd:
                break
        if not cwd or cwd in tracked or cwd == str(Path.home()) or not Path(cwd).is_dir():
            continue
        if (DATA_DIR / Path(cwd).name / "north_star.json").exists():
            continue  # another folder with the same name is already tracked
        last = max(f.stat().st_mtime for f in files)
        size = sum(f.stat().st_size for f in files)
        if cwd not in found or found[cwd]["last_active"] < last:
            found[cwd] = {"path": cwd, "name": Path(cwd).name, "last_active": last,
                          "sessions": len(files), "history_mb": round(size / 1e6, 1)}
    return sorted(found.values(), key=lambda c: -c["last_active"])[:limit]
