"""Run-scoped logical budgets and metered HTTP attempts, shared by adapters."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urljoin

from requests.cookies import RequestsCookieJar, extract_cookies_to_jar
from requests.sessions import SessionRedirectMixin


def _run_dir() -> Path | None:
    value = os.environ.get("DRBENCH_RUN_DIR") or os.environ.get("DEEP_RESEARCH_RUN_DIR")
    return Path(value) if value else None


@contextmanager
def _state():
    root = _run_dir()
    if root is None:
        yield None
        return
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".adapter-counts.json"
    with (root / ".adapter-counts.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                state = {}
            state = {"search": 0, "read": 0, "blocked": 0, "requests": 0,
                     "request_errors": 0, "request_seconds": 0.0, "redirects": 0, **state}
            try:
                yield state
            finally:
                with tempfile.NamedTemporaryFile(mode="w", dir=root, encoding="utf-8", delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump(state, stream)
                try:
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def snapshot() -> dict | None:
    with _state() as state:
        return dict(state) if state is not None else None


def reserve(kind: str) -> None:
    if kind not in {"search", "read", "requests"}:
        raise ValueError(f"unsupported budget: {kind}")
    limit = int(os.environ.get(f"DRBENCH_MAX_{kind.upper()}") or
                os.environ.get(f"DEEP_RESEARCH_MAX_{kind.upper()}", "0"))
    if limit < 0:
        raise ValueError(f"{kind} budget must be non-negative")
    with _state() as state:
        if state is None:
            if limit:
                raise ValueError("set DEEP_RESEARCH_RUN_DIR to enforce a budget")
            return
        if limit and state[kind] >= limit:
            state["blocked"] += 1
            raise RuntimeError(f"{kind} budget exhausted ({limit})")
        state[kind] += 1


def _add(**values) -> None:
    with _state() as state:
        if state is not None:
            for key, value in values.items():
                state[key] = round(state.get(key, 0) + value, 6)


@contextmanager
def _attempt(sender, url: str, **kwargs):
    reserve("requests")
    started = time.monotonic()
    response = None
    failed = False
    try:
        response = sender(url, allow_redirects=False, **kwargs)
        failed = int(response.status_code) >= 400
        yield response
    except Exception:
        failed = True
        raise
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            _add(request_errors=int(failed), request_seconds=time.monotonic() - started)


@contextmanager
def request(sender, url: str, *, allow_redirects: bool = True, url_ok=None, **kwargs):
    """Count each HTTP hop before sending; include streamed body consumption time."""
    cookies = RequestsCookieJar()
    if kwargs.get("cookies"):
        cookies.update(kwargs["cookies"])
    for hop in range(11):
        if url_ok is not None and not url_ok(url):
            raise ValueError(f"blocked or unsupported URL: {url}")
        with _attempt(sender, url, **kwargs) as response:
            if (allow_redirects and response.status_code in {301, 302, 303, 307, 308}
                    and response.headers.get("Location")):
                if hop == 10:
                    raise RuntimeError("too many HTTP redirects")
                _add(redirects=1)
                cookies.update(response.cookies)
                prepared = getattr(response, "request", None)
                raw = getattr(response, "raw", None)
                if prepared is not None and raw is not None:
                    # response.cookies omits expiry/deletion instructions. Apply
                    # the original Set-Cookie headers to the shared redirect jar.
                    extract_cookies_to_jar(cookies, prepared, raw)
                kwargs["cookies"] = cookies
                destination = urljoin(url, response.headers["Location"])
                headers = dict(kwargs.get("headers") or {})
                authorization = prepared.headers.get("Authorization") if prepared is not None else headers.get("Authorization")
                if authorization and not SessionRedirectMixin().should_strip_auth(url, destination):
                    headers["Authorization"] = authorization
                else:
                    headers.pop("Authorization", None)
                kwargs["headers"] = headers
                url = destination
                continue
            yield response
            return
