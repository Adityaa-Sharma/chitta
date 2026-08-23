"""chitta - a local-first personal intelligence, kept deliberately small."""
import argparse
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

from . import __version__, ollama, smriti
from .config import DEFAULT_TIER, EMBED, TIERS, UI_PORT

PERSONA = (
    "You are Chitta. In Samkhya and the Yoga Sutras, chitta is the mind-substrate "
    "where impressions settle. You are that, for one person.\n"
    "Be direct and concrete. Prefer one good sentence to five hedging ones. "
    "You are allowed to be philosophical, but only when it earns its place - "
    "never as decoration on an ordinary answer."
)


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def need_server():
    if not ollama.alive():
        sys.exit("chitta: ollama is not responding on 127.0.0.1:11434\n"
                 "       try: open -a Ollama   (or: chitta doctor)")


def cmd_ask(args):
    need_server()
    tier = TIERS[args.tier]
    msgs = [{"role": "system", "content": PERSONA},
            {"role": "user", "content": " ".join(args.prompt)}]
    try:
        for chunk in ollama.chat(tier["model"], msgs,
                                 keep_alive=tier["keep_alive"],
                                 num_ctx=tier["num_ctx"],
                                 think=args.think):
            sys.stdout.write(chunk)
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    print()


def cmd_status(args):
    need_server()
    loaded = ollama.ps()
    print(f"\n  chitta {__version__}\n")
    if not loaded:
        print("  resident: nothing. 0 B of weights in memory.\n")
    else:
        total = sum(m.get("size_vram") or m.get("size") or 0 for m in loaded)
        print(f"  resident: {len(loaded)} model(s), {human(total)}\n")
        for m in loaded:
            sz = m.get("size_vram") or m.get("size") or 0
            print(f"    {m['name']:<28} {human(sz):>9}   until {m.get('expires_at','?')[:19]}")
        print()
    on_disk = {m["name"] for m in ollama.tags()}
    print("  tiers")
    for name, t in TIERS.items():
        mark = "*" if t["model"] in on_disk else "-"
        print(f"    {mark} {name:<8} {t['model']:<18} keep_alive={t['keep_alive']:<5} {t['role'][:44]}")
    mark = "*" if EMBED["model"] in on_disk else "-"
    print(f"    {mark} {'embed':<8} {EMBED['model']:<18}")
    print("\n  (* on disk, - not pulled)")
    try:
        st = smriti.stats(smriti.connect())
        print(f"\n  smriti   {st['claims_live']} live claims · {st['subjects']} subjects "
              f"· {st['superseded']} superseded · {st['episodes']} episodes\n")
    except Exception as e:
        print(f"\n  smriti   unavailable ({e})\n")


def cmd_models(args):
    need_server()
    models = sorted(ollama.tags(), key=lambda m: -(m.get("size") or 0))
    if not models:
        print("no models pulled yet")
        return
    print()
    for m in models:
        d = m.get("details", {})
        print(f"  {m['name']:<30} {human(m.get('size')):>9}  "
              f"{d.get('parameter_size','?'):>6}  {d.get('quantization_level','?')}")
    print(f"\n  {len(models)} models, {human(sum(m.get('size') or 0 for m in models))} on disk\n")


def cmd_unload(args):
    need_server()
    loaded = ollama.ps()
    if not loaded:
        print("nothing resident - already at zero")
        return
    targets = loaded if args.all else [m for m in loaded if m["name"] == args.model]
    if not targets:
        sys.exit(f"chitta: {args.model} is not resident")
    for m in targets:
        ollama.unload(m["name"])
        print(f"evicted {m['name']}  (freed {human(m.get('size_vram') or m.get('size'))})")


def cmd_ui(args):
    """Serve the dashboard on the one origin ollama trusts (see OLLAMA_ORIGINS)."""
    import functools, http.server, socketserver
    d = Path(__file__).resolve().parent / "ui"
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(d))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", UI_PORT), handler) as srv:
        url = f"http://127.0.0.1:{UI_PORT}/index.html"
        print(f"chitta ui -> {url}   (ctrl-c to stop)")
        webbrowser.open(url)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print()


def cmd_feed(args):
    need_server()
    text = " ".join(args.text) if args.text else sys.stdin.read()
    text = text.strip()
    if not text:
        sys.exit("chitta: nothing to feed")
    con = smriti.connect()
    print(f"reading {len(text)} chars via {args.tier}…")
    ep, added, superseded = smriti.feed(con, text, source=args.source,
                                        ref=args.ref, tier=args.tier)
    if not added:
        print("nothing durable in that. episode kept anyway.")
    for cid, c in added:
        print(f"  + {c['subject']} · {c['predicate']} · {c['object']}")
    if superseded:
        print(f"\n  {len(superseded)} earlier belief(s) superseded — "
              f"see: chitta contradictions")


