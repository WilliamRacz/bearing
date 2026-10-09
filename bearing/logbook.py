"""Logbook: a plain-English entry for every leg, written by Claude.

Runs through the person's own Claude Code (`claude -p`) so it counts against
their existing plan instead of needing an API key. Entries are cached in
data/<project>/logbook.json and only regenerated with --redo.
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .claude import ask_claude
from .history import Leg

MAX_MESSAGE_CHARS = 3_000
MAX_LEG_CHARS = 120_000

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One short line, like a commit title."},
        "summary": {"type": "string", "description": "2-4 plain-English sentences a non-engineer understands."},
        "built": {"type": "array", "items": {"type": "string"},
                  "description": "What was built, changed or fixed."},
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string"},
                    "made_by": {"type": "string", "enum": ["you", "claude", "unclear"]},
                },
                "required": ["decision", "made_by"],
            },
            "description": "Choices that shape the project. made_by=you if the person asked for or approved it; claude if Claude chose it without being asked.",
        },
        "open_items": {"type": "array", "items": {"type": "string"},
                       "description": "Unfinished, broken or promised-for-later work at the end of the leg."},
        "direction": {"type": "string",
                      "description": "One sentence: what the project appeared to be aiming at during this leg."},
    },
    "required": ["headline", "summary", "built", "decisions", "open_items", "direction"],
}

SYSTEM_PROMPT = """You write Logbook entries for Bearing, a tool that helps people keep \
AI-built projects aligned with their goals. You are given one leg (one sitting) of a \
Claude Code session: the person's prompts, Claude's replies, files edited and commits.

Write for the project owner, who may not be technical. Use plain language, no jargon \
unless the person used it. Be specific: name features, screens and files when it helps. \
Only report what the transcript shows; never invent work. Pay special attention to \
decisions — especially ones Claude made on its own or the person approved with a quick \
"yes" — because forgotten decisions are what cause projects to drift."""


def fingerprint(leg: Leg) -> str:
    return f"{leg.start.isoformat()}|{len(leg.prompts)}|{len(leg.conversation)}"


def leg_prompt(leg: Leg, project_name: str, previous: dict | None) -> str:
    lines = [f"Project folder: {project_name}",
             f"Leg {leg.number}: {leg.start:%a %b %d %Y %H:%M} to {leg.end:%H:%M}"]
    if previous:
        lines.append(f"Previous leg's direction: {previous['direction']}")
    if leg.intent:
        lines.append(f"Claude Code's own context summary during this leg said the intent was: {leg.intent}")
    if leg.commits:
        lines.append("Commits: " + "; ".join(f"{c['sha']} {c['subject']}" for c in leg.commits))
    if leg.files_touched:
        lines.append("Files edited: " + ", ".join(sorted(leg.files_touched)[:40]))
    lines.append("\n--- Conversation ---")

    body, used = [], 0
    for role, text in leg.conversation:
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[:MAX_MESSAGE_CHARS] + " […trimmed]"
        entry = f"[{'PERSON' if role == 'user' else 'CLAUDE'}] {text}"
        if used + len(entry) > MAX_LEG_CHARS:
            body.append("[…rest of leg trimmed for length]")
            break
        body.append(entry)
        used += len(entry)
    return "\n".join(lines) + "\n" + "\n\n".join(body)


def build_logbook(legs: list[Leg], project_name: str, store: Path, only: set[int] | None,
                  redo: bool, model: str | None, workers: int = 4,
                  progress=None) -> tuple[dict, set[int]]:
    """Write missing or stale entries. Returns (all entries, leg numbers written now)."""
    book = json.loads(store.read_text()) if store.exists() else {}
    todo = [leg for leg in legs
            if (only is None or leg.number in only)
            and leg.conversation
            and (redo or book.get(str(leg.number), {}).get("fingerprint") != fingerprint(leg))]

    by_number = {leg.number: leg for leg in legs}
    print(f"Writing {len(todo)} Logbook entries ({len(legs) - len(todo)} cached or skipped)…")

    def run(leg: Leg):
        prev = book.get(str(leg.number - 1), {}).get("entry")
        return leg, ask_claude(leg_prompt(leg, project_name, prev), SCHEMA, SYSTEM_PROMPT,
                               model, store.parent)

    written: set[int] = set()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run, leg) for leg in todo]
        for fut in as_completed(futures):
            try:
                leg, entry = fut.result()
            except Exception as exc:  # keep going; report the failure
                print(f"  ! failed: {exc}")
                continue
            book[str(leg.number)] = {"fingerprint": fingerprint(leg), "entry": entry}
            store.write_text(json.dumps(book, indent=2))
            written.add(leg.number)
            if progress:
                progress(len(written), len(todo))
            print(f"  leg {leg.number:>2}: {entry['headline']}")

    return {int(k): v["entry"] for k, v in book.items() if int(k) in by_number}, written


def render_markdown(legs: list[Leg], entries: dict, project_name: str) -> str:
    out = [f"# {project_name} — Logbook", ""]
    for leg in legs:
        entry = entries.get(leg.number)
        if not entry:
            continue
        cost = f"~${leg.cost_usd:,.2f}"
        out += [f"## Leg {leg.number} · {leg.start:%a %b %d} · {entry['headline']}",
                f"_{leg.start:%H:%M}–{leg.end:%H:%M} · {len(leg.prompts)} prompts · {cost} at API prices"
                + (f" · commits: {', '.join(c['sha'] for c in leg.commits)}" if leg.commits else " · no commit")
                + "_", "", entry["summary"], ""]
        if entry["built"]:
            out += ["**Built / changed**"] + [f"- {b}" for b in entry["built"]] + [""]
        if entry["decisions"]:
            out += ["**Decisions**"] + [
                f"- {d['decision']} _({'you' if d['made_by'] == 'you' else 'Claude, unasked' if d['made_by'] == 'claude' else 'unclear who'})_"
                for d in entry["decisions"]] + [""]
        if entry["open_items"]:
            out += ["**Left open**"] + [f"- {o}" for o in entry["open_items"]] + [""]
        out += [f"**Heading:** {entry['direction']}", ""]
    return "\n".join(out)
