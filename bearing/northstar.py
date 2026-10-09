"""North Star: draft a project's goals from its Logbook, then let the owner confirm.

"Find my bearing" for an existing project: Bearing reads every Logbook entry
plus the project's own docs, drafts goals, non-goals and milestones, and lists
the moments the course changed so the owner can accept or reject each one.
"""

import json
from datetime import date
from pathlib import Path

from .claude import ask_claude
from .history import Leg

DOC_CHARS = 25_000

SCHEMA = {
    "type": "object",
    "properties": {
        "one_liner": {"type": "string", "description": "What the project is, in one sentence."},
        "goals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "G1, G2, …"},
                    "goal": {"type": "string"},
                    "why": {"type": "string", "description": "Evidence from the Logbook or docs."},
                },
                "required": ["id", "goal", "why"],
            },
        },
        "non_goals": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "milestones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "status": {"type": "string", "enum": ["done", "in_progress", "not_started"]},
                    "evidence": {"type": "string"},
                },
                "required": ["name", "status", "evidence"],
            },
        },
        "course_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "leg": {"type": "integer"},
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "made_by": {"type": "string", "enum": ["you", "claude", "unclear"]},
                    "question": {"type": "string",
                                 "description": "A yes/no question for the owner: was this intended?"},
                },
                "required": ["leg", "from", "to", "made_by", "question"],
            },
            "description": "Legs where the project's direction meaningfully shifted, oldest first.",
        },
        "questions": {"type": "array", "items": {"type": "string"},
                      "description": "Things only the owner can answer that would sharpen the goals."},
    },
    "required": ["one_liner", "goals", "non_goals", "constraints", "milestones",
                 "course_changes", "questions"],
}

SYSTEM_PROMPT = """You draft a North Star for Bearing, a tool that keeps AI-built \
projects aligned with their owner's goals. You get a project's Logbook (one entry per \
work session, oldest first) and any planning docs in the project.

Describe where the project is heading NOW, based on the most recent legs and the most \
recent planning docs; earlier directions belong in course_changes, not goals. Write \
3-6 goals, each concrete enough that a session can be judged as advancing it or not. \
Non-goals should capture things the project explicitly ruled out or drifted into and \
then abandoned. Write in plain language for a non-technical owner. Don't invent facts; \
where the evidence is thin, ask in questions."""


def project_docs(project: Path) -> str:
    paths = [project / "README.md", project / "CLAUDE.md", project / "NORTH_STAR.md",
             *sorted((project / "docs").glob("*.md"))]
    chunks = []
    for p in paths:
        if p.is_file():
            text = p.read_text(errors="ignore")[:DOC_CHARS]
            chunks.append(f"--- {p.relative_to(project)} ---\n{text}")
    return "\n\n".join(chunks)


def logbook_digest(legs: list[Leg], entries: dict) -> str:
    out = []
    for leg in legs:
        e = entries.get(leg.number)
        if not e:
            continue
        decisions = "; ".join(f"{d['decision']} ({d['made_by']})" for d in e["decisions"])
        commits = ", ".join(f"{c['sha']} {c['subject']}" for c in leg.commits) or "none"
        out.append(
            f"Leg {leg.number} · {leg.start:%Y-%m-%d} · {e['headline']}\n"
            f"Summary: {e['summary']}\nDecisions: {decisions or 'none'}\n"
            f"Left open: {'; '.join(e['open_items']) or 'none'}\n"
            f"Direction: {e['direction']}\nCommits: {commits}")
    return "\n\n".join(out)


def draft(project: Path, legs: list[Leg], entries: dict, model: str | None, cwd: Path) -> dict:
    docs = project_docs(project)
    prompt = (f"Project folder: {project.name}\n\n=== Logbook ===\n{logbook_digest(legs, entries)}"
              + (f"\n\n=== Project docs ===\n{docs}" if docs else ""))
    star = ask_claude(prompt, SCHEMA, SYSTEM_PROMPT, model, cwd)
    star["status"] = "draft"
    star["drafted_on"] = date.today().isoformat()
    for change in star["course_changes"]:
        change["status"] = "pending"
    return star


def confirm(star: dict, reject: set[int]) -> dict:
    star["status"] = "confirmed"
    star["confirmed_on"] = date.today().isoformat()
    for change in star["course_changes"]:
        if change["status"] == "pending":
            change["status"] = "rejected" if change["leg"] in reject else "accepted"
    return star


def render_markdown(star: dict, project_name: str) -> str:
    status = "Confirmed " + star["confirmed_on"] if star["status"] == "confirmed" \
        else f"DRAFT ({star['drafted_on']}) — not yet confirmed"
    out = [f"# {project_name} — North Star", "", f"> {star['one_liner']}", "",
           f"**Status:** {status}", "", "## Goals", ""]
    out += [f"- **{g['id']}: {g['goal']}**  \n  _Why:_ {g['why']}" for g in star["goals"]]
    out += ["", "## Non-goals", ""] + [f"- {n}" for n in star["non_goals"]]
    out += ["", "## Constraints", ""] + [f"- {c}" for c in star["constraints"]]
    icons = {"done": "✅", "in_progress": "🔄", "not_started": "⬜"}
    out += ["", "## Milestones", ""] + [
        f"- {icons[m['status']]} **{m['name']}** — {m['evidence']}" for m in star["milestones"]]
    out += ["", "## Course changes", "", "| Leg | From | To | Who | Status |", "|---|---|---|---|---|"]
    out += [f"| {c['leg']} | {c['from']} | {c['to']} | {c['made_by']} | {c['status']} |"
            for c in star["course_changes"]]
    pending = [c for c in star["course_changes"] if c["status"] == "pending"]
    if pending:
        out += ["", "**To confirm:**", ""] + [f"- Leg {c['leg']}: {c['question']}" for c in pending]
    if star.get("deferred"):
        out += ["", "## Deferred", ""] + [f"- Leg {d['leg']}: {d['item']} _(deferred {d['on']})_"
                                          for d in star["deferred"]]
    if star["questions"]:
        out += ["", "## Open questions", ""] + [f"- {q}" for q in star["questions"]]
    return "\n".join(out) + "\n"


def load(store: Path) -> dict | None:
    return json.loads(store.read_text()) if store.exists() else None
