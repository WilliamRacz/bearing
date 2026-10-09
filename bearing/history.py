"""Read a project's past: Claude Code transcripts + git history, split into legs.

A leg is one sitting of work: a run of activity with no gap longer than
LEG_GAP. Long Claude Code sessions (weeks, many compactions) are split into
legs so each can get its own Logbook entry and drift check.
"""

import json
import re
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
LEG_GAP = timedelta(hours=3)
IDLE_CUTOFF = timedelta(minutes=15)

# USD per million tokens. Cache writes: 5m = 1.25x input, 1h = 2x input.
PRICING = {
    "claude-opus-4-6": {"input": 5.00, "output": 25.00, "cache_read": 0.50},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20},
}

FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


@dataclass
class Leg:
    number: int
    start: datetime
    end: datetime
    active_seconds: float = 0.0
    prompts: list = field(default_factory=list)
    compactions: int = 0
    intent: str = ""
    tokens: dict = field(default_factory=lambda: {
        "input": 0, "output": 0, "cache_write": 0, "cache_read": 0})
    cost_usd: float = 0.0
    unpriced_models: set = field(default_factory=set)
    files_touched: set = field(default_factory=set)
    commits: list = field(default_factory=list)
    conversation: list = field(default_factory=list)  # (role, text), main session only


def transcript_dir(project: Path) -> Path:
    return CLAUDE_PROJECTS / re.sub(r"[^A-Za-z0-9]", "-", str(project))


