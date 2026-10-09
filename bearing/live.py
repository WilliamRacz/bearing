"""Live loop: brief Claude Code at session start, update the Logbook after.

Claude Code hooks call `python -m bearing hook session-start|session-end`.
SessionStart prints the briefing (fast: reads files only) and kicks off a
background catch-up, since desktop sessions often never formally end.
SessionEnd kicks off the same background update.
"""

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import DATA_DIR, drift, northstar
from .correct import load_corrections
from .history import read_past
from .logbook import build_logbook, render_markdown

PACKAGE_PARENT = Path(__file__).resolve().parent.parent  # makes `-m bearing` importable
GUARD_ENV = "BEARING_RUNNING"
LOCK_STALE_SECONDS = 30 * 60
RECENT_LEGS = 3


def project_dir(project: Path) -> Path:
    return DATA_DIR / project.name


# --- Briefing -------------------------------------------------------------

def briefing(project: Path) -> str | None:
    out_dir = project_dir(project)
    star = northstar.load(out_dir / "north_star.json")
    if not star or star["status"] != "confirmed":
        return None

    lines = ["# Bearing briefing",
             "This project is tracked by Bearing, which keeps the work aligned with the owner's "
             "confirmed North Star below.", "",
             f"**Project:** {star['one_liner']}", "", "**Goals**"]
    lines += [f"- {g['id']}: {g['goal']}" for g in star["goals"]]
    lines += ["", "**Non-goals (don't build these without asking)**"]
    lines += [f"- {n}" for n in star["non_goals"]]
    if star.get("deferred"):
        lines += ["", "**Deferred by the owner (don't work on these unless asked)**"]
        lines += [f"- {d['item']}" for d in star["deferred"]]
    next_up = [m for m in star["milestones"] if m["status"] != "done"]
    if next_up:
        lines += ["", "**Milestones not yet done**"]
        lines += [f"- {m['name']} ({m['status'].replace('_', ' ')})" for m in next_up]

    book_path = out_dir / "logbook.json"
    if book_path.exists():
        book = {int(k): v["entry"] for k, v in json.loads(book_path.read_text()).items()}
        recent = sorted(book)[-RECENT_LEGS:]
        if recent:
            lines += ["", "**Recent legs (most recent last)**"]
            for n in recent:
                e = book[n]
                lines.append(f"- Leg {n}: {e['headline']}")
                for item in e["open_items"][:5]:
                    lines.append(f"  - Left open: {item}")

    drift_path = out_dir / "drift.json"
    if drift_path.exists():
        ratings = json.loads(drift_path.read_text())
        corrected = {n for n, c in load_corrections(out_dir / "corrections.json").items()
                     if c["action"] != "fix-prompt"}
        flagged = [r for r in ratings.values() if r["rating"] in ("drifting", "off_course")
                   and r["leg"] not in corrected][-3:]
        if flagged:
            lines += ["", "**Recent drift flags**"]
            lines += [f"- Leg {r['leg']} ({r['rating'].replace('_', ' ')}): {r['note']}"
                      for r in flagged]

    lines += ["", "**How to work with Bearing**",
              "- Before starting work that serves none of the goals, or touches a non-goal, "
              "say so and ask the owner first.",
              "- When you make a significant choice the owner didn't ask for (architecture, "
              "service, scope, data handling), state it in one line starting with \"Decision:\" "
              "so it is recorded in the Logbook.",
              "- When a meaningful chunk of work is done and working, suggest a git commit so "
              "there is a checkpoint to return to."]
    return "\n".join(lines)


# --- Background update ----------------------------------------------------

def update(project: Path, model: str | None = None):
    """Bring Logbook, drift ratings and rendered files up to date."""
    out_dir = project_dir(project)
    out_dir.mkdir(parents=True, exist_ok=True)
    legs = read_past(project)
    entries, written = build_logbook(legs, project.name, out_dir / "logbook.json", None,
                                     False, model)
    (out_dir / "LOGBOOK.md").write_text(render_markdown(legs, entries, project.name))

    star = northstar.load(out_dir / "north_star.json")
    if not star or star["status"] != "confirmed":
        print("No confirmed North Star; skipping drift.")
        return
    drift_path = out_dir / "drift.json"
    ratings = {int(k): v for k, v in json.loads(drift_path.read_text()).items()} \
        if drift_path.exists() else {}
    to_score = [l for l in legs if l.number in entries
                and (l.number in written or l.number not in ratings)]
    if to_score:
        print(f"Rating {len(to_score)} legs: {[l.number for l in to_score]}")
        ratings.update(drift.score(to_score, entries, star, model, out_dir))
        drift_path.write_text(json.dumps(ratings, indent=2))
    (out_dir / "COURSE_MAP.md").write_text(drift.render_markdown(
        legs, entries, ratings, star, project.name, load_corrections(out_dir / "corrections.json")))


def acquire_lock(lock: Path) -> bool:
    if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
        lock.unlink(missing_ok=True)
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL))
        return True
    except FileExistsError:
        return False


def run_update_locked(project: Path):
    out_dir = project_dir(project)
    out_dir.mkdir(parents=True, exist_ok=True)
    lock = out_dir / ".update.lock"
    if not acquire_lock(lock):
        print("Another update is running; skipping.")
        return
    try:
        print(f"--- update {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
        update(project)
    finally:
        lock.unlink(missing_ok=True)


def spawn_update(project: Path):
    """Run the update in a detached background process so hooks return instantly."""
    out_dir = project_dir(project)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = open(out_dir / "update.log", "a")
    subprocess.Popen(
        [sys.executable, "-m", "bearing", "update", str(project)],
        cwd=out_dir, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
        start_new_session=True, env=bearing_env())


def bearing_env() -> dict:
    """Environment for Bearing's own subprocesses: importable, and guarded against hooks."""
    path = os.pathsep.join(filter(None, [str(PACKAGE_PARENT), os.environ.get("PYTHONPATH")]))
    return {**os.environ, "PYTHONPATH": path, GUARD_ENV: "1"}


def hook_command(event: str) -> str:
    """The command Claude Code runs for a hook. Works wherever Bearing is installed."""
    return (f"PYTHONPATH={shlex.quote(str(PACKAGE_PARENT))} "
            f"{shlex.quote(sys.executable)} -m bearing hook {event}")


# --- Hooks ----------------------------------------------------------------

def handle_hook(event: str):
    if os.environ.get(GUARD_ENV):
        return  # never react to Bearing's own claude -p calls
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())

    if event == "session-start":
        text = briefing(project)
        if text:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "SessionStart", "additionalContext": text}}))
        spawn_update(project)
    elif event == "session-end":
        spawn_update(project)


def install_hooks(project: Path) -> Path:
    """Add Bearing's hooks to the project's .claude/settings.local.json (merging)."""
    settings_path = project / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(exist_ok=True)
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    hooks = settings.setdefault("hooks", {})
    for event, arg in (("SessionStart", "session-start"), ("SessionEnd", "session-end")):
        command = hook_command(arg)
        groups = hooks.setdefault(event, [])
        groups[:] = [g for g in groups
                     if not any("-m bearing hook" in h.get("command", "") for h in g.get("hooks", []))]
        groups.append({"hooks": [{"type": "command", "command": command, "timeout": 30}]})
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return settings_path
