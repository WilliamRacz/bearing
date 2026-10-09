"""bearing doctor: check that everything Bearing relies on is in place."""

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import BEARING_HOME, DATA_DIR, __version__

OK, WARN, FAIL = "✓", "!", "✗"


def check(results: list, status: str, label: str, detail: str = ""):
    results.append((status, label, detail))


def run() -> int:
    results: list = []

    v = sys.version_info
    check(results, OK if v >= (3, 10) else FAIL, f"Python {v.major}.{v.minor}",
          "" if v >= (3, 10) else "Bearing needs Python 3.10 or newer.")

    claude = shutil.which("claude")
    if claude:
        version = subprocess.run([claude, "--version"], capture_output=True, text=True).stdout.strip()
        check(results, OK, f"Claude Code {version}", claude)
    else:
        check(results, FAIL, "Claude Code not found",
              "Install it from https://claude.com/claude-code and sign in, then rerun.")

    check(results, OK if shutil.which("git") else FAIL, "git",
          "" if shutil.which("git") else "Install git (on a Mac: xcode-select --install).")

    if platform.system() == "Darwin":
        check(results, OK, "macOS", "\"Open in Claude Code\" uses Terminal.")
    else:
        check(results, WARN, platform.system(),
              "\"Open in Claude Code\" only works on macOS for now; everything else works.")

    settings = Path.home() / ".claude" / "settings.json"
    days = None
    if settings.exists():
        try:
            days = json.loads(settings.read_text()).get("cleanupPeriodDays")
        except json.JSONDecodeError:
            pass
    if days is None or days < 90:
        check(results, WARN, f"Claude Code keeps transcripts for {days or 30} days",
              "Bearing can only read history that still exists. To keep more, add "
              "\"cleanupPeriodDays\": 365 to ~/.claude/settings.json.")
    else:
        check(results, OK, f"Claude Code keeps transcripts for {days} days")

    projects = sorted(p.parent for p in DATA_DIR.glob("*/project.json"))
    check(results, OK, f"Data folder {BEARING_HOME}", f"{len(projects)} project(s)")
    for out_dir in projects:
        path = Path(json.loads((out_dir / "project.json").read_text())["path"])
        hooks = path / ".claude" / "settings.local.json"
        live = hooks.exists() and "-m bearing hook" in hooks.read_text()
        if not path.exists():
            check(results, WARN, f"  {out_dir.name}", f"folder moved or deleted: {path}")
        else:
            check(results, OK if live else WARN, f"  {out_dir.name}",
                  "briefing Claude Code each session" if live
                  else f"not briefing Claude Code — run: bearing install-hooks {path}")

    print(f"Bearing {__version__}\n")
    for status, label, detail in results:
        print(f" {status} {label}" + (f"\n     {detail}" if detail else ""))
    failed = any(s == FAIL for s, _, _ in results)
    print("\n" + ("Fix the ✗ items above, then run bearing doctor again." if failed
                  else "Ready. Run bearing to open the app."))
    return 1 if failed else 0
