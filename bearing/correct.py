"""Course correct: act on a drifted leg — roll back, accept, defer, or get a fix prompt.

Rollback never rewrites history: it reverts the leg's commits on a new branch
(bearing/rollback-leg-N) so the owner can review before merging.
"""

import json
import subprocess
from datetime import date
from pathlib import Path

from . import DATA_DIR, drift, northstar
from .history import Leg, read_past

ROLLBACK_PREFIX = "bearing/rollback-leg-"


def git(project: Path, *args, check=True) -> str:
    return subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True,
                          check=check).stdout.strip()


def load_corrections(path: Path) -> dict[int, dict]:
    return {int(k): v for k, v in json.loads(path.read_text()).items()} if path.exists() else {}


def save_corrections(path: Path, corrections: dict):
    path.write_text(json.dumps(corrections, indent=2))


# --- What depends on this leg ----------------------------------------------

def commit_files(project: Path, sha: str) -> set[str]:
    return set(filter(None, git(project, "show", "--name-only", "--format=", sha).splitlines()))


def later_commits(project: Path, after_sha: str) -> list[tuple[str, str, set[str]]]:
    out = git(project, "log", "--reverse", "--format=%x1e%h%x1f%s", "--name-only",
              f"{after_sha}..HEAD")
    commits = []
    for block in filter(None, out.split("\x1e")):
        head, *files = block.strip().splitlines()
        sha, subject = head.split("\x1f", 1)
        commits.append((sha, subject, set(filter(None, files))))
    return commits


def dependents(leg: Leg, legs: list[Leg], project: Path) -> dict:
    """Later legs and commits that touched the same files as this leg."""
    later_legs = [(l.number, sorted(l.files_touched & leg.files_touched))
                  for l in legs if l.number > leg.number and l.files_touched & leg.files_touched]
    later = []
    if leg.commits:
        changed = set().union(*(commit_files(project, c["sha"]) for c in leg.commits))
        later = [(sha, subject, sorted(files & changed))
                 for sha, subject, files in later_commits(project, leg.commits[-1]["sha"])
                 if files & changed]
    return {"legs": later_legs, "commits": later}


# --- Actions ----------------------------------------------------------------

def accept(star: dict, leg: Leg, rating: dict | None, note: str) -> dict:
    """Record the drift as an intended change, so future checks treat it as on course."""
    what = "; ".join((rating or {}).get("off_goal", []) + (rating or {}).get("against", [])) \
        or (rating or {}).get("note", "")
    star["course_changes"].append({
        "leg": leg.number, "from": f"Flagged as drift: {what}", "to": note, "made_by": "you",
        "question": "", "status": "accepted", "accepted_on": date.today().isoformat()})
    star["course_changes"].sort(key=lambda c: c["leg"])
    return star


def defer(star: dict, leg: Leg, note: str) -> dict:
    star.setdefault("deferred", []).append(
        {"leg": leg.number, "item": note, "on": date.today().isoformat()})
    return star


