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
CREATE UNIQUE INDEX IF NOT EXISTS episode_ref
  ON episode(source, ref) WHERE ref IS NOT NULL;
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
  confidence REAL DEFAULT 0.5,
  cardinality TEXT NOT NULL DEFAULT 'many'
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
                    "cardinality": {"type": "string", "enum": ["one", "many"]},
                },
                "required": ["subject", "predicate", "object", "cardinality"],
            },
        }
    },
    "required": ["claims"],
}

EXTRACT_PROMPT = """Extract durable claims from the text. A claim is
(subject, predicate, object) - e.g. ("aditya", "is building", "chitta").

One atomic fact per claim. Object under 15 words. Split anything compound
into separate claims - never pack a whole paragraph into one object.

Keep: goals, commitments, beliefs, preferences, relationships, decisions,
things that are true for weeks not minutes.
Drop: pleasantries, one-off events, anything you would not want repeated
back in six months.

subject and predicate lowercase. Use "aditya" for first person.

cardinality is the important one. "one" means the subject can hold only a
single value for this predicate at a time, so a new value replaces the old:
lives in, works at, is married to, currently prefers. "many" means several
can be true at once: wants, has, knows, is building, is interested in.
When unsure say "many" - it keeps the old fact instead of destroying it.

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
    """Returns the new id, or None if this (source, ref) was already ingested.

    Re-running an ingest must be free, otherwise nobody runs it on a schedule.
    """
    cur = con.execute(
        "INSERT OR IGNORE INTO episode(source, ref, ts, text) VALUES(?,?,?,?)",
        (source, ref, ts or now(), text),
    )
    con.commit()
    return cur.lastrowid if cur.rowcount else None


def add_claim(con, subject, predicate, obj, episode_id=None, confidence=0.5,
              valid_from=None, observed_at=None, cardinality="many"):
    """Insert, superseding the prior value only if the predicate holds one.

    Cardinality matters more than it looks. "aditya prefers X" is single
    valued, so a new preference genuinely replaces the old one. "aditya
    wants X" is not - wanting a second thing does not stop you wanting the
    first. Superseding blindly turned nineteen real claims into fake
    contradictions on the first real ingest. Default to "many": keeping a
    stale fact is recoverable, deleting a true one is not.

    Returns (claim_id, superseded_id_or_None).
    """
    s, p = subject.strip().lower(), predicate.strip().lower()
    o = obj.strip()
    t = observed_at or now()

    prior = con.execute(
        "SELECT id, object FROM claim WHERE subject=? AND predicate=? AND valid_to IS NULL"
        + ("" if cardinality == "one" else " AND lower(object)=?"),
        (s, p) if cardinality == "one" else (s, p, o.lower()),
    ).fetchone()

    # Same thing said twice is not a contradiction. Leave the original alone
    # so valid_from keeps pointing at when it first became true.
    if prior and prior["object"].strip().lower() == o.lower():
        return prior["id"], None

    cur = con.execute(
        "INSERT INTO claim(subject,predicate,object,valid_from,valid_to,"
        "observed_at,episode_id,confidence,cardinality) VALUES(?,?,?,?,NULL,?,?,?,?)",
        (s, p, o, valid_from or t, t, episode_id, confidence, cardinality),
    )
    new_id = cur.lastrowid
    if prior:
        con.execute("UPDATE claim SET valid_to=?, superseded_by=? WHERE id=?",
                    (t, new_id, prior["id"]))
    con.commit()
    return new_id, (prior["id"] if prior else None)


MAX_OBJECT = 200


def _clean(claims):
    """Drop malformed extractions.

    Do NOT enforce this with maxLength in the json schema - the decoder then
    truncates mid-token and the model spills the next object into the current
    string, so you get `mac for first time'}, {` as an object value. Bound it
    here, after decoding.

    Dropping is safe: the episode keeps the raw text, so a better prompt can
    re-derive the claim later. Claims are derived data, episodes are source.
    """
    out, seen = [], set()
    for c in claims:
        if not isinstance(c, dict):
            continue
        s = str(c.get("subject", "")).strip()
        p = str(c.get("predicate", "")).strip()
        o = str(c.get("object", "")).strip()
        if not (s and p and o):
            continue
        if len(o) > MAX_OBJECT or len(p) > 60 or len(s) > 60:
            continue          # a paragraph, not a claim - failed extraction
        if "'}," in o or '"},' in o:
            continue          # decoder spillage
        k = (s.lower(), p.lower(), o.lower())
        if k in seen:
            continue
        seen.add(k)
        c["subject"], c["predicate"], c["object"] = s, p, o
        out.append(c)
    return out


def extract(text, tier="work"):
    t = TIERS[tier]
    out = ollama.chat_json(
        t["model"],
        [{"role": "system", "content": EXTRACT_PROMPT},
         {"role": "user", "content": text}],
        EXTRACT_SCHEMA, keep_alive=t["keep_alive"], num_ctx=t["num_ctx"],
    )
    return _clean(out.get("claims", []))


def feed(con, text, source="note", ref=None, tier="work"):
    ep = add_episode(con, source, text, ref)
    added, superseded = [], []
    for c in extract(text, tier):
        if not (c.get("subject") and c.get("predicate") and c.get("object")):
            continue
        cid, old = add_claim(con, c["subject"], c["predicate"], c["object"],
                             episode_id=ep, confidence=c.get("confidence", 0.5),
                             cardinality=c.get("cardinality", "many"))
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


def graph(con, limit=250):
    """Nodes and edges for the viewer.

    Subjects and objects are both nodes; a claim is an edge. Superseded
    claims are included but flagged, because seeing what a belief replaced
    is the whole reason the history is kept.
    """
    rows = con.execute(
        "SELECT subject, predicate, object, valid_to, cardinality, confidence "
        "FROM claim ORDER BY observed_at DESC LIMIT ?", (limit,)).fetchall()
    nodes, edges = {}, []
    for r in rows:
        for name, kind in ((r["subject"], "subject"), (r["object"], "object")):
            n = nodes.setdefault(name, {"id": name, "kind": kind, "deg": 0})
            n["deg"] += 1
            if kind == "subject":
                n["kind"] = "subject"      # being a subject anywhere wins
        edges.append({"s": r["subject"], "t": r["object"], "label": r["predicate"],
                      "live": r["valid_to"] is None})
    return {"nodes": list(nodes.values()), "edges": edges}


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
    a, old = add_claim(con, "Aditya", "Is Building", "a CLI", confidence=0.9,
                       cardinality="one")
    assert old is None

    # identical restatement must not fork history
    b, old = add_claim(con, "aditya", "is building", "a CLI", cardinality="one")
    assert b == a and old is None, "restating a claim should be a no-op"

    # a real change supersedes
    c, old = add_claim(con, "aditya", "is building", "a knowledge graph",
                       cardinality="one")
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
    add_claim(con, "aditya", "prefers", "minimal dependencies", cardinality="one")
    assert len(recall(con)) == 2

    # multi-valued predicates must accumulate, never evict. This is the bug
    # that made 19 fake contradictions out of one ingest.
    add_claim(con, "aditya", "wants", "local models")
    add_claim(con, "aditya", "wants", "a knowledge graph")
    add_claim(con, "aditya", "wants", "voice input")
    wants = [r for r in recall(con) if r["predicate"] == "wants"]
    assert len(wants) == 3, f"multi-valued predicate evicted: {len(wants)} of 3 survived"
    # ...but an exact repeat is still a no-op
    _, old = add_claim(con, "aditya", "wants", "voice input")
    assert old is None and len([r for r in recall(con) if r["predicate"] == "wants"]) == 3
    assert stats(con)["superseded"] == 1, "only the single-valued change superseded"
    # _clean guards the things the decoder actually got wrong
    bad = _clean([
        {"subject": "a", "predicate": "b", "object": "x" * 500},      # paragraph
        {"subject": "a", "predicate": "b", "object": "mac'}, {"},     # spillage
        {"subject": "", "predicate": "b", "object": "c"},             # empty
        {"subject": "a", "predicate": "likes", "object": "tea"},      # good
        {"subject": "A", "predicate": "Likes", "object": "Tea"},      # dup
    ])
    assert len(bad) == 1 and bad[0]["object"] == "tea", bad

    print("smriti selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
