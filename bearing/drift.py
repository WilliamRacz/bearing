"""Drift: score each leg against the North Star, per goal."""

import json
from concurrent.futures import ThreadPoolExecutor

from .claude import ask_claude
from .history import Leg

CHUNK = 10

SCHEMA = {
    "type": "object",
    "properties": {
        "legs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "leg": {"type": "integer"},
                    "rating": {"type": "string",
                               "enum": ["on_course", "drifting", "off_course", "earlier_heading", "no_progress"]},
                    "goals_advanced": {"type": "array", "items": {"type": "string"},
                                       "description": "Goal ids this leg moved forward."},
                    "off_goal": {"type": "array", "items": {"type": "string"},
                                 "description": "Work that serves no goal."},
                    "against": {"type": "array", "items": {"type": "string"},
                                "description": "Work that conflicts with a non-goal or constraint."},
                    "note": {"type": "string", "description": "One plain-English sentence explaining the rating."},
                },
                "required": ["leg", "rating", "goals_advanced", "off_goal", "against", "note"],
            },
        }
    },
    "required": ["legs"],
}

SYSTEM_PROMPT = """You check AI-built project sessions ("legs") against the project's \
North Star for Bearing. For each leg, rate it:

- on_course: mainly advanced one or more goals.
- drifting: some goal progress, but meaningful work that serves no goal, or a decision \
that quietly changes the project.
- off_course: mostly work that serves no goal, or work that conflicts with a non-goal \
or constraint.
- earlier_heading: the leg happened before an ACCEPTED course change and was on course \
for the direction at that time. Don't call it drift.
- no_progress: setup, troubleshooting or discussion with no real change.

Judge legs before a REJECTED course change by today's goals. Be fair and specific; \
the owner will use this to decide whether to roll back. One sentence per note, plain \
language."""


def leg_block(leg: Leg, entry: dict) -> str:
    decisions = "; ".join(f"{d['decision']} ({d['made_by']})" for d in entry["decisions"])
    return (f"Leg {leg.number} · {leg.start:%Y-%m-%d} · {entry['headline']}\n"
            f"Summary: {entry['summary']}\nBuilt: {'; '.join(entry['built'])}\n"
            f"Decisions: {decisions or 'none'}\nDirection: {entry['direction']}")


def score(legs: list[Leg], entries: dict, star: dict, model: str | None, cwd) -> dict[int, dict]:
    star_text = json.dumps({k: star[k] for k in
                            ("one_liner", "goals", "non_goals", "constraints", "course_changes")},
                           indent=1)
    scored_legs = [l for l in legs if l.number in entries]
    chunks = [scored_legs[i:i + CHUNK] for i in range(0, len(scored_legs), CHUNK)]

    def run(chunk):
        prompt = (f"=== North Star ===\n{star_text}\n\n=== Legs to rate ===\n"
                  + "\n\n".join(leg_block(l, entries[l.number]) for l in chunk))
        return ask_claude(prompt, SCHEMA, SYSTEM_PROMPT, model, cwd)["legs"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [r for rs in pool.map(run, chunks) for r in rs]
    return {r["leg"]: r for r in results}


ICONS = {"on_course": "✅", "drifting": "⚠️", "off_course": "❌",
         "earlier_heading": "🧭", "no_progress": "·"}
LABELS = {"on_course": "On course", "drifting": "Drifting", "off_course": "Off course",
          "earlier_heading": "Earlier heading", "no_progress": "No progress"}


CORRECTION_LABELS = {"accept": "✔ accepted by you", "defer": "⏸ deferred",
                     "rollback": "↩ rolled back", "fix-prompt": "🛠 fix requested"}


def render_markdown(legs: list[Leg], entries: dict, ratings: dict, star: dict,
                    project_name: str, corrections: dict | None = None) -> str:
    corrections = corrections or {}
    counts = {k: sum(1 for r in ratings.values() if r["rating"] == k) for k in ICONS}
    goal_hits = {g["id"]: sum(1 for r in ratings.values() if g["id"] in r["goals_advanced"])
                 for g in star["goals"]}
    out = [f"# {project_name} — Course map", "",
           " · ".join(f"{ICONS[k]} {LABELS[k]}: {n}" for k, n in counts.items() if n), "",
           "**Legs that advanced each goal:** "
           + " · ".join(f"{gid} {n}" for gid, n in goal_hits.items()), "",
           "| Leg | Date | | Goals | Headline | Note |", "|---|---|---|---|---|---|"]
    changes = {c["leg"]: c for c in star["course_changes"]}
    for leg in legs:
        r = ratings.get(leg.number)
        if not r:
            continue
        if leg.number in changes:
            c = changes[leg.number]
            out.append(f"| | | 🔀 | | **Course change ({c['status']}):** {c['from']} → {c['to']} | |")
        fix = corrections.get(leg.number)
        fixed = f" **[{CORRECTION_LABELS[fix['action']]} {fix['on']}]**" if fix else ""
        out.append(f"| {leg.number} | {leg.start:%b %d} | {ICONS[r['rating']]} | "
                   f"{', '.join(r['goals_advanced']) or '—'} | {entries[leg.number]['headline']} | "
                   f"{r['note']}{fixed} |")
    flagged = [r for r in ratings.values() if r["off_goal"] or r["against"]]
    if flagged:
        out += ["", "## Flags", ""]
        for r in sorted(flagged, key=lambda r: r["leg"]):
            if r["leg"] in corrections:
                out.append(f"- Leg {r['leg']}: {CORRECTION_LABELS[corrections[r['leg']]['action']]}")
                continue
            for item in r["against"]:
                out.append(f"- ❌ Leg {r['leg']}: {item}")
            for item in r["off_goal"]:
                out.append(f"- ⚠️ Leg {r['leg']}: {item}")
    return "\n".join(out) + "\n"