def rollback(project: Path, leg: Leg) -> str:
    """Revert the leg's commits on a new branch. Returns the branch name."""
    if not leg.commits:
        raise SystemExit(f"Leg {leg.number} has no commits, so there's no checkpoint to roll "
                         "back to. Use --fix-prompt to have Claude undo it by hand.")
    if git(project, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("The project has uncommitted changes. Commit or stash them first.")
    start = git(project, "rev-parse", "--abbrev-ref", "HEAD")
    branch = f"{ROLLBACK_PREFIX}{leg.number}"
    git(project, "switch", "-c", branch)
    shas = [c["sha"] for c in reversed(leg.commits)]  # newest first
    result = subprocess.run(["git", "-C", str(project), "revert", "--no-edit", *shas],
                            capture_output=True, text=True)
    if result.returncode != 0:
        subprocess.run(["git", "-C", str(project), "revert", "--abort"], capture_output=True)
        git(project, "switch", start)
        git(project, "branch", "-D", branch)
        raise SystemExit("Rollback hit conflicts with later work, so nothing was changed.\n"
                         "Later commits build on this leg — use --fix-prompt to undo it by hand.\n"
                         + result.stderr.strip()[-600:])
    git(project, "switch", start)
    return branch


def fix_prompt(leg: Leg, entry: dict, rating: dict | None, star: dict) -> str:
    """A ready-to-paste instruction for Claude Code to undo a drifted leg by hand."""
    flags = (rating or {}).get("against", []) + (rating or {}).get("off_goal", [])
    goals = "\n".join(f"- {g['id']}: {g['goal']}" for g in star["goals"])
    lines = [f"Bearing course correction for leg {leg.number} ({leg.start:%b %d}): "
             f"\"{entry['headline']}\".", "",
             "This work drifted from the project's North Star:"]
    lines += [f"- {f}" for f in flags] or [f"- {(rating or {}).get('note', '')}"]
    lines += ["", "What was built in that leg:"] + [f"- {b}" for b in entry["built"]]
    if leg.files_touched:
        lines += ["", "Files edited in that leg (may have changed since):"]
        lines += [f"- {f}" for f in sorted(leg.files_touched)]
    lines += ["", "Current goals:", goals, "",
              "Please find what remains of the drifted work, list it for me, and propose how to "
              "remove or disable it without breaking anything that serves the goals. Don't "
              "change anything until I approve the plan. When done, suggest a commit."]
    return "\n".join(lines)


def describe(leg: Leg, entry: dict, rating: dict | None, deps: dict,
             correction: dict | None) -> str:
    r = rating or {}
    lines = [f"Leg {leg.number} · {leg.start:%a %b %d} · {entry['headline']}",
             f"Rating: {r.get('rating', 'not rated').replace('_', ' ')} — {r.get('note', '')}"]
    for item in r.get("against", []):
        lines.append(f"  ❌ {item}")
    for item in r.get("off_goal", []):
        lines.append(f"  ⚠️  {item}")
    if correction:
        lines.append(f"Already corrected: {correction['action']} on {correction['on']}"
                     + (f" — {correction['note']}" if correction.get("note") else ""))
    lines.append("")
    if leg.commits:
        lines.append("Checkpoints: " + ", ".join(f"{c['sha']} {c['subject']}" for c in leg.commits))
    else:
        lines.append("Checkpoints: none (no commits in this leg) — rollback isn't possible.")
    if deps["commits"]:
        lines.append(f"Later commits that build on it ({len(deps['commits'])}):")
        lines += [f"  {sha} {subject} — {', '.join(files[:3])}" for sha, subject, files in deps["commits"][:8]]
    if deps["legs"]:
        lines.append("Later legs that edited the same files: "
                     + ", ".join(str(n) for n, _ in deps["legs"]))
    lines += ["", "Options:",
              "  --accept \"why\"     it was intended; record it in the North Star",
              "  --defer \"what\"     leave it for later; Claude is told not to touch it unasked",
              "  --rollback         revert the leg's commits on a review branch"
              + ("" if leg.commits else " (not available)"),
              "  --fix-prompt       print an instruction to paste into Claude Code"]
    return "\n".join(lines)


# --- Shared entry point (CLI and web) ---------------------------------------

class CorrectionError(Exception):
    pass


def load_state(project: Path) -> dict:
    out_dir = DATA_DIR / project.name
    book = out_dir / "logbook.json"
    star = northstar.load(out_dir / "north_star.json")
    if not book.exists() or not star:
        raise CorrectionError("Run logbook and find-bearing first.")
    drift_path = out_dir / "drift.json"
    return {
        "out_dir": out_dir,
        "legs": read_past(project),
        "entries": {int(k): v["entry"] for k, v in json.loads(book.read_text()).items()},
        "star": star,
        "ratings": {int(k): v for k, v in json.loads(drift_path.read_text()).items()}
        if drift_path.exists() else {},
        "corrections": load_corrections(out_dir / "corrections.json"),
    }


def apply_correction(project: Path, leg_number: int, action: str, note: str = "") -> dict:
    """Run one course-correct action. Returns {"message", "prompt"?, "branch"?}."""
    st = load_state(project)
    leg = next((l for l in st["legs"] if l.number == leg_number), None)
    if leg is None or leg_number not in st["entries"]:
        raise CorrectionError(f"No Logbook entry for leg {leg_number}.")
    star, rating, entry = st["star"], st["ratings"].get(leg_number), st["entries"][leg_number]
    result: dict = {}

    if action == "fix-prompt":
        result["prompt"] = fix_prompt(leg, entry, rating, star)
        result["message"] = "Paste this into Claude Code in the project."
        record = {"action": action}
    elif action == "accept":
        if not note:
            raise CorrectionError("Say briefly why it was intended.")
        star = accept(star, leg, rating, note)
        record = {"action": action, "note": note}
        result["message"] = f"Leg {leg_number} accepted into the North Star."
    elif action == "defer":
        if not note:
            raise CorrectionError("Say what to leave for later.")
        star = defer(star, leg, note)
        record = {"action": action, "note": note}
        result["message"] = f"Deferred. Claude will be told not to work on it unless asked."
    elif action == "rollback":
        try:
            branch = rollback(project, leg)
        except SystemExit as exc:
            raise CorrectionError(str(exc)) from None
        record = {"action": action, "branch": branch}
        result.update(branch=branch, message=(
            f"Reverted leg {leg_number}'s commits on branch {branch}. Your current branch is "
            f"unchanged; review and merge it when you're happy."))
    else:
        raise CorrectionError(f"Unknown action {action!r}.")

    out_dir, corrections = st["out_dir"], st["corrections"]
    corrections[leg_number] = {**record, "on": date.today().isoformat()}
    save_corrections(out_dir / "corrections.json", corrections)
    (out_dir / "north_star.json").write_text(json.dumps(star, indent=2))
    (out_dir / "NORTH_STAR.md").write_text(northstar.render_markdown(star, project.name))
    (out_dir / "COURSE_MAP.md").write_text(drift.render_markdown(
        st["legs"], st["entries"], st["ratings"], star, project.name, corrections))
    return result
