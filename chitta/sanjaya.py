"""sanjaya - he who was given sight of a battlefield he was not standing on,
and narrated it to someone who could not see.

Ingestion. Turns things that already exist on this machine into episodes.
The graph is easy; feeding it is where these projects die, so every adapter
here must be re-runnable for free — dedupe is on (source, ref) in sqlite.

ponytail: no Google API client, no OAuth dance, no GCP project. Gmail speaks
IMAP and imaplib is stdlib. Swap to the API only if you need labels/push.
"""
import email
import email.utils
import glob
import imaplib
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import smriti

CLAUDE_SESSIONS = Path.home() / ".claude" / "projects"


# ---------- claude code sessions ----------

def claude_sessions(root=CLAUDE_SESSIONS, min_chars=40):
    """One episode per session, not per message.

    Extraction costs ~4s a call, so 50 messages would be 3 minutes of the
    work tier. A session read whole is also better context than a turn.
    Only the human's own words - assistant text would teach it my habits,
    not his.
    """
    for f in sorted(glob.glob(str(Path(root) / "*" / "*.jsonl"))):
        turns, ts, cwd = [], None, None
        for line in open(f, errors="replace"):
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("type") != "user":
                continue
            c = o.get("message", {}).get("content")
            if isinstance(c, list):  # tool results and attachments arrive as blocks
                c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
            if not isinstance(c, str):
                continue
            c = c.strip()
            # command output and pasted blobs are noise, not intent
            if len(c) < 12 or c.startswith(("<", "/", "!")) or len(c) > 4000:
                continue
            turns.append(c)
            ts = ts or o.get("timestamp")
            cwd = cwd or o.get("cwd")
        text = "\n".join(turns)
        if len(text) < min_chars:
            continue
        yield {"source": "claude", "ref": Path(f).stem, "ts": ts,
               "text": f"(working in {cwd})\n{text}" if cwd else text}


# ---------- gmail over imap ----------

def _keychain(service="chitta-gmail"):
    """Read the address and app password the user stored themselves.

    chitta never sees a password in the clear and never writes one - the
    user runs `security add-generic-password` once, macOS holds it.
    """
    meta = subprocess.run(["security", "find-generic-password", "-s", service],
                          capture_output=True, text=True)
    if meta.returncode:
        raise RuntimeError(
            f"no keychain entry '{service}'. Store one yourself with:\n"
            f'  security add-generic-password -s {service} -a YOU@gmail.com -w\n'
            "  (it will prompt for the app password; needs 2FA + an app password\n"
            "   from https://myaccount.google.com/apppasswords)")
    m = re.search(r'"acct"<blob>="([^"]+)"', meta.stdout)
    if not m:
        raise RuntimeError(f"keychain entry '{service}' has no account set")
    addr = m.group(1)
    pw = subprocess.run(["security", "find-generic-password", "-s", service,
                         "-a", addr, "-w"], capture_output=True, text=True)
    if pw.returncode:
        raise RuntimeError(f"could not read password for {addr}")
    return addr, pw.stdout.strip()


def _body(msg, limit=1500):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(errors="replace")[:limit]
                except Exception:
                    pass
        return ""
    try:
        return (msg.get_payload(decode=True) or b"").decode(errors="replace")[:limit]
    except Exception:
        return ""


def gmail(days=7, limit=40, mailbox="INBOX"):
    addr, pw = _keychain()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        M.login(addr, pw)
        M.select(mailbox, readonly=True)      # readonly: never mark anything seen
        ok, data = M.search(None, f'(SINCE {since})')
        ids = data[0].split()[-limit:] if ok == "OK" else []
        for i in ids:
            ok, d = M.fetch(i, "(RFC822)")
            if ok != "OK" or not d or not isinstance(d[0], tuple):
                continue
            msg = email.message_from_bytes(d[0][1])
            subj = str(email.header.make_header(
                email.header.decode_header(msg.get("Subject", "")))) or "(no subject)"
            frm = msg.get("From", "")
            mid = msg.get("Message-ID") or f"{mailbox}:{i.decode()}"
            try:
                ts = email.utils.parsedate_to_datetime(msg.get("Date", "")).isoformat()
            except Exception:
                ts = None
            yield {"source": "gmail", "ref": mid, "ts": ts,
                   "text": f"From: {frm}\nSubject: {subj}\n\n{_body(msg)}",
                   "_digest": f"- from {frm}: {subj}"}
    finally:
        try:
            M.logout()
        except Exception:
            pass


# ---------- driver ----------

def ingest(con, episodes, tier="work", digest=False, on_episode=None):
    """Store episodes, then extract claims.

    digest=True extracts once from a summary of everything rather than once
    per item. Mail is mostly noise; one pass over forty subject lines finds
    the durable relationships without forty model calls or forty junk claims.
    """
    stored, digest_lines = [], []
    for ep in episodes:
        eid = smriti.add_episode(con, ep["source"], ep["text"],
                                 ref=ep.get("ref"), ts=ep.get("ts"))
        if eid is None:
            continue                      # already ingested; re-running is free
        stored.append((eid, ep))
        digest_lines.append(ep.get("_digest") or ep["text"][:300])
        if on_episode:
            on_episode(ep)

    added, superseded = [], []
    if not stored:
        return stored, added, superseded

    def absorb(text, episode_id):
        for c in smriti.extract(text, tier):
            if not (c.get("subject") and c.get("predicate") and c.get("object")):
                continue
            cid, old = smriti.add_claim(con, c["subject"], c["predicate"], c["object"],
                                        episode_id=episode_id,
                                        confidence=c.get("confidence", 0.5))
            added.append(c)
            if old:
                superseded.append(old)

    if digest:
        absorb("\n".join(digest_lines)[:8000], stored[0][0])
    else:
        for eid, ep in stored:
            absorb(ep["text"][:8000], eid)
    return stored, added, superseded
