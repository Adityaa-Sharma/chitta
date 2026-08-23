"""Every knob in one place.

Defaults live here. A user file at ~/.chitta/config.toml is deep-merged over
them, so you only write what you want to change and nothing breaks when new
keys appear. tomllib is stdlib as of 3.11, so reading it costs nothing.

Nothing in chitta should hardcode a model, a path, a duration, a prompt, or a
threshold. If you find yourself typing a literal, it belongs in DEFAULTS.
"""
import os
import tomllib
from copy import deepcopy
from pathlib import Path

DATA = Path(os.environ.get("CHITTA_DATA", Path.home() / ".chitta")).expanduser()
CONFIG_PATH = Path(os.environ.get("CHITTA_CONFIG", DATA / "config.toml")).expanduser()

DEFAULTS = {
    "ollama": {
        "host": "http://127.0.0.1:11434",
    },
    "ui": {
        "port": 7717,                 # must match OLLAMA_ORIGINS in the launchd plist
        "ram_budget_gb": 6,
        "poll_models_ms": 2500,
        "poll_smriti_ms": 5000,
        "poll_graph_ms": 8000,
        "graph_max_claims": 250,
        "graph_label_chars": 24,
    },
    # keep_alive is the RAM lever: how long weights linger after the last token
    "tiers": {
        "reflex": {"model": "qwen3:1.7b", "keep_alive": "30m", "num_ctx": 4096,
                   "role": "routing, extraction, 'is this worth interrupting a human for?'"},
        "work": {"model": "qwen3:8b", "keep_alive": "5m", "num_ctx": 8192,
                 "role": "summarising, consolidation, ordinary questions"},
        "heavy": {"model": "gpt-oss:20b", "keep_alive": "60s", "num_ctx": 8192,
                  "role": "multi-step reasoning that reflex and work both failed"},
    },
    "embed": {"model": "embeddinggemma:300m", "keep_alive": "30m"},
    "default_tier": "work",
    "persona": {
        "prompt": (
            "You are Chitta. In Samkhya and the Yoga Sutras, chitta is the "
            "mind-substrate where impressions settle. You are that, for one person.\n"
            "Be direct and concrete. Prefer one good sentence to five hedging ones. "
            "You are allowed to be philosophical, but only when it earns its place - "
            "never as decoration on an ordinary answer."
        ),
    },
    "extract": {
        "tier": "work",
        "first_person_as": "aditya",
        "max_subject_chars": 60,
        "max_predicate_chars": 60,
        "max_object_chars": 200,
        "max_input_chars": 8000,
        "default_confidence": 0.5,
        "default_cardinality": "many",   # never destroy a fact on a guess
        "prompt": "",                    # blank = build from the template below
    },
    "ask": {
        # At personal scale the whole live graph fits in context, and perfect
        # recall beats any retrieval heuristic. Add embeddings only when the
        # claim count actually outgrows this.
        # ponytail: no vector search until max_claims is genuinely too small.
        "use_memory": True,
        "max_claims": 120,
        "preamble": "What you already know about this person, from your own memory:",
        "instruction": (
            "Answer from that memory when it is relevant. If the memory does not "
            "cover the question, say so plainly rather than inventing a fact about them."
        ),
    },
    "voice": {
        "enabled": True,
        "voice": "Aman",     # en_IN, ships with macOS. `say -v '?'` lists 184.
        "rate": 185,
        "speak_answers": False,
    },
    "ingest": {
        "claude": {
            "enabled": True,
            "path": "~/.claude/projects",
            "min_session_chars": 40,
            "min_turn_chars": 12,
            "max_turn_chars": 4000,
            "skip_prefixes": ["<", "/", "!"],
            "digest": False,
        },
        "gmail": {
            "enabled": True,
            "keychain_service": "chitta-gmail",
            "host": "imap.gmail.com",
            "mailbox": "INBOX",
            "days": 7,
            "limit": 40,
            "body_chars": 1500,
            "digest": True,       # one extraction over all subjects, not 40 calls
        },
    },
    "daemon": {
        "interval_minutes": 20,
        "max_interruptions_per_day": 3,
        "triage_tier": "reflex",
        "escalate_tier": "work",
        "quiet_hours": [22, 8],
    },
}

EXTRACT_TEMPLATE = """Extract durable claims from the text. A claim is
(subject, predicate, object) - e.g. ("{me}", "is building", "chitta").

One atomic fact per claim. Object under 15 words. Split anything compound
into separate claims - never pack a whole paragraph into one object.

Keep: goals, commitments, beliefs, preferences, relationships, decisions,
things that are true for weeks not minutes.
Drop: pleasantries, one-off events, anything you would not want repeated
back in six months.

subject and predicate lowercase. Use "{me}" for first person.

cardinality is the important one. "one" means the subject can hold only a
single value for this predicate at a time, so a new value replaces the old:
lives in, works at, is married to, currently prefers. "many" means several
can be true at once: wants, has, knows, is building, is interested in.
When unsure say "many" - it keeps the old fact instead of destroying it.

Return {{"claims": []}} rather than inventing anything. Be sparing."""


def _merge(base, over):
    out = deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(path=None):
    path = Path(path or CONFIG_PATH)
    user = {}
    if path.exists():
        with open(path, "rb") as f:
            user = tomllib.load(f)
    cfg = _merge(DEFAULTS, user)
    if not cfg["extract"]["prompt"]:
        cfg["extract"]["prompt"] = EXTRACT_TEMPLATE.format(
            me=cfg["extract"]["first_person_as"])
    return cfg


CONFIG = load()

# Convenience aliases. Read CONFIG for anything new; these exist so callers
# stay readable, not so values get copied around.
HOST = os.environ.get("CHITTA_OLLAMA", CONFIG["ollama"]["host"])
TIERS = CONFIG["tiers"]
EMBED = CONFIG["embed"]
DEFAULT_TIER = CONFIG["default_tier"]
UI_PORT = int(os.environ.get("CHITTA_UI_PORT", CONFIG["ui"]["port"]))
