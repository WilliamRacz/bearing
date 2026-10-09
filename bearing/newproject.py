"""Start new: a goal-setting conversation that becomes a North Star, then a project.

The conversation is stateless on our side: the caller sends the whole chat each
turn and Claude replies with the next question, or a draft North Star once it
has enough. Creating the project makes the folder, a first commit (checkpoint
zero), the confirmed North Star and the hooks. Launching opens Claude Code in
Terminal, already briefed.
"""

import json
import re
import shlex
import subprocess
from datetime import date
from pathlib import Path

from . import DATA_DIR, northstar
from .claude import ask_claude
from .live import install_hooks

MAX_TURNS_HINT = 6

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "suggested_name": {"type": "string",
                           "description": "Short folder name, letters/numbers/dashes, e.g. habit-tracker."},
        "one_liner": northstar.SCHEMA["properties"]["one_liner"],
        "goals": northstar.SCHEMA["properties"]["goals"],
        "non_goals": northstar.SCHEMA["properties"]["non_goals"],
        "constraints": northstar.SCHEMA["properties"]["constraints"],
        "milestones": northstar.SCHEMA["properties"]["milestones"],
        "questions": northstar.SCHEMA["properties"]["questions"],
    },
    "required": ["suggested_name", "one_liner", "goals", "non_goals", "constraints",
                 "milestones", "questions"],
}

CHAT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "What to say to the person next."},
        "ready": {"type": "boolean", "description": "True once north_star is filled in."},
        "north_star": DRAFT_SCHEMA,
    },
    "required": ["reply", "ready"],
}

SYSTEM_PROMPT = f"""You are Bearing's project setup guide. Bearing helps people who \
build software with AI (often without an engineering background) stay aligned with \
what they set out to build. Your job: turn the person's idea into a North Star through \
a short, friendly conversation.

Ask one or two questions at a time, in plain language, building on what they said. \
Cover, roughly in this order: what they want to build and why; who it's for; what a \
first usable version must do (what "done" looks like); what it should NOT become; \
constraints (time, money, skills, tools or platforms they want). Offer sensible \
suggestions instead of making them invent everything, and say what you'd recommend.

Aim to have enough within about {MAX_TURNS_HINT} questions. When you do, set ready=true, \
fill north_star and use reply to summarise it in two or three sentences and invite \
changes. Goals: 3-5, concrete enough that a work session can be judged as advancing \
them or not. Milestones: 3-6 small, ordered steps to a first usable version, all \
status not_started, evidence "Not started". If the person asks for changes after a \
draft, return an updated north_star with ready=true. Never ask about things they \
already answered."""


def chat(messages: list[dict], model: str | None = None) -> dict:
    """messages: [{"role": "user"|"bearing", "text": str}, ...]. Returns CHAT_SCHEMA."""
    if not messages:
        return {"reply": "What would you like to build? Describe it however it comes to mind "
                         "— a sentence or two is plenty.", "ready": False}
    transcript = "\n\n".join(
        f"{'PERSON' if m['role'] == 'user' else 'BEARING'}: {m['text']}" for m in messages)
    result = ask_claude(f"Conversation so far:\n\n{transcript}\n\nRespond as BEARING.",
                        CHAT_SCHEMA, SYSTEM_PROMPT, model, cwd=DATA_DIR)
    if not result.get("north_star"):
        result["ready"] = False
    return result


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "new-project"


def create(name: str, parent: Path, draft: dict) -> Path:
    """Make the project folder with a first commit, confirmed North Star and hooks."""
    name = slugify(name)
    parent = parent.expanduser().resolve()
    if not parent.is_dir():
        raise ValueError(f"{parent} isn't a folder.")
    project = parent / name
    if project.exists() and any(project.iterdir()):
        raise ValueError(f"{project} already exists and isn't empty. Pick another name.")
    if (DATA_DIR / name).exists():
        raise ValueError(f"Bearing already tracks a project called {name}. Pick another name.")

    project.mkdir(exist_ok=True)
    goals = "\n".join(f"- **{g['id']}:** {g['goal']}" for g in draft["goals"])
    (project / "README.md").write_text(
        f"# {name}\n\n{draft['one_liner']}\n\n## Goals\n\n{goals}\n\n"
        "_This project is tracked by Bearing, which keeps its North Star, Logbook and "
        "course map._\n")
    (project / ".gitignore").write_text(".DS_Store\n.env\n.claude/settings.local.json\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Start project with Bearing"], cwd=project,
                   check=True)

    star = {k: draft[k] for k in ("one_liner", "goals", "non_goals", "constraints",
                                  "milestones", "questions")}
    star.update(course_changes=[], status="confirmed", drafted_on=date.today().isoformat(),
                confirmed_on=date.today().isoformat(), started_with_bearing=True)
    out_dir = DATA_DIR / name
    out_dir.mkdir(parents=True)
    (out_dir / "project.json").write_text(json.dumps({"path": str(project)}))
    (out_dir / "north_star.json").write_text(json.dumps(star, indent=2))
    (out_dir / "NORTH_STAR.md").write_text(northstar.render_markdown(star, name))
    (out_dir / "logbook.json").write_text("{}")
    install_hooks(project)
    return project


KICKOFF = ("Bearing just set up this project. Read the Bearing briefing, then propose a short, "
           "plain-language plan for the first milestone: what you'd build first, what I'll be able "
           "to see or try at the end, and anything you need from me. Wait for my go-ahead before "
           "writing code.")


def launch_command(project: Path, first_session: bool) -> str:
    cmd = f"cd {shlex.quote(str(project))} && claude"
    if first_session:
        cmd += " " + shlex.quote(KICKOFF)
    return cmd


def launch(project: Path, first_session: bool):
    """Open Terminal (macOS) with Claude Code running in the project."""
    shell = launch_command(project, first_session)
    applescript = shell.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(["osascript",
                    "-e", f'tell application "Terminal" to do script "{applescript}"',
                    "-e", 'tell application "Terminal" to activate'],
                   check=True, capture_output=True)
