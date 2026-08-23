"""Tier definitions and paths.

The whole design rests on one rule: only Tier 0 is allowed to be resident.
Everything above it is summoned, used, and released.
"""
import os
from pathlib import Path

HOST = os.environ.get("CHITTA_OLLAMA", "http://127.0.0.1:11434")

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CHITTA_DATA", Path.home() / ".chitta"))

# keep_alive is the RAM lever: how long weights linger after the last token.
TIERS = {
    "reflex": {
        "model": "qwen3:1.7b",
        "keep_alive": "30m",   # always hot; it is the cheap thing that decides
        "num_ctx": 4096,
        "role": "routing, extraction, 'is this worth interrupting a human for?'",
    },
    "work": {
        "model": "qwen3:8b",
        "keep_alive": "5m",    # summoned, then released
        "num_ctx": 8192,
        "role": "summarising, consolidation, ordinary questions",
    },
    "heavy": {
        "model": "gpt-oss:20b",
        "keep_alive": "60s",   # rare, expensive, evicted almost immediately
        "num_ctx": 8192,
        "role": "multi-step reasoning that reflex and work both failed",
    },
}

EMBED = {"model": "embeddinggemma:300m", "keep_alive": "30m"}

DEFAULT_TIER = "work"

# must match OLLAMA_ORIGINS in ops/com.chitta.ollama.plist
UI_PORT = 7717
