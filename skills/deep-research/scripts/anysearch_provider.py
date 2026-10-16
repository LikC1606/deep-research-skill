#!/usr/bin/env python3
"""Narrow AnySearch discovery adapter for deep-research.

This module only returns search cards. Evidence reading, citation admission, and
research state remain owned by research.py.
"""

from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import re
from collections.abc import Callable
from typing import Any

import requests

_metrics_spec = importlib.util.spec_from_file_location("deep_research_metrics", Path(__file__).with_name("run_metrics.py"))
metrics = importlib.util.module_from_spec(_metrics_spec)
_metrics_spec.loader.exec_module(metrics)


ENDPOINT = "https://api.anysearch.com/mcp"
CLIENT_HEADER = "deep-research/1.0"
MAX_RESPONSE_BYTES = 2_000_000
RESULT_HEADING_RE = re.compile(r"^###\s+\d+\.\s+(.+?)\s*$")
URL_LINE_RE = re.compile(r"^-\s+\*\*URL\*\*:\s*(.+?)\s*$", re.IGNORECASE)
MARKDOWN_LINK_RE = re.compile(r"^\[[^\]]*\]\((https?://[^)]+)\)$", re.IGNORECASE)


class AnySearchError(RuntimeError):
    """Raised when AnySearch cannot safely return candidate cards."""


def _source_policy() -> str:
    value = os.environ.get("DEEP_RESEARCH_SOURCE_POLICY") or os.environ.get(
        "DRBENCH_SOURCE_POLICY", "web_allowed"
    )
    value = value.strip().casefold()
    if value not in {"local_only", "web_allowed", "mixed"}:
        raise AnySearchError(f"unsupported source policy: {value}")
    return value


def _timeout() -> float:
    raw = os.environ.get("DEEP_RESEARCH_ANYSEARCH_TIMEOUT", "15")
    try:
        value = float(raw)
    except ValueError:
        value = 15.0
    return max(2.0, min(value, 30.0))


def _read_limited_json(response: Any) -> dict:
    chunks = []
    size = 0
    for chunk in response.iter_content(64 * 1024):
        if not chunk:
            continue
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise AnySearchError("AnySearch response exceeds size limit")
        chunks.append(chunk)
    try:
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnySearchError("AnySearch returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise AnySearchError("AnySearch returned an invalid response shape")
    return value


def _rpc(arguments: dict, post: Callable | None = None) -> str:
    if _source_policy() == "local_only":
        raise AnySearchError("AnySearch is disabled by source_policy=local_only")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Anysearch-Client": CLIENT_HEADER,
    }
    api_key = os.environ.get("ANYSEARCH_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "search", "arguments": arguments},
    }
    sender = post or requests.post
    try:
        with metrics.request(sender,
            ENDPOINT,
            json=payload,
            headers=headers,
            timeout=_timeout(),
            allow_redirects=False,
            stream=True,
        ) as response:
            status = int(getattr(response, "status_code", 0) or 0)
            if 300 <= status < 400:
                raise AnySearchError("AnySearch endpoint returned a redirect")
            response.raise_for_status()
            value = _read_limited_json(response)
    except AnySearchError:
        raise
    except requests.RequestException as exc:
        raise AnySearchError(f"AnySearch request failed: {type(exc).__name__}") from exc

    error = value.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "API error")[:200]
        raise AnySearchError(f"AnySearch API error: {message}")
    content = (value.get("result") or {}).get("content") or []
    texts = [
        str(item.get("text") or "")
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    text = "\n".join(part for part in texts if part.strip())
    if not text:
        raise AnySearchError("AnySearch returned no readable search content")
    return text


def _normalise_url(value: str) -> str:
    value = value.strip().strip("<>")
    markdown_match = MARKDOWN_LINK_RE.match(value)
    if markdown_match:
        value = markdown_match.group(1)
    return value.split()[0].rstrip(".,;") if value else ""


def parse_search_markdown(text: str, limit: int) -> list[dict]:
    """Convert AnySearch Markdown into compact, non-citable search cards."""
    cards = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if not current or not current.get("url"):
            current = None
            return
        snippet = " ".join(current.pop("body", []))
        snippet = re.sub(r"\s+", " ", snippet).strip()[:900]
        current["snippet"] = snippet
        current["engine"] = "anysearch"
        cards.append(current)
        current = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        heading = RESULT_HEADING_RE.match(line)
        if heading:
            flush()
            current = {"title": heading.group(1).strip(), "url": "", "body": []}
            continue
        if current is None or not line:
            continue
        url_match = URL_LINE_RE.match(line)
        if url_match:
            current["url"] = _normalise_url(url_match.group(1))
            continue
        if not line.startswith("## Search Results"):
            current["body"].append(line)
    flush()
    return cards[: max(1, min(limit, 10))]


def _arguments(query: str, limit: int, artifact: str | None) -> dict:
    arguments: dict[str, Any] = {
        "query": query,
        "max_results": max(1, min(limit, 10)),
    }
    if artifact == "paper":
        arguments.update(
            {
                "domain": "academic",
                "sub_domain": "academic.search",
                "sub_domain_params": {"sort": "relevance_score"},
            }
        )
    return arguments


def search(
    query: str,
    limit: int = 8,
    artifact: str | None = None,
    *,
    post: Callable | None = None,
) -> list[dict]:
    if not query.strip():
        raise AnySearchError("AnySearch query must not be empty")
    text = _rpc(_arguments(query.strip(), limit, artifact), post=post)
    cards = parse_search_markdown(text, limit)
    if not cards:
        raise AnySearchError("AnySearch returned no parseable search cards")
    return cards
