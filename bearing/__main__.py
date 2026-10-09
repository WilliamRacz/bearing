"""Bearing command line.

    bearing            (opens the app)
    bearing --help     (all commands)
"""

import argparse
import json
from pathlib import Path

from .history import read_past, to_json
from . import DATA_DIR, correct, doctor, drift, live, newproject, northstar, onboard, server
from .logbook import build_logbook, render_markdown


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def print_legs(legs, detail: bool):
    header = f"{'Leg':>4}  {'When':<22} {'Active':>7} {'Prompts':>7} {'Compact':>7} " \
             f"{'Output':>7} {'Est. $':>8}  Commits"
    print(header)
    print("-" * len(header))
    for leg in legs:
        when = f"{leg.start:%a %b %d %H:%M}–{leg.end:%H:%M}"
        hours, mins = divmod(round(leg.active_seconds / 60), 60)
        active = f"{hours}h{mins:02d}" if hours else f"{mins}m"
        cost = f"${leg.cost_usd:,.2f}" + ("*" if leg.unpriced_models else "")
        print(f"{leg.number:>4}  {when:<22} {active:>7} {len(leg.prompts):>7} "
              f"{leg.compactions:>7} {fmt_tokens(leg.tokens['output']):>7} {cost:>8}  "
              f"{len(leg.commits) or ''}")
        if detail:
            if leg.prompts:
                print(f"        first prompt: {leg.prompts[0][:110]!r}")
            if leg.intent:
                print(f"        intent: {leg.intent[:200]}")
            for c in leg.commits:
                print(f"        commit {c['sha']}: {c['subject']}")
            if leg.files_touched:
                print(f"        files edited: {len(leg.files_touched)}")
            print()

    total_cost = sum(l.cost_usd for l in legs)
    total_out = sum(l.tokens["output"] for l in legs)
    total_prompts = sum(len(l.prompts) for l in legs)
    unpriced = sorted(set().union(*(l.unpriced_models for l in legs)))
    print("-" * len(header))
    print(f"{len(legs)} legs · {total_prompts} prompts · {fmt_tokens(total_out)} output tokens · "
          f"~${total_cost:,.2f} at API prices")
    if unpriced:
        print(f"* not priced (unknown model): {', '.join(unpriced)}")
    no_commit = [l.number for l in legs if not l.commits]
    if no_commit:
        print(f"Legs with no git commit (no checkpoint to roll back to): {len(no_commit)} of {len(legs)}")


