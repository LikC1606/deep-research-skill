#!/usr/bin/env python3
"""Retriever-v1 candidate for the deep-research benchmark.

The benchmark baseline remains in ``search.py``.  This adapter reuses its
budget, URL-policy, and event-log code while changing only result selection and
document extraction.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import fitz
import requests
from bs4 import BeautifulSoup


def _load_baseline():
    path = Path(__file__).with_name("search.py")
    spec = importlib.util.spec_from_file_location("drbench_baseline_adapter", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load baseline adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = _load_baseline()

MAX_DOWNLOAD_BYTES = 10_000_000
MAX_TEXT_CHARS = 400_000
WORD_RE = re.compile(r"[a-z0-9]+")
HAN_RE = re.compile(r"[\u3400-\u9fff]+")
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "in", "is", "it", "of", "on", "or", "that", "the", "this",
    "to", "was", "were", "what", "when", "where", "which", "who", "why",
    "with",
}
ACADEMIC_HOSTS = (
    "acm.org", "arxiv.org", "cambridge.org", "doi.org", "ieee.org",
    "jstor.org", "nature.com", "ncbi.nlm.nih.gov", "oup.com", "plos.org",
    "science.org", "sciencedirect.com", "springer.com", "ssrn.com",
    "tandfonline.com", "wiley.com",
)
LOW_QUALITY_HOSTS = (
    "baike.baidu.com", "blog.csdn.net", "medium.com", "quora.com",
    "so.com", "wenku.baidu.com", "zhidao.baidu.com",
)


def _is_english_query(query: str) -> bool:
    latin = len(re.findall(r"[A-Za-z]", query))
    han = len(re.findall(r"[\u3400-\u9fff]", query))
    return latin >= 3 and latin >= han * 2


def _tokens(value: str) -> set[str]:
    lowered = value.casefold()
    tokens = {
        token for token in WORD_RE.findall(lowered)
        if len(token) > 1 and token not in STOPWORDS
    }
    for chunk in HAN_RE.findall(lowered):
        if len(chunk) <= 2:
            tokens.add(chunk)
        else:
            tokens.update(chunk[index:index + 2] for index in range(len(chunk) - 1))
    return tokens


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.casefold().split(":", 1)[0].removeprefix("www.")


def _source_type(url: str, engine: str = "") -> str:
    host = _host(url)
    if host in {"github.com", "raw.githubusercontent.com"}:
        return "practitioner_artifact"
    if engine == "openalex" or host.endswith((".gov", ".edu")):
        return "primary_or_academic"
    if any(host == item or host.endswith("." + item) for item in ACADEMIC_HOSTS):
        return "primary_or_academic"
    return "web"


def _source_bonus(url: str, engine: str) -> float:
    host = _host(url)
    if any(host == item or host.endswith("." + item) for item in LOW_QUALITY_HOSTS):
        return -2.5
    bonus = 1.0 if engine == "openalex" else 0.0
    if host.endswith((".gov", ".edu")):
        bonus += 1.5
    elif any(host == item or host.endswith("." + item) for item in ACADEMIC_HOSTS):
        bonus += 1.25
    return bonus


def _bing(query: str, limit: int, english: bool) -> list[dict]:
    params = {"q": query, "format": "rss"}
    if english:
        params.update(
            {"mkt": "en-US", "setlang": "en-US", "cc": "us", "ensearch": "1"}
        )
    url = "https://www.bing.com/search?" + urllib.parse.urlencode(params)
    try:
        body, _ = base._get(url)
        root = ET.fromstring(body)
    except Exception:
        return []
    cards = []
    for item in root.findall(".//item")[:limit]:
        target = (item.findtext("link") or "").strip()
        title = base._clean(item.findtext("title") or "", 240)
        if not target or not title or not base._url_ok(target):
            continue
        cards.append(
            {
                "title": title,
                "url": target,
                "snippet": base._clean(item.findtext("description") or "", 700),
                "engine": "bing-rss",
            }
        )
    return cards


def _abstract(row: dict) -> str:
    positions = []
    for token, indexes in (row.get("abstract_inverted_index") or {}).items():
        positions.extend((int(index), token) for index in indexes)
    return " ".join(token for _, token in sorted(positions))


def _best_openalex_url(row: dict) -> str:
    locations = [row.get("best_oa_location") or {}, row.get("primary_location") or {}]
    for location in locations:
        for key in ("pdf_url", "landing_page_url"):
            value = location.get(key) or ""
            if value and base._url_ok(value):
                return value
    for value in (row.get("doi") or "", row.get("id") or ""):
        if value and base._url_ok(value):
            return value
    return ""


def _openalex(query: str, limit: int) -> list[dict]:
    # OpenAlex full-text search now consumes daily credits.  Autocomplete and
    # direct work lookup are free, so discover compact candidates here and
    # retrieve the abstract only if the agent chooses to read one.
    words = [token for token in WORD_RE.findall(query.casefold()) if token not in STOPWORDS]
    academic_query = " ".join(words[:3]) or query
    params = {"q": academic_query}
    url = "https://api.openalex.org/autocomplete/works?" + urllib.parse.urlencode(params)
    try:
        body, _ = base._get(url)
        rows = json.loads(body).get("results", [])
    except Exception:
        return []
    cards = []
    for row in rows[:limit]:
        target = row.get("external_id") or row.get("id") or ""
        title = base._clean(row.get("display_name") or "", 240)
        if not target or not title or not base._url_ok(target):
            continue
        details = " | ".join(
            part for part in (
                row.get("hint") or "",
                f"cited by {row.get('cited_by_count', 0)}",
            ) if part
        )
        cards.append(
            {
                "title": title,
                "url": target,
                "snippet": base._clean(details, 900),
                "engine": "openalex",
                "openalex_id": row.get("id") or "",
                "cited_by_count": int(row.get("cited_by_count") or 0),
            }
        )
    return cards


def _rank(cards: list[dict], query: str, limit: int) -> list[dict]:
    query_tokens = _tokens(query)
    query_phrase = " ".join(WORD_RE.findall(query.casefold()))
    unique = {}
    for card in cards:
        key = card["url"].split("#", 1)[0]
        if key not in unique and base._url_ok(key):
            card = dict(card, url=key)
            title_tokens = _tokens(card.get("title", ""))
            snippet_tokens = _tokens(card.get("snippet", ""))
            title_hits = len(query_tokens & title_tokens)
            all_hits = len(query_tokens & (title_tokens | snippet_tokens))
            coverage = all_hits / max(1, len(query_tokens))
            phrase_bonus = 0.75 if len(query_phrase) >= 8 and query_phrase in (
                " ".join(WORD_RE.findall((card.get("title", "") + " " + card.get("snippet", "")).casefold()))
            ) else 0.0
            citation_bonus = min(0.6, math.log1p(card.get("cited_by_count", 0)) / 12)
            card["_rank"] = (
                title_hits * 2.5 + all_hits * 0.6 + coverage * 3.0 + phrase_bonus
                + citation_bonus + _source_bonus(key, card.get("engine", ""))
            )
            unique[key] = card

    remaining = list(unique.values())
    selected = []
    domain_counts: Counter[str] = Counter()
    while remaining and len(selected) < limit:
        best = max(
            remaining,
            key=lambda card: (
                card["_rank"] - 1.5 * domain_counts[_host(card["url"])],
                card.get("title", ""),
                card["url"],
            ),
        )
        remaining.remove(best)
        domain_counts[_host(best["url"])] += 1
        best["source_type"] = _source_type(best["url"], best.get("engine", ""))
        best.pop("_rank", None)
        best.pop("cited_by_count", None)
        selected.append(best)
    return selected


def search(query: str, limit: int = 8) -> dict:
    base._reserve("search")
    english = _is_english_query(query)
    fetch_limit = max(8, limit)
    cards = _bing(query, fetch_limit, english)
    if not english:
        cards.extend(base._so360(query, fetch_limit))
    if __import__("os").environ.get("DRBENCH_ACADEMIC_SEARCH") == "1":
        cards.extend(_openalex(query, fetch_limit))
    result = {"query": query, "results": _rank(cards, query, limit)}
    base._record_event("search", result)
    return result


def _fetch_bytes(url: str) -> tuple[bytes, str, str]:
    if not base._url_ok(url):
        raise ValueError(f"blocked or unsupported URL: {url}")
    response = requests.get(
        url,
        headers={
            "User-Agent": base.UA,
            "Accept": "application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.1",
        },
        timeout=base.TIMEOUT,
        allow_redirects=True,
        stream=True,
    )
    if not base._url_ok(response.url):
        raise ValueError(f"redirected to blocked URL: {response.url}")
    response.raise_for_status()
    chunks = []
    size = 0
    for chunk in response.iter_content(64 * 1024):
        if not chunk:
            continue
        remaining = MAX_DOWNLOAD_BYTES - size
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining])
        size += min(len(chunk), remaining)
    return b"".join(chunks), response.url, response.headers.get("Content-Type", "")


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:MAX_TEXT_CHARS]


def _pdf_text(raw: bytes) -> tuple[str, str]:
    with fitz.open(stream=raw, filetype="pdf") as document:
        metadata = document.metadata or {}
        title = base._clean(metadata.get("title") or "", 240)
        parts = []
        size = 0
        for page in document:
            value = page.get_text("text")
            if value:
                parts.append(value)
                size += len(value)
            if size >= MAX_TEXT_CHARS:
                break
    return title, _normalise_text(" ".join(parts))


def _html_text(raw: bytes, encoding: str = "utf-8") -> tuple[str, str]:
    soup = BeautifulSoup(raw.decode(encoding, errors="replace"), "html.parser")
    for node in soup(["script", "style", "noscript", "svg", "form", "nav", "footer"]):
        node.decompose()
    title = base._clean(soup.title.get_text(" ") if soup.title else "", 240)
    main = soup.select_one("main, article")
    text = (main or soup).get_text(" ")
    return title, _normalise_text(text)


def _soft_error_page(title: str, text: str) -> bool:
    title_value = title.strip().casefold()
    if re.match(r"^(?:40[134]|410)(?:\b|\s*[|:\-])", title_value):
        return True
    if re.match(r"^(?:page\s+)?not found\b", title_value):
        return True
    body = text[:1_000].casefold()
    return len(text) < 2_000 and bool(
        re.search(r"\b(?:404\s+not found|page not found|requested page (?:was not|could not be) found)\b", body)
    )


def _content_shell(title: str, text: str) -> bool:
    value = f"{title} {text[:1_000]}".casefold()
    return len(text) < 2_000 and bool(
        re.search(
            r"\b(?:redirecting|cookies? must be enabled|enable cookies|"
            r"checking your browser|verify you are human|access denied)\b",
            value,
        )
    )


def _arxiv_pdf(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.casefold().removeprefix("www.")
    match = re.match(r"/(?:abs|html)/([^?#]+)", parsed.path)
    if host in {"arxiv.org", "export.arxiv.org"} and match:
        return "https://arxiv.org/pdf/" + match.group(1)
    return None


def _github_raw_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.casefold().removeprefix("www.") != "github.com":
        return None
    parts = parsed.path.split("/")
    if len(parts) < 7 or parts[3] != "blob":
        return None
    raw_path = "/".join(parts[1:3] + parts[4:])
    return "https://raw.githubusercontent.com/" + raw_path


def _doi_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(urllib.parse.unquote(url))
    if parsed.netloc.casefold().removeprefix("www.") == "doi.org":
        value = parsed.path.lstrip("/")
        return value or None
    match = DOI_RE.search(parsed.path)
    return match.group(0).rstrip(".,;") if match else None


def _pubmed_id_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.casefold().removeprefix("www.")
    if host not in {"pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov"}:
        return None
    match = re.search(r"/(?:pubmed/)?(\d{4,10})(?:/|$)", parsed.path)
    return match.group(1) if match else None


def _pubmed_record(identifier: str) -> tuple[str, str, str] | None:
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode(
        {"db": "pubmed", "id": identifier, "rettype": "abstract", "retmode": "xml"}
    )
    try:
        body, final_url = base._get(url)
        root = ET.fromstring(body)
    except Exception:
        return None
    title_node = root.find(".//ArticleTitle")
    title = base._clean("".join(title_node.itertext()) if title_node is not None else "", 240)
    parts = []
    for node in root.findall(".//Abstract/AbstractText"):
        text = _normalise_text("".join(node.itertext()))
        if not text:
            continue
        label = str(node.attrib.get("Label") or "").strip()
        parts.append(f"{label}: {text}" if label else text)
    text = _normalise_text(" ".join(parts))
    if not text:
        return None
    return title, text, final_url


def _openalex_record(identifier: str) -> tuple[str, str, str] | None:
    if identifier.startswith("W"):
        work_id = identifier
    else:
        work_id = "https://doi.org/" + identifier
    url = "https://api.openalex.org/works/" + urllib.parse.quote(work_id, safe="")
    url += "?" + urllib.parse.urlencode(
        {"select": "id,display_name,doi,abstract_inverted_index"}
    )
    try:
        body, final_url = base._get(url)
        row = json.loads(body)
    except Exception:
        return None
    text = _normalise_text(_abstract(row))
    if not text:
        return None
    return base._clean(row.get("display_name") or "", 240), text, final_url


def _openalex_id_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.casefold().removeprefix("www.") != "openalex.org":
        return None
    match = re.fullmatch(r"/(?:works/)?(W\d+)/?", parsed.path, re.IGNORECASE)
    return match.group(1).upper() if match else None


def _windows(text: str, terms: list[str], window: int, max_windows: int) -> list[str]:
    lowered = text.casefold()
    positions = []
    for term in terms:
        needle = term.strip().casefold()
        start = 0
        while needle and (position := lowered.find(needle, start)) >= 0:
            positions.append(position)
            start = position + len(needle)
            if len(positions) >= max_windows * max(3, len(terms)):
                break
    selected = []
    for position in sorted(set(positions)):
        if all(abs(position - old) > window for old in selected):
            selected.append(position)
        if len(selected) >= max_windows:
            break
    if not selected:
        return [text[:window * 2]] if text else []
    return [
        text[max(0, position - window):min(len(text), position + window)]
        for position in selected
    ]


def read_page(url: str, terms: list[str], window: int = 650, max_windows: int = 5) -> dict:
    base._reserve("read")
    attempts = []
    arxiv_pdf = _arxiv_pdf(url)
    candidates = [(arxiv_pdf, "arxiv_pdf")] if arxiv_pdf else []
    candidates.append((url, "direct"))
    github_raw = _github_raw_url(url)
    if github_raw:
        candidates.append((github_raw, "github_raw"))
    title = ""
    text = ""
    final_url = url
    content_type = ""
    fallback = "none"
    errors = []
    openalex_id = _openalex_id_from_url(url)
    doi = _doi_from_url(url)
    pubmed_id = _pubmed_id_from_url(url)
    recovered = (
        _openalex_record(openalex_id)
        if openalex_id
        else _openalex_record(doi)
        if doi
        else _pubmed_record(pubmed_id)
        if pubmed_id
        else None
    )
    if recovered:
        title, text, final_url = recovered
        content_type = "pubmed_abstract" if pubmed_id else "openalex_abstract"
        fallback = "pubmed_eutils" if pubmed_id else "openalex_abstract"

    for target, label in candidates if not text else []:
        if not target or target in attempts:
            continue
        attempts.append(target)
        try:
            raw, fetched_url, mime = _fetch_bytes(target)
            is_pdf = raw.startswith(b"%PDF") or "application/pdf" in mime.casefold()
            if is_pdf:
                title, text = _pdf_text(raw)
                content_type = "pdf"
            else:
                encoding_match = re.search(r"charset=([^; ]+)", mime, re.IGNORECASE)
                encoding = encoding_match.group(1).strip('"\'') if encoding_match else "utf-8"
                title, text = _html_text(raw, encoding)
                content_type = "html"
                if _soft_error_page(title, text) or _content_shell(title, text):
                    errors.append(f"{target}: unreadable content shell ({title or 'untitled'})")
                    title = ""
                    text = ""
                    continue
            if text:
                final_url = fetched_url
                fallback = label
                break
            errors.append(f"{target}: empty text")
        except Exception as exc:
            errors.append(f"{target}: {exc}")

    if not text:
        recovered = _openalex_record(doi) if doi else None
        if recovered:
            title, text, final_url = recovered
            content_type = "openalex_abstract"
            fallback = "openalex_abstract"
    if not text:
        raise RuntimeError("; ".join(errors) or f"no readable text: {url}")

    result = {
        "url": url,
        "resolved_url": final_url,
        "title": title,
        "windows": _windows(text, terms, window, max_windows),
        "content_type": content_type,
        "extracted_chars": len(text),
        "source_type": (
            "primary_or_academic"
            if content_type in {"openalex_abstract", "pubmed_abstract"}
            else _source_type(final_url)
        ),
        "fallback": fallback,
        "failed_attempts": len(errors),
    }
    base._record_event("read", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    search_parser = sub.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=8)
    read_parser = sub.add_parser("read")
    read_parser.add_argument("url")
    read_parser.add_argument("--term", action="append", default=[])
    read_parser.add_argument("--window", type=int, default=650)
    args = parser.parse_args()
    try:
        if args.command == "search":
            result = search(args.query, max(1, min(args.limit, 12)))
        else:
            result = read_page(args.url, args.term, max(100, min(args.window, 2_000)))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