def parse_time(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()


def read_jsonl(path: Path):
    with open(path) as f:
        for line in f:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def prompt_text(record) -> str | None:
    """Return the text of a prompt the person typed, or None."""
    if record.get("type") != "user" or record.get("isMeta") or record.get("isCompactSummary"):
        return None
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(b.get("type") == "tool_result" for b in content):
            return None
        content = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not text or text.startswith("<") or text.startswith("[Request interrupted"):
        return None
    return text


def intent_from_summary(summary: str) -> str:
    match = re.search(r"Primary Request and Intent:\s*(.+?)(?:\n\s*2\.|\Z)", summary, re.S)
    text = match.group(1) if match else summary
    return " ".join(text.split())[:400]


def usage_cost(model: str, usage: dict) -> float | None:
    price = PRICING.get(model)
    if price is None:
        return None
    per = 1_000_000
    cache = usage.get("cache_creation") or {}
    write_5m = cache.get("ephemeral_5m_input_tokens", 0)
    write_1h = cache.get("ephemeral_1h_input_tokens", 0)
    if not cache:
        write_5m = usage.get("cache_creation_input_tokens", 0)
    return (
        usage.get("input_tokens", 0) * price["input"]
        + usage.get("output_tokens", 0) * price["output"]
        + usage.get("cache_read_input_tokens", 0) * price["cache_read"]
        + write_5m * price["input"] * 1.25
        + write_1h * price["input"] * 2
    ) / per


def collect_events(tdir: Path):
    """Flatten all transcripts (incl. subagents) into timestamped events."""
    events = []
    seen_responses = set()
    files = sorted(tdir.glob("*.jsonl")) + sorted(tdir.glob("*/subagents/*.jsonl"))
    for path in files:
        is_subagent = "subagents" in path.parts
        for r in read_jsonl(path):
            ts = r.get("timestamp")
            if not ts:
                continue
            when = parse_time(ts)
            msg = r.get("message") or {}
            event = {"time": when, "kind": "activity"}

            if r.get("isCompactSummary"):
                content = msg.get("content")
                if not isinstance(content, str):
                    content = json.dumps(content)
                event.update(kind="compaction", intent=intent_from_summary(content))
            elif not is_subagent and (text := prompt_text(r)):
                event.update(kind="prompt", text=text)
            elif r.get("type") == "assistant" and isinstance(msg, dict):
                rid = msg.get("id") or r.get("requestId")
                if msg.get("usage") and rid not in seen_responses:
                    seen_responses.add(rid)
                    event.update(kind="usage", model=msg.get("model"), usage=msg["usage"])
                for block in msg.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and not is_subagent and block.get("text", "").strip():
                        events.append({"time": when, "kind": "reply", "text": block["text"].strip()})
                    if block.get("type") == "tool_use" and block.get("name") in FILE_TOOLS:
                        fp = (block.get("input") or {}).get("file_path")
                        if fp:
                            events.append({"time": when, "kind": "file", "path": fp})
            events.append(event)
    events.sort(key=lambda e: e["time"])
    return events


def git_commits(project: Path):
    try:
        out = subprocess.run(
            ["git", "-C", str(project), "log", "--reverse", "--format=%h%x1f%aI%x1f%s"],
            capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    commits = []
    for line in out.splitlines():
        sha, date, subject = line.split("\x1f", 2)
        commits.append({"sha": sha, "time": datetime.fromisoformat(date).astimezone(),
                        "subject": subject})
    return commits


def split_legs(events) -> list[Leg]:
    legs: list[Leg] = []
    last = None
    for e in events:
        t = e["time"]
        if last is None or t - last > LEG_GAP:
            legs.append(Leg(number=len(legs) + 1, start=t, end=t))
        elif t - last <= IDLE_CUTOFF:
            legs[-1].active_seconds += (t - last).total_seconds()
        leg = legs[-1]
        leg.end = t
        last = t

        kind = e["kind"]
        if kind == "prompt":
            leg.prompts.append(e["text"])
            leg.conversation.append(("user", e["text"]))
        elif kind == "reply":
            leg.conversation.append(("claude", e["text"]))
        elif kind == "compaction":
            leg.compactions += 1
            leg.intent = e["intent"]
        elif kind == "file":
            leg.files_touched.add(e["path"])
        elif kind == "usage":
            u = e["usage"]
            leg.tokens["input"] += u.get("input_tokens", 0)
            leg.tokens["output"] += u.get("output_tokens", 0)
            leg.tokens["cache_write"] += u.get("cache_creation_input_tokens", 0)
            leg.tokens["cache_read"] += u.get("cache_read_input_tokens", 0)
            cost = usage_cost(e["model"], u)
            if cost is None:
                if e["model"] and not e["model"].startswith("<"):
                    leg.unpriced_models.add(e["model"])
            else:
                leg.cost_usd += cost
    # Drop legs with no prompts and no usage (e.g. metadata-only blips).
    legs = [l for l in legs if l.prompts or l.tokens["output"]]
    for i, leg in enumerate(legs, 1):
        leg.number = i
    return legs


def attach_commits(legs: list[Leg], commits):
    """A commit belongs to the leg it falls in, or the most recent leg before it."""
    for c in commits:
        owner = None
        for leg in legs:
            if leg.start <= c["time"]:
                owner = leg
        (owner or legs[0]).commits.append(c)


def read_past(project: Path) -> list[Leg]:
    tdir = transcript_dir(project)
    if not tdir.exists():
        return []  # no Claude Code sessions yet (e.g. a project just started with Bearing)
    legs = split_legs(collect_events(tdir))
    if legs:
        attach_commits(legs, git_commits(project))
    return legs


def to_json(legs: list[Leg]) -> list[dict]:
    rows = []
    for leg in legs:
        d = asdict(leg)
        d["active_minutes"] = round(d.pop("active_seconds") / 60)
        d.pop("conversation")
        d["start"] = leg.start.isoformat()
        d["end"] = leg.end.isoformat()
        d["cost_usd"] = round(leg.cost_usd, 2)
        d["unpriced_models"] = sorted(leg.unpriced_models)
        d["files_touched"] = sorted(leg.files_touched)
        d["commits"] = [{**c, "time": c["time"].isoformat()} for c in leg.commits]
        rows.append(d)
    return rows