def main():
    parser = argparse.ArgumentParser(prog="bearing")
    sub = parser.add_subparsers(dest="command")
    rp = sub.add_parser("read-past", help="Split a project's Claude Code history into legs")
    rp.add_argument("project", type=Path)
    rp.add_argument("--detail", action="store_true", help="Show prompts, intent and commits per leg")
    lb = sub.add_parser("logbook", help="Write a plain-English Logbook entry for each leg")
    lb.add_argument("project", type=Path)
    lb.add_argument("--legs", help="Only these legs, e.g. 4,20,25")
    lb.add_argument("--redo", action="store_true", help="Rewrite entries even if cached")
    lb.add_argument("--model", help="Claude model for summaries (default: your Claude Code default)")
    fb = sub.add_parser("find-bearing", help="Draft a North Star from the Logbook and project docs")
    fb.add_argument("project", type=Path)
    fb.add_argument("--model")
    cf = sub.add_parser("confirm", help="Confirm the North Star; course changes are accepted unless rejected")
    cf.add_argument("project", type=Path)
    cf.add_argument("--reject", default="", help="Legs whose course change you reject, e.g. 25")
    dr = sub.add_parser("drift", help="Rate every leg against the North Star")
    dr.add_argument("project", type=Path)
    dr.add_argument("--draft", action="store_true", help="Allow an unconfirmed North Star")
    dr.add_argument("--model")
    up = sub.add_parser("update", help="Bring Logbook and drift up to date (what the hooks run)")
    up.add_argument("project", type=Path)
    br = sub.add_parser("brief", help="Print the briefing Claude Code gets at session start")
    br.add_argument("project", type=Path)
    ih = sub.add_parser("install-hooks", help="Add Bearing's hooks to a project's Claude Code settings")
    ih.add_argument("project", type=Path)
    co = sub.add_parser("correct", help="Course correct a leg: show options, accept, defer, roll back")
    co.add_argument("project", type=Path)
    co.add_argument("leg", type=int)
    action = co.add_mutually_exclusive_group()
    action.add_argument("--accept", metavar="WHY", help="It was intended; record it in the North Star")
    action.add_argument("--defer", metavar="WHAT", help="Leave it for later")
    action.add_argument("--rollback", action="store_true", help="Revert the leg's commits on a review branch")
    action.add_argument("--fix-prompt", action="store_true", help="Print an instruction for Claude Code")
    sv = sub.add_parser("serve", help="Open the Bearing web app on this computer (the default)")
    sv.add_argument("--port", type=int, default=7777)
    sv.add_argument("--no-open", action="store_true", help="Don't open a browser tab")
    ad = sub.add_parser("add", help="Bring an existing project in: Logbook + draft North Star")
    ad.add_argument("project", type=Path)
    ad.add_argument("--model")
    sub.add_parser("doctor", help="Check that everything Bearing needs is set up")
    nw = sub.add_parser("new", help="Start a new project: talk it through, then launch Claude Code")
    nw.add_argument("--in", dest="parent", type=Path, default=Path.home() / "Desktop",
                    help="Folder to create the project in (default: Desktop)")
    hk = sub.add_parser("hook", help="Entry point for Claude Code hooks (reads JSON on stdin)")
    hk.add_argument("event", choices=["session-start", "session-end"])
    args = parser.parse_args()

    if args.command == "hook":
        live.handle_hook(args.event)
        return
    if args.command in (None, "serve"):
        server.serve(getattr(args, "port", 7777), open_browser=not getattr(args, "no_open", False))
        return
    if args.command == "doctor":
        raise SystemExit(doctor.run())
    if args.command == "add":
        project = args.project.expanduser().resolve()
        problem = onboard.check_project(project)
        if problem:
            exit(problem)
        onboard.run(project, args.model)
        print(f"\nDraft North Star saved. Review it in the app (bearing), or here:\n"
              f"  {(DATA_DIR / project.name / 'NORTH_STAR.md').as_posix().replace(str(Path.home()), '~')}\n"
              f"Then: bearing confirm {project}   (add --reject 12 to reject a course change)")
        return
    if args.command == "new":
        run_new(args.parent)
        return

    project = args.project.expanduser().resolve()
    server.register(project)
    if args.command == "update":
        live.run_update_locked(project)
        return
    if args.command == "brief":
        print(live.briefing(project) or "No confirmed North Star yet.")
        return
    if args.command == "correct":
        run_correct(args, project)
        return
    if args.command == "install-hooks":
        print(f"Hooks added to {live.install_hooks(project)}")
        return

    legs = read_past(project)
    out_dir = DATA_DIR / project.name
    out_dir.mkdir(parents=True, exist_ok=True)

    if not legs and args.command in ("read-past", "logbook", "find-bearing", "drift"):
        exit(f"No Claude Code sessions found for {project} yet.")
    if args.command == "read-past":
        print_legs(legs, args.detail)
        out = out_dir / "legs.json"
        out.write_text(json.dumps(to_json(legs), indent=2))
        print(f"\nSaved {out.as_posix().replace(str(Path.home()), '~')}")
    elif args.command == "logbook":
        only = {int(n) for n in args.legs.split(",")} if args.legs else None
        entries, _ = build_logbook(legs, project.name, out_dir / "logbook.json", only,
                                args.redo, args.model)
        md = out_dir / "LOGBOOK.md"
        md.write_text(render_markdown(legs, entries, project.name))
        print(f"\nSaved {md.as_posix().replace(str(Path.home()), '~')}")
    else:
        star_store = out_dir / "north_star.json"
        star_md = out_dir / "NORTH_STAR.md"
        entries = load_entries(out_dir)
        if args.command == "find-bearing":
            print(f"Drafting North Star from {len(entries)} Logbook entries…")
            star = northstar.draft(project, legs, entries, args.model, out_dir)
        elif args.command == "confirm":
            star = northstar.load(star_store) or exit("No North Star yet — run find-bearing first.")
            star = northstar.confirm(star, {int(n) for n in args.reject.split(",") if n})
        if args.command in ("find-bearing", "confirm"):
            star_store.write_text(json.dumps(star, indent=2))
            star_md.write_text(northstar.render_markdown(star, project.name))
            print(f"Saved {star_md.as_posix().replace(str(Path.home()), '~')} ({star['status']})")
        elif args.command == "drift":
            star = northstar.load(star_store) or exit("No North Star yet — run find-bearing first.")
            if star["status"] != "confirmed" and not args.draft:
                exit("North Star is still a draft — confirm it first, or pass --draft.")
            print(f"Rating {len(entries)} legs against the North Star…")
            ratings = drift.score(legs, entries, star, args.model, out_dir)
            (out_dir / "drift.json").write_text(json.dumps(ratings, indent=2))
            md = out_dir / "COURSE_MAP.md"
            md.write_text(drift.render_markdown(legs, entries, ratings, star, project.name))
            print(f"Saved {md.as_posix().replace(str(Path.home()), '~')}")


