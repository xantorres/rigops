"""One chat completion against an OpenAI-compatible endpoint.

The only module in the package that touches the network. Every failure comes
back as an ``error`` string rather than an exception, so one unreachable case is
recorded and the run moves on to the next.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .checks import printable

MAX_REPLY_BYTES = 1024 * 1024
MAX_ERROR_BYTES = 8192
MAX_ERROR_CHARS = 300
_THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.S)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: urllib would replay the Authorization header to the new host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _TooLarge(Exception):
    pass


_OPENER = urllib.request.build_opener(_NoRedirect)


def chat_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def _echoes(text: str, api_key: str) -> bool:
    """True when ``text`` holds any 8-character run of the key, in any case.

    Catches a server echoing the header back cut short, re-cased or wrapped,
    which an exact replace would let through. A shorter key gets the exact
    replace only: a window its own length would match ordinary words.
    """
    if len(api_key) < 8:
        return False
    key, text = api_key.lower(), text.lower()
    return any(key[i:i + 8] in text for i in range(len(key) - 7))


def _describe(exc) -> str:
    """Name a failure without quoting the exception's text, which can carry
    request headers, proxy credentials or raw server bytes."""
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timed out"
    if isinstance(exc, json.JSONDecodeError):
        return "reply body is not JSON"
    if isinstance(exc, UnicodeDecodeError):
        return "reply body is not UTF-8"
    return type(exc).__name__


def _http_error(exc) -> str:
    try:
        with exc:
            detail = " ".join(exc.read(MAX_ERROR_BYTES).decode("utf-8", "replace").split())
    except (OSError, http.client.HTTPException):
        detail = ""
    return f"HTTP {exc.code}: {detail}" if detail else f"HTTP {exc.code}"


def _fetch(request, timeout_s) -> bytes:
    with _OPENER.open(request, timeout=timeout_s) as response:
        chunks, size = [], 0
        while True:
            chunk = response.read1(65536)
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > MAX_REPLY_BYTES:
                raise _TooLarge
            chunks.append(chunk)


def complete(endpoint, model, messages, *, max_tokens, temperature, timeout_s, api_key=None):
    """POST one chat completion and return ``{"content", "ms", "error"}``.

    ``timeout_s`` bounds the whole call. The request runs on a worker thread
    because http.client applies its timeout to each socket read, so a server
    trickling headers or chunk sizes would otherwise hold the run open. A
    leading ``<think>`` block, which some servers inline for reasoning models,
    is dropped: the assertions judge the answer, not the scratchpad.
    """
    started = time.monotonic()

    def done(content="", error=None, bare=None):
        if error is not None:
            if api_key:
                error = error.replace(api_key, "[redacted]")
                if _echoes(error, api_key):
                    error = f"{bare or 'request failed'} (detail withheld: it quoted the API key)"
            error = printable(error)[:MAX_ERROR_CHARS]
        ms = round((time.monotonic() - started) * 1000)
        return {"content": content, "ms": ms, "error": error}

    url = chat_url(endpoint)
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        return done(error="endpoint must be an http or https URL")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST",
    )

    box = {}

    def fetch():
        try:
            box["raw"] = _fetch(request, timeout_s)
        except Exception as exc:  # noqa: BLE001 - handed back to the calling thread below
            box["exc"] = exc

    worker = threading.Thread(target=fetch, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        return done(error="timed out")
    try:
        if "exc" in box:
            raise box["exc"]
        body = json.loads(box["raw"].decode("utf-8"))
    except _TooLarge:
        return done(error=f"reply larger than {MAX_REPLY_BYTES} bytes")
    except urllib.error.HTTPError as exc:
        return done(error=_http_error(exc), bare=f"HTTP {exc.code}")
    except urllib.error.URLError as exc:
        return done(error=f"unreachable: {exc.reason}", bare="unreachable")
    except (OSError, http.client.HTTPException, ValueError, RecursionError) as exc:
        return done(error=_describe(exc))
    try:
        content = body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return done(error="reply has no choices[0].message.content")
    if not isinstance(content, str):
        return done(error="choices[0].message.content is not a string")
    content = _THINK.sub("", content, count=1)
    if content.lstrip().startswith("<think>"):
        return done(error="reply ended inside a <think> block; raise max_tokens")
    return done(content=content)
