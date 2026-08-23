"""smriti - that which is remembered.

A bitemporal claim store. Two clocks, and the second one is the whole point:

    valid_from / valid_to   when a thing was true of the world
    observed_at             when chitta was told

Keeping both is what lets you ask "what did I believe in March, and does it
survive contact with what I believe now?" A pile of embeddings cannot answer
that, because it only knows similarity, not succession.

Writing a claim supersedes any open claim with the same subject+predicate.
Nothing is deleted; the old row gets a valid_to and points at its successor.
That closed row IS the contradiction - no separate detection pass needed.

ponytail: sqlite, not a graph database. Recursive CTEs cover multi-hop and
stdlib costs nothing at idle. Move to kuzu if traversal depth ever bites.
ponytail: LIKE, not FTS5. Sub-ms at personal scale (<1e5 claims). Add FTS5
when a query measurably drags.
"""
import json
import sqlite3
from datetime import datetime, timezone

from . import ollama
from .config import DATA, TIERS

SCHEMA = """
CREATE TABLE IF NOT EXISTS episode(
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  ref TEXT,
  ts TEXT NOT NULL,
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim(
  id INTEGER PRIMARY KEY,
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  observed_at TEXT NOT NULL,
  superseded_by INTEGER REFERENCES claim(id),
  episode_id INTEGER REFERENCES episode(id),
  confidence REAL DEFAULT 0.5
);
CREATE INDEX IF NOT EXISTS claim_sp ON claim(subject, predicate, valid_to);
"""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "object": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["subject", "predicate", "object"],
            },
        }
    },
    "required": ["claims"],
}

EXTRACT_PROMPT = """Extract durable claims from the text. A claim is
(subject, predicate, object) - e.g. ("aditya", "is building", "chitta").

Keep: goals, commitments, beliefs, preferences, relationships, decisions,
things that are true for weeks not minutes.
Drop: pleasantries, one-off events, anything you would not want repeated
back in six months.

subject and predicate lowercase. Use "aditya" for first person.
Return {"claims": []} rather than inventing anything. Be sparing."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=None):
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path or DATA / "smriti.db")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def add_episode(con, source, text, ref=None, ts=None):
    cur = con.execute(
        "INSERT INTO episode(source, ref, ts, text) VALUES(?,?,?,?)",
        (source, ref, ts or now(), text),
    )
    return cur.lastrowid


def add_claim(con, subject, predicate, obj, episode_id=None, confidence=0.5,
              valid_from=None, observed_at=None):
    """Insert, superseding any open claim with the same subject+predicate.

    Returns (claim_id, superseded_id_or_None).
    """
    s, p = subject.strip().lower(), predicate.strip().lower()
    o = obj.strip()
    t = observed_at or now()

    prior = con.execute(
        "SELECT id, object FROM claim WHERE subject=? AND predicate=? AND valid_to IS NULL",
        (s, p),
    ).fetchone()

    # Same thing said twice is not a contradiction. Leave the original alone
    # so valid_from keeps pointing at when it first became true.
    if prior and prior["object"].strip().lower() == o.lower():
        return prior["id"], None

    cur = con.execute(
        "INSERT INTO claim(subject,predicate,object,valid_from,valid_to,"
        "observed_at,episode_id,confidence) VALUES(?,?,?,?,NULL,?,?,?)",
        (s, p, o, valid_from or t, t, episode_id, confidence),
    )
    new_id = cur.lastrowid
    if prior:
        con.execute("UPDATE claim SET valid_to=?, superseded_by=? WHERE id=?",
                    (t, new_id, prior["id"]))
    con.commit()
    return new_id, (prior["id"] if prior else None)


def extract(text, tier="work"):
    t = TIERS[tier]
    out = ollama.chat_json(
        t["model"],
        [{"role": "system", "content": EXTRACT_PROMPT},
         {"role": "user", "content": text}],
        EXTRACT_SCHEMA, keep_alive=t["keep_alive"], num_ctx=t["num_ctx"],
    )
    return out.get("claims", [])


def feed(con, text, source="note", ref=None, tier="work"):
    ep = add_episode(con, source, text, ref)
    added, superseded = [], []
    for c in extract(text, tier):
        if not (c.get("subject") and c.get("predicate") and c.get("object")):
            continue
        cid, old = add_claim(con, c["subject"], c["predicate"], c["object"],
                             episode_id=ep, confidence=c.get("confidence", 0.5))
        added.append((cid, c))
        if old:
            superseded.append(old)
    return ep, added, superseded


def recall(con, query=None, include_history=False, limit=50):
    sql = "SELECT * FROM claim"
    where, args = [], []
    if not include_history:
        where.append("valid_to IS NULL")
    if query:
        where.append("(subject LIKE ? OR predicate LIKE ? OR object LIKE ?)")
        args += [f"%{query.lower()}%"] * 2 + [f"%{query}%"]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY observed_at DESC LIMIT ?"
    return con.execute(sql, (*args, limit)).fetchall()


def contradictions(con, limit=50):
    """Every place a belief was replaced. old -> new, with both timestamps."""
    return con.execute(
        "SELECT o.subject, o.predicate, o.object AS was, n.object AS now_is,"
        "       o.valid_from AS held_from, o.valid_to AS changed_at "
        "FROM claim o JOIN claim n ON o.superseded_by = n.id "
        "ORDER BY o.valid_to DESC LIMIT ?", (limit,)).fetchall()


def stats(con):
    q = lambda s: con.execute(s).fetchone()[0]
    return {
        "episodes": q("SELECT COUNT(*) FROM episode"),
        "claims_live": q("SELECT COUNT(*) FROM claim WHERE valid_to IS NULL"),
        "claims_total": q("SELECT COUNT(*) FROM claim"),
        "superseded": q("SELECT COUNT(*) FROM claim WHERE superseded_by IS NOT NULL"),
        "subjects": q("SELECT COUNT(DISTINCT subject) FROM claim"),
    }


def _selfcheck():
    """Supersession is the only non-obvious logic here, so it is what we test."""
    con = connect(":memory:")
    a, old = add_claim(con, "Aditya", "Is Building", "a CLI", confidence=0.9)
    assert old is None

    # identical restatement must not fork history
    b, old = add_claim(con, "aditya", "is building", "a CLI")
    assert b == a and old is None, "restating a claim should be a no-op"

    # a real change supersedes
    c, old = add_claim(con, "aditya", "is building", "a knowledge graph")
    assert old == a and c != a

    live = recall(con)
    assert len(live) == 1 and live[0]["object"] == "a knowledge graph"
    assert len(recall(con, include_history=True)) == 2

    con.execute("SELECT valid_to, superseded_by FROM claim WHERE id=?", (a,))
    row = con.execute("SELECT * FROM claim WHERE id=?", (a,)).fetchone()
    assert row["valid_to"] is not None and row["superseded_by"] == c

    ct = contradictions(con)
    assert len(ct) == 1
    assert ct[0]["was"] == "a CLI" and ct[0]["now_is"] == "a knowledge graph"

    # unrelated predicate is untouched
    add_claim(con, "aditya", "prefers", "minimal dependencies")
    assert len(recall(con)) == 2
    assert stats(con)["superseded"] == 1
    print("smriti selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