def cmd_recall(args):
    con = smriti.connect()
    rows = smriti.recall(con, args.query, include_history=args.history)
    if not rows:
        print("nothing remembered yet" if not args.query else "no match")
        return
    print()
    for r in rows:
        dead = "" if r["valid_to"] is None else f"  (until {r['valid_to'][:10]})"
        print(f"  {r['subject']} · {r['predicate']} · {r['object']}"
              f"{dead}\n    {r['observed_at'][:10]}  conf={r['confidence']:.2f}")
    print()


def cmd_contradictions(args):
    con = smriti.connect()
    rows = smriti.contradictions(con)
    if not rows:
        print("no beliefs have changed yet")
        return
    print()
    for r in rows:
        print(f"  {r['subject']} · {r['predicate']}")
        print(f"    was  {r['was']}   (held from {r['held_from'][:10]})")
        print(f"    now  {r['now_is']}   (changed {r['changed_at'][:10]})\n")


def cmd_doctor(args):
    print("\n  chitta doctor\n")
    ok = ollama.alive()
    print(f"  {'PASS' if ok else 'FAIL'}  ollama reachable")
    if not ok:
        print("        -> open -a Ollama\n")
        return

    want = {
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "OLLAMA_FLASH_ATTENTION": "1",
        "OLLAMA_KV_CACHE_TYPE": "q8_0",
    }
    # read the env of the running server, not our own shell
    pid = subprocess.run(["pgrep", "-f", "ollama serve"], capture_output=True, text=True)
    env = ""
    if pid.stdout.strip():
        first = pid.stdout.split()[0]
        env = subprocess.run(["ps", "-p", first, "-wwE", "-o", "command="],
                             capture_output=True, text=True).stdout
    for k, v in want.items():
        good = f"{k}={v}" in env
        print(f"  {'PASS' if good else 'WARN'}  {k}={v}"
              + ("" if good else "   <- not set on the running server"))
    if not all(f"{k}={v}" in env for k, v in want.items()):
        print("\n        -> cp ops/com.chitta.ollama.plist ~/Library/LaunchAgents/ && "
              "launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.chitta.ollama.plist")
    print()


def main():
    p = argparse.ArgumentParser(prog="chitta", description=__doc__)
    p.add_argument("-v", "--version", action="version", version=f"chitta {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask", help="ask a question")
    a.add_argument("prompt", nargs="+")
    a.add_argument("-t", "--tier", choices=list(TIERS), default=DEFAULT_TIER)
    a.add_argument("--think", action="store_true", help="let it reason before answering")
    a.set_defaults(fn=cmd_ask)

    s = sub.add_parser("status", help="what is resident right now")
    s.set_defaults(fn=cmd_status)

    m = sub.add_parser("models", help="what is on disk")
    m.set_defaults(fn=cmd_models)

    u = sub.add_parser("unload", help="evict weights, free RAM")
    u.add_argument("model", nargs="?")
    u.add_argument("--all", action="store_true")
    u.set_defaults(fn=cmd_unload)

    ui = sub.add_parser("ui", help="open the dashboard")
    ui.set_defaults(fn=cmd_ui)

    f = sub.add_parser("feed", help="give it something to remember")
    f.add_argument("text", nargs="*", help="text, or omit to read stdin")
    f.add_argument("--source", default="note")
    f.add_argument("--ref")
    f.add_argument("-t", "--tier", choices=list(TIERS), default="work")
    f.set_defaults(fn=cmd_feed)

    r = sub.add_parser("recall", help="what it remembers")
    r.add_argument("query", nargs="?")
    r.add_argument("--history", action="store_true", help="include superseded beliefs")
    r.set_defaults(fn=cmd_recall)

    x = sub.add_parser("contradictions", help="where your beliefs changed")
    x.set_defaults(fn=cmd_contradictions)

    d = sub.add_parser("doctor", help="check the RAM-critical settings")
    d.set_defaults(fn=cmd_doctor)

    args = p.parse_args()
    if args.cmd == "unload" and not args.all and not args.model:
        p.error("give a model name or --all")
    args.fn(args)


if __name__ == "__main__":
    main()
