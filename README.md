# Bearing

**Keep what you build with Claude Code pointed at what you set out to build.**

Building a big project with an AI agent drifts. Every session starts fresh, you approve things you later forget, and thirty sessions in, you and the AI are aiming at different goals. Bearing watches your Claude Code sessions and keeps them lined up with your goals:

- **North Star:** your goals, non-goals and milestones. You confirm them once.
- **Logbook:** a plain-English entry for every sitting of work: what was built, what was decided (and whether *you* decided it or Claude did), and what was left open.
- **Course map:** each session rated against each goal, so you can see drift where it started.
- **Course correct:** accept a change, defer it, roll it back on a review branch, or get a ready-made prompt for Claude to undo it.
- **Briefing:** every new Claude Code session starts knowing your goals, what's off-limits and what was left open last time.

Everything runs on your Mac. There are no accounts and no servers.

## Requirements

- macOS (Linux works too, except the "Open in Claude Code" button)
- Python 3.10 or newer
- [Claude Code](https://claude.com/claude-code), installed and signed in
- git

## Install

The easiest way is [pipx](https://pipx.pypa.io), which installs Bearing in its own space so it doesn't interfere with anything else:

```bash
brew install pipx
pipx ensurepath
pipx install git+https://github.com/WilliamRacz/bearing
```

Open a new terminal window, then check your setup:

```bash
bearing doctor
```

It checks that everything is in place and tells you how to fix anything that isn't.

To update later: `pipx upgrade bearing`. To remove: `pipx uninstall bearing` (your data in `~/.bearing` stays until you delete it).

## Use it

```bash
bearing
```

This opens the app in your browser at http://127.0.0.1:7777. Leave the terminal open while you use it.

**Starting something new?** Click **+ New project → A new idea**. Bearing asks a few questions about what you're building, drafts your North Star and lets you adjust it. Then it creates the project folder and opens Claude Code there, already briefed.

**Already building something?** Click **+ New project → A project I'm already building** and pick the folder. Bearing reads its Claude Code history, writes the Logbook and drafts a North Star. Check the goals, keep or drop each change of direction it found, and confirm. Bearing then rates every past session against your goals.

After that, there's nothing extra to do. Use Claude Code as usual, and Bearing logs and checks each session in the background. Open the app to see the course map and handle anything flagged.

## What it costs

Bearing uses **your own Claude Code** (`claude -p`) to write Logbook entries and check drift, so it counts toward your existing Claude plan. Each new sitting of work takes about two short requests. Bringing in an existing project takes about two requests per sitting in its history.

The **Spend** view estimates what your sessions *would* cost at API prices. That's useful for comparing sessions, but it's not a bill.

## Your data

- Bearing reads Claude Code's own transcripts in `~/.claude/projects` and your project's git history.
- What Bearing writes is stored in `~/.bearing` (or set `BEARING_HOME` to put it elsewhere).
- Logbook and drift requests go through Claude Code, under the same terms as your normal Claude Code use. Nothing goes anywhere else.
- **Claude Code deletes transcripts after 30 days by default.** To give Bearing more history, add `"cleanupPeriodDays": 365` to `~/.claude/settings.json`.

## Briefing hooks

When you confirm a North Star with **Brief Claude Code automatically** ticked, or start a project in Bearing, two hooks are added to that project's `.claude/settings.local.json`:

- **SessionStart** gives Claude a short briefing: goals, non-goals, open milestones, what was left open recently and any drift flags. It also asks Claude to check with you before off-goal work, to label its own decisions with "Decision:", and to suggest commits.
- **SessionEnd** writes the new Logbook entry and drift check in the background.

To stop briefing a project, delete those two entries from its `.claude/settings.local.json`.

## Command line

Everything in the app is also available from the terminal:

| Command | What it does |
|---|---|
| `bearing` | Open the app |
| `bearing new` | Start a new project through a conversation |
| `bearing add <folder>` | Bring in an existing project (Logbook + draft North Star) |
| `bearing confirm <folder> [--reject 12]` | Confirm the North Star; course changes are kept unless rejected |
| `bearing drift <folder>` | Rate every leg against the North Star |
| `bearing correct <folder> <leg> [--accept/--defer/--rollback/--fix-prompt]` | Course correct a leg |
| `bearing brief <folder>` | Show what Claude Code is told at session start |
| `bearing install-hooks <folder>` | Turn on the briefing hooks |
| `bearing read-past <folder>` | List a project's legs with tokens and cost |
| `bearing doctor` | Check your setup |

## Words Bearing uses

| Term | Meaning |
|---|---|
| **Leg** | One sitting of work. A new leg starts after 3+ hours away. Long Claude Code sessions are split into legs. |
| **North Star** | Goals, non-goals, constraints and milestones. It changes only when you approve. |
| **Logbook** | One entry per leg: what changed, decisions, open items. |
| **Drift** | A leg's rating: on course, drifting, off course, earlier heading (on course for the plan *at the time*) or no progress. |
| **Course change** | A point where the project's direction changed. Keep it and it's part of the plan; drop it and later checks treat it as drift. |
| **Course correct** | Accept, defer, roll back, or ask Claude to fix a drifted leg. |

## Limitations

- Works with Claude Code only, for now.
- Bearing identifies projects by folder name, so two tracked folders can't share a name.
- You can't yet edit a drafted North Star's goals directly in the app. Reshape a new project's North Star through the conversation.
- Rollback only works for legs that have git commits. Commit often.

## License

MIT. See [LICENSE](LICENSE).
