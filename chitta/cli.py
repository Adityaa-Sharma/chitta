"""chitta - a local-first personal intelligence, kept deliberately small."""
import argparse
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

from . import __version__, ollama, sanjaya, shruti, smriti
from .config import CONFIG, CONFIG_PATH, DEFAULT_TIER, DEFAULTS, EMBED, TIERS, UI_PORT

PERSONA = CONFIG["persona"]["prompt"]


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
    system = PERSONA
    if CONFIG["ask"]["use_memory"] and not args.no_memory:
        mem = smriti.context(smriti.connect())
        if mem:
            system += "\n\n" + mem
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": " ".join(args.prompt)}]
    buf = []
    try:
        for chunk in ollama.chat(tier["model"], msgs,
                                 keep_alive=tier["keep_alive"],
                                 num_ctx=tier["num_ctx"],
                                 think=args.think):
            sys.stdout.write(chunk)
            sys.stdout.flush()
            buf.append(chunk)
    except KeyboardInterrupt:
        pass
    print()
    if args.speak:
        shruti.speak("".join(buf))


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
    """Serve the dashboard on the one origin ollama trusts (see OLLAMA_ORIGINS).

    Static files plus one JSON route, because smriti lives in a sqlite file the
    browser cannot open. ~15 lines beats standing up a second service.
    """
    import functools, http.server, json as _json, socketserver
    d = Path(__file__).resolve().parent / "ui"

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            route = self.path.split("?")[0]
            if route not in ("/api/smriti", "/api/graph", "/api/config", "/api/context"):
                return super().do_GET()
            try:
                con = smriti.connect()
                if route == "/api/graph":
                    body = _json.dumps(smriti.graph(con)).encode()
                elif route == "/api/config":
                    # the dashboard reads tiers/budget from here rather than
                    # keeping a second copy that drifts out of sync
                    body = _json.dumps({"tiers": CONFIG["tiers"], "ui": CONFIG["ui"],
                                        "persona": CONFIG["persona"]["prompt"]}).encode()
                elif route == "/api/context":
                    body = _json.dumps({"context": smriti.context(con)}).encode()
                else:
                    body = _json.dumps({
                        "stats": smriti.stats(con),
                        "claims": [dict(r) for r in smriti.recall(con, limit=12)],
                        "contradictions": [dict(r) for r in smriti.contradictions(con, limit=6)],
                    }).encode()
                code = 200
            except Exception as e:
                body, code = _json.dumps({"error": str(e)}).encode(), 500
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass  # a polling dashboard would otherwise spam the terminal

    handler = functools.partial(Handler, directory=str(d))
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


def _report(added, superseded, stored=None):
    if stored is not None:
        print(f"  {len(stored)} new episode(s)")
    seen = set()
    for c in added:
        k = (c["subject"], c["predicate"])
        if k in seen:
            continue
        seen.add(k)
        print(f"  + {c['subject']} · {c['predicate']} · {c['object']}")
    if not added:
        print("  nothing durable found")
    if superseded:
        print(f"\n  {len(superseded)} earlier belief(s) superseded — chitta contradictions")


def cmd_ingest(args):
    need_server()
    con = smriti.connect()
    if args.what == "claude":
        eps = sanjaya.claude_sessions()
        digest = False
    else:
        try:
            eps = sanjaya.gmail(days=args.days, limit=args.limit)
        except RuntimeError as e:
            sys.exit(f"chitta: {e}")
        digest = True
    print(f"ingesting {args.what}…")
    try:
        stored, added, sup = sanjaya.ingest(con, eps, tier=args.tier, digest=digest)
    except RuntimeError as e:
        sys.exit(f"chitta: {e}")
    if not stored:
        print("  nothing new — already ingested")
        return
    _report(added, sup, stored)


