"""Minimal Ollama client. stdlib only - no httpx, no requests.

Importing httpx costs ~100ms of startup. For a CLI you invoke fifty times a
day that is the difference between a tool and a chore.
"""
import json
import urllib.error
import urllib.request

from .config import HOST


def _req(path, payload=None, method=None, timeout=300):
    url = f"{HOST}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method or ("POST" if data else "GET"),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req, timeout=timeout)


def _json(path, payload=None, method=None, timeout=300):
    with _req(path, payload, method, timeout) as r:
        body = r.read()
    return json.loads(body) if body else {}


def alive():
    try:
        _json("/api/version", timeout=3)
        return True
    except Exception:
        return False


def tags():
    """Every model on disk."""
    return _json("/api/tags", timeout=30).get("models", [])


def ps():
    """Only what is resident in memory right now - the number that matters."""
    return _json("/api/ps", timeout=30).get("models", [])


def chat(model, messages, keep_alive=None, num_ctx=None, think=False,
         stream=True, format=None):
    """Yields content chunks."""
    payload = {"model": model, "messages": messages, "stream": stream, "think": think}
    if format is not None:
        payload["format"] = format
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    if num_ctx:
        payload["options"] = {"num_ctx": num_ctx}

    with _req("/api/chat", payload) as r:
        if not stream:
            yield json.loads(r.read())["message"]["content"]
            return
        for line in r:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            piece = obj.get("message", {}).get("content")
            if piece:
                yield piece
            if obj.get("done"):
                break


def unload(model):
    """Evict weights immediately. keep_alive=0 is the whole trick."""
    return _json("/api/generate", {"model": model, "keep_alive": 0}, timeout=60)


def delete(model):
    return _json("/api/delete", {"model": model}, method="DELETE", timeout=60)


def chat_json(model, messages, schema, keep_alive=None, num_ctx=None):
    """Constrained decode against a JSON schema. Ollama enforces the grammar,
    so this cannot come back as prose with a code fence around it."""
    txt = "".join(chat(model, messages, keep_alive=keep_alive, num_ctx=num_ctx,
                       stream=False, format=schema))
    return json.loads(txt)


def embed(model, texts):
    return _json("/api/embed", {"model": model, "input": texts}, timeout=120)["embeddings"]
