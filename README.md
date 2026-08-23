# chitta

चित्त — in Samkhya and the Yoga Sutras, the mind-substrate where impressions settle.
A local-first personal intelligence, built to consume as little RAM as possible.

Only Tier 0 is allowed to be resident. Everything above it is summoned, used, released.

## Tiers

| tier   | model               | measured | keep_alive | for |
|--------|---------------------|----------|-----------|-----|
| reflex | qwen3:1.7b          | 1.54 GB  | 30m       | routing, extraction, "is this worth interrupting a human for?" |
| work   | qwen3:8b            | 5.66 GB  | 5m        | summarising, consolidation, ordinary questions |
| heavy  | gpt-oss:20b         | —        | 60s       | reasoning that reflex and work both failed |
| embed  | embeddinggemma:300m | 0.6 GB   | 30m       | feeds the graph |

Measured on M5 Pro / 48 GB via `/api/ps`, all 4-bit, KV cache at q8_0.

## Why it is small

The KV cache, not the weights, is what usually blows up local inference.
At fp16 and 32k context qwen3:8b's cache alone exceeds the 4-bit model.
Ollama also allocates that cache *per parallel slot*, four by default.

`ops/com.chitta.ollama.plist` fixes both:

    OLLAMA_NUM_PARALLEL=1        one slot, not four
    OLLAMA_KV_CACHE_TYPE=q8_0    quantised cache
    OLLAMA_FLASH_ATTENTION=1     required for the above to engage
    OLLAMA_MAX_LOADED_MODELS=1   never two models resident
    OLLAMA_KEEP_ALIVE=5m         evict by default

Together, roughly 5x less working set than defaults.
The launchd agent replaces Ollama.app's server, so the GUI app need not run at all.

## Install

    cp ops/com.chitta.ollama.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.chitta.ollama.plist
    uv tool install --editable .

The agent runs `ops/ollama-serve.sh`, which evicts Ollama.app and its server
before binding :11434 — so the GUI app can stay a login item and lose the race
anyway. No System Settings toggle needed.

## Use

    chitta ask "..."        # -t reflex|work|heavy
    chitta status           # what is resident right now
    chitta models           # what is on disk
    chitta unload --all     # back to zero
    chitta ui               # dashboard on 127.0.0.1:7717
    chitta doctor           # check the RAM-critical settings

The core is stdlib-only. Importing httpx costs ~100ms of startup, and this is
a CLI you run fifty times a day.

## Not built yet

Named for what each will do, per the Mahabharata:

- **shruti** — voice in (Wispr Flow)
- **sanjaya** — ingestion: Claude Code sessions, Gmail, Calendar, Notion
- **vyasa** — nightly consolidation: raw day to entities and claims
- **smriti** — the temporal knowledge graph (Graphiti + Kuzu, embedded, zero idle RAM)
- **narada** — the daemon that decides when to interrupt you. Budget: 3 a day.
- **vidura** — counsel. Tells you what you do not want to hear.
- **kriya** — hands. Executes tasks on the machine, behind an allowlist.