def cmd_listen(args):
    """Wispr Flow types into the focused window, so stdin is the microphone."""
    need_server()
    if sys.stdin.isatty():
        print("dictate now (Wispr Flow, or type). ctrl-D when done:\n")
    text = sys.stdin.read().strip()
    if not text:
        sys.exit("chitta: heard nothing")
    con = smriti.connect()
    print(f"\nheard {len(text)} chars…")
    ep, added, sup = smriti.feed(con, text, source="voice", tier=args.tier)
    _report([c for _, c in added], sup)
    if args.speak:
        n = len({(c["subject"], c["predicate"]) for _, c in added})
        shruti.speak(f"Remembered {n} thing{'s' if n != 1 else ''}." if n
                     else "Nothing worth keeping in that.")


def cmd_say(args):
    if not shruti.speak(" ".join(args.text), voice=args.voice):
        sys.exit("chitta: `say` unavailable")


def _toml(v, indent=0):
    pad = "  " * indent
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    sv = str(v)
    return '"""\n' + sv + '"""' if "\n" in sv else '"' + sv.replace('"', '\\"') + '"'


def _emit(d, prefix=""):
    out, tables = [], []
    for k, v in d.items():
        if isinstance(v, dict):
            tables.append((k, v))
        else:
            out.append(f"{k} = {_toml(v)}")
    for k, v in tables:
        name = f"{prefix}{k}"
        out.append(f"\n[{name}]")
        out.extend(_emit(v, name + "."))
    return out


def cmd_config(args):
    if args.path:
        print(CONFIG_PATH)
        return
    if args.init:
        if CONFIG_PATH.exists() and not args.force:
            sys.exit(f"chitta: {CONFIG_PATH} exists (use --force to overwrite)")
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        body = ("# chitta config. Everything here overrides the defaults in\n"
                "# chitta/config.py — delete any line to fall back to the default.\n"
                "# Values shown ARE the current defaults.\n\n")
        CONFIG_PATH.write_text(body + "\n".join(_emit(DEFAULTS)) + "\n")
        print(f"wrote {CONFIG_PATH}")
        return
    src = CONFIG_PATH if CONFIG_PATH.exists() else None
    print(f"\n  source: {src or '(defaults only — chitta config --init to create)'}\n")
    for line in _emit(CONFIG):
        print("  " + line if line else "")
    print()


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
    a.add_argument("--speak", action="store_true", help="read the answer aloud")
    a.add_argument("--no-memory", action="store_true", help="answer without smriti")
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

    g = sub.add_parser("ingest", help="feed it from what is already on this machine")
    g.add_argument("what", choices=["claude", "gmail"])
    g.add_argument("--days", type=int, default=7)
    g.add_argument("--limit", type=int, default=40)
    g.add_argument("-t", "--tier", choices=list(TIERS), default="work")
    g.set_defaults(fn=cmd_ingest)

    l = sub.add_parser("listen", help="dictate (Wispr Flow) or pipe text in")
    l.add_argument("-t", "--tier", choices=list(TIERS), default="work")
    l.add_argument("--speak", action="store_true", help="confirm aloud")
    l.set_defaults(fn=cmd_listen)

    sp = sub.add_parser("say", help="read something aloud")
    sp.add_argument("text", nargs="+")
    sp.add_argument("--voice", default=shruti.VOICE)
    sp.set_defaults(fn=cmd_say)

    cf = sub.add_parser("config", help="show or create the config file")
    cf.add_argument("--init", action="store_true", help="write a full config.toml")
    cf.add_argument("--path", action="store_true", help="print the config path")
    cf.add_argument("--force", action="store_true")
    cf.set_defaults(fn=cmd_config)

    d = sub.add_parser("doctor", help="check the RAM-critical settings")
    d.set_defaults(fn=cmd_doctor)

    args = p.parse_args()
    if args.cmd == "unload" and not args.all and not args.model:
        p.error("give a model name or --all")
    args.fn(args)


if __name__ == "__main__":
    main()