def run_new(parent: Path):
    messages = []
    reply = newproject.chat(messages)
    while True:
        print(f"\nBearing: {reply['reply']}\n")
        if reply.get("ready"):
            draft = reply["north_star"]
            preview = {**draft, "course_changes": [], "status": "draft",
                       "drafted_on": "now"}
            print(northstar.render_markdown(preview, draft["suggested_name"]))
            answer = input("Approve this North Star? [y] or type what to change: ").strip()
            if answer.lower() in ("", "y", "yes"):
                break
        else:
            answer = input("You: ").strip()
            if not answer:
                continue
        messages += [{"role": "bearing", "text": reply["reply"]}, {"role": "user", "text": answer}]
        print("…")
        reply = newproject.chat(messages)

    name = input(f"Project folder name [{draft['suggested_name']}]: ").strip() or draft["suggested_name"]
    try:
        project = newproject.create(name, parent, draft)
    except ValueError as exc:
        exit(str(exc))
    print(f"Created {project} with a first commit, the North Star and Bearing's hooks.")
    if input("Open Claude Code there now? [Y/n] ").strip().lower() in ("", "y", "yes"):
        newproject.launch(project, first_session=True)
        print("Opened in Terminal.")
    else:
        print(f"Later: {newproject.launch_command(project, first_session=True)}")


def run_correct(args, project: Path):
    action = ("fix-prompt" if args.fix_prompt else "accept" if args.accept
              else "defer" if args.defer else "rollback" if args.rollback else None)
    try:
        if action is None:
            st = correct.load_state(project)
            leg = next((l for l in st["legs"] if l.number == args.leg), None) \
                or exit(f"No leg {args.leg}.")
            deps = correct.dependents(leg, st["legs"], project)
            print(correct.describe(leg, st["entries"][leg.number], st["ratings"].get(leg.number),
                                   deps, st["corrections"].get(leg.number)))
            return
        result = correct.apply_correction(project, args.leg, action,
                                          args.accept or args.defer or "")
    except correct.CorrectionError as exc:
        exit(str(exc))
    if "prompt" in result:
        print(result["prompt"])
    else:
        print(result["message"])


def load_entries(out_dir: Path) -> dict:
    store = out_dir / "logbook.json"
    if not store.exists():
        exit("No Logbook yet — run logbook first.")
    return {int(k): v["entry"] for k, v in json.loads(store.read_text()).items()}


if __name__ == "__main__":
    main()
