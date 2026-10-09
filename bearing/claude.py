"""Run Claude through the person's own Claude Code, returning structured output."""

import json
import subprocess
from pathlib import Path


def ask_claude(prompt: str, schema: dict, system: str, model: str | None = None,
               cwd: Path | None = None, timeout: int = 900) -> dict:
    """`claude -p` with no tools, no MCP and no saved session, validated against schema."""
    cmd = ["claude", "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
           "--system-prompt", system, "--tools", "", "--no-session-persistence",
           "--strict-mcp-config"]
    if model:
        cmd += ["--model", model]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=cwd,
                          timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip()[:500])
    result = json.loads(proc.stdout)
    if result.get("is_error"):
        raise RuntimeError(str(result.get("result"))[:500])
    output = result.get("structured_output")
    if output is None:
        output = json.loads(result["result"])
    return output
