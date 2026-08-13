#!/usr/bin/env python3
"""Small read-only web adapter for the deep-research benchmark pilot.

It returns compact search cards and local text windows so agent runs are
comparable without putting whole search-engine pages in the context.
"""

from __future__ import annotations

import argparse
import fcntl
from functools import lru_cache
import html
import ipaddress
import json
import os
import re
import socket
import sys
import queue
import threading
from pathlib import Path
import urllib.parse
import xml.etree.ElementTree as ET
from collections import OrderedDict

import requests
from bs4 import BeautifulSoup


UA = "deep-research-benchmark/0.1 (read-only; research evaluation)"
MAX_BYTES = 400_000
TIMEOUT = 15


def _policy_parts() -> tuple[str, ...]:
    return tuple(
        part.strip().casefold()
        for part in os.environ.get("DRBENCH_BLOCK_URL_SUBSTRINGS", "").split(",")
        if part.strip()
    )


def _blocked_by_policy(url: str) -> bool:
    lowered = url.casefold()
    return any(part in lowered for part in _policy_parts())


def _reserve(kind: str) -> None:
    """Enforce per-run limits when the pilot sets a run directory."""
    run_dir = os.environ.get("DRBENCH_RUN_DIR")
    if not run_dir:
        return
    limit_name = "DRBENCH_MAX_SEARCH" if kind == "search" else "DRBENCH_MAX_READ"
    limit = int(os.environ.get(limit_name, "0"))
    if limit <= 0:
        return
    state_path = Path(run_dir) / ".adapter-counts.json"
    lock_path = Path(run_dir) / ".adapter-counts.lock"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                state = {"search": 0, "read": 0, "blocked": 0}
            if state.get(kind, 0) >= limit:
                state["blocked"] = state.get("blocked", 0) + 1
                state_path.write_text(json.dumps(state), encoding="utf-8")
                raise RuntimeError(f"{kind} budget exhausted ({limit})")
            state[kind] = state.get(kind, 0) + 1
            state_path.write_text(json.dumps(state), encoding="utf-8")
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@lru_cache(maxsize=512)
def _blocked_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return True
    resolved: queue.Queue = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            resolved.put((socket.getaddrinfo(host, None), None))
        except OSError as exc:
            resolved.put(([], exc))

    threading.Thread(target=resolve, daemon=True).start()
    try:
        addresses, error = resolved.get(timeout=2)
    except queue.Empty:
        return True
    if error is not None:
        return False
    for item in addresses:
        ip = ipaddress.ip_address(item[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def _url_ok(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and not _blocked_by_policy(url)
        and not _blocked_host(parsed.hostname or "")
    )


def _get(url: str) -> tuple[str, str]:
    if not _url_ok(url):
        raise ValueError(f"blocked or unsupported URL: {url}")
    response = requests.get(
        url,
        headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.1"},
        timeout=TIMEOUT,
        allow_redirects=True,
    )
    if not _url_ok(response.url):
        raise ValueError(f"redirected to blocked URL: {response.url}")
    response.raise_for_status()
    raw = response.content[:MAX_BYTES]
    encoding = response.encoding or "utf-8"
    return raw.decode(encoding, errors="replace"), response.url


def _clean(value: str, limit: int = 700) -> str:
    value = html.unescape(re.sub(r"\s+", " ", value or "")).strip()
    return value[:limit]


def _record_event(kind: str, payload: dict) -> None:
    run_dir = os.environ.get("DRBENCH_RUN_DIR")
    if not run_dir:
        return
    path = Path(run_dir) / ".adapter-events.jsonl"
    lock_path = Path(run_dir) / ".adapter-events.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"kind": kind, **payload}, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _bing(query: str, limit: int) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?format=rss&" + urllib.parse.urlencode({"q": query})
    try:
        body, _ = _get(url)
        root = ET.fromstring(body)
    except Exception:
        return []
    cards: list[dict[str, str]] = []
    for item in root.findall(".//item")[:limit]:
        link = (item.findtext("link") or "").strip()
        title = _clean(item.findtext("title") or "", 240)
        snippet = _clean(item.findtext("description") or "", 500)
        if link and title:
            cards.append({"title": title, "url": link, "snippet": snippet, "engine": "bing-rss"})
    return cards


def _so360(query: str, limit: int) -> list[dict[str, str]]:
    url = "https://www.so.com/s?" + urllib.parse.urlencode({"q": query})
    try:
        body, _ = _get(url)
    except Exception:
        return []
    soup = BeautifulSoup(body, "html.parser")
    cards: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in soup.select("li.res-list"):
        anchor = item.select_one("h3.res-title a")
        if anchor is None:
            continue
        target = anchor.get("data-mdurl") or anchor.get("href") or ""
        if not target.startswith(("http://", "https://")) or target in seen:
            continue
        title = _clean(anchor.get_text(" "), 240)
        snippet_node = item.select_one(".res-list-summary")
        snippet = _clean(snippet_node.get_text(" ") if snippet_node else item.get_text(" "), 500)
        if not title:
            continue
        seen.add(target)
        cards.append({"title": title, "url": target, "snippet": snippet, "engine": "360"})
        if len(cards) >= limit:
            break
    return cards


def _openalex(query: str, limit: int) -> list[dict[str, str]]:
    params = {
        "search": query,
        "per-page": str(limit),
        "select": (
            "id,display_name,publication_year,doi,primary_location,"
            "cited_by_count,abstract_inverted_index"
        ),
    }
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    try:
        body, _ = _get(url)
        rows = json.loads(body).get("results", [])
    except Exception:
        return []
    cards: list[dict[str, str]] = []
    for row in rows:
        location = row.get("primary_location") or {}
        target = location.get("landing_page_url") or row.get("doi") or row.get("id") or ""
        title = _clean(row.get("display_name") or "", 240)
        if not title or not _url_ok(target):
            continue
        inverted = row.get("abstract_inverted_index") or {}
        tokens: list[tuple[int, str]] = []
        for token, positions in inverted.items():
            tokens.extend((int(position), token) for position in positions)
        abstract = " ".join(token for _, token in sorted(tokens))
        source = (location.get("source") or {}).get("display_name") or ""
        details = " | ".join(
            part
            for part in (
                str(row.get("publication_year") or ""),
                source,
                f"cited by {row.get('cited_by_count', 0)}",
            )
            if part
        )
        snippet = _clean(f"{details}. {abstract}", 700)
        cards.append({"title": title, "url": target, "snippet": snippet, "engine": "openalex"})
    return cards


def _interleave(groups: list[list[dict[str, str]]], limit: int) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    for index in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if index < len(group):
                merged.append(group[index])
                if len(merged) >= limit:
                    return merged
    return merged


def search(query: str, limit: int = 8) -> dict:
    _reserve("search")
    # 360 usually has useful Chinese snippets; Bing RSS is a fallback lane.
    web_cards = _so360(query, limit)
    web_cards.extend(_bing(query, limit))
    if os.environ.get("DRBENCH_ACADEMIC_SEARCH") == "1":
        cards = _interleave([_openalex(query, max(2, limit // 2)), web_cards], limit * 2)
    else:
        cards = web_cards
    deduped: OrderedDict[str, dict[str, str]] = OrderedDict()
    for card in cards:
        key = card["url"].split("#", 1)[0]
        if _url_ok(key) and key not in deduped:
            deduped[key] = card
        if len(deduped) >= limit:
            break
    result = {"query": query, "results": list(deduped.values())}
    _record_event("search", result)
    return result


def read_page(url: str, terms: list[str], window: int = 650, max_windows: int = 5) -> dict:
    _reserve("read")
    body, final_url = _get(url)
    soup = BeautifulSoup(body, "html.parser")
    for node in soup(["script", "style", "noscript", "svg", "form", "nav", "footer"]):
        node.decompose()
    title = _clean(soup.title.get_text(" ") if soup.title else "", 240)
    text = _clean(soup.get_text(" "), MAX_BYTES)
    lowered = text.casefold()
    spans: list[str] = []
    positions: list[int] = []
    for term in terms:
        start = 0
        needle = term.casefold()
        while needle and (pos := lowered.find(needle, start)) >= 0:
            positions.append(pos)
            start = pos + len(needle)
            if len(positions) >= max_windows * 3:
                break
    selected: list[int] = []
    for pos in sorted(set(positions)):
        if all(abs(pos - old) > window for old in selected):
            selected.append(pos)
        if len(selected) >= max_windows:
            break
    for pos in selected:
        left = max(0, pos - window)
        right = min(len(text), pos + window)
        spans.append(text[left:right])
    if not spans:
        spans = [text[:window * 2]] if text else []
    result = {"url": final_url, "title": title, "windows": spans}
    _record_event("read", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=8)
    p_read = sub.add_parser("read")
    p_read.add_argument("url")
    p_read.add_argument("--term", action="append", default=[])
    p_read.add_argument("--window", type=int, default=650)
    args = parser.parse_args()
    try:
        result = search(args.query, max(1, min(args.limit, 12))) if args.command == "search" else read_page(args.url, args.term, args.window)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
