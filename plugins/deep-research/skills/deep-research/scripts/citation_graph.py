"""Bounded Semantic Scholar discovery requests; returned papers remain unread leads."""

from __future__ import annotations

import json
import re
from urllib.parse import quote, unquote, urlencode, urlsplit


API = "https://api.semanticscholar.org/graph/v1/paper/"


def paper_id(value: str) -> str:
    value = value.strip()
    if value.startswith(("https://", "http://")):
        parsed = urlsplit(value)
        host, path = (parsed.hostname or "").lower(), unquote(parsed.path).strip("/")
        if host in {"doi.org", "dx.doi.org"}:
            value = "DOI:" + path
        elif host in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"} and path.startswith(("abs/", "pdf/")):
            value = "ARXIV:" + path.split("/", 1)[1].removesuffix(".pdf")
        elif host in {"semanticscholar.org", "www.semanticscholar.org"} and path.startswith("paper/"):
            value = path.rsplit("/", 1)[-1]
        else:
            raise ValueError("follow requires a DOI, arXiv ID, Semantic Scholar ID, or a supported paper URL/REF")
    value = re.sub(r"^(doi|arxiv):", lambda match: match[1].upper() + ":", value, flags=re.I)
    if re.fullmatch(r"10\.\d{4,9}/[^\s?#]+", value):
        value = "DOI:" + value
    if re.fullmatch(r"DOI:10\.\d{4,9}/[^\s?#]+", value):
        return value
    if re.fullmatch(r"ARXIV:(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", value):
        return value
    if re.fullmatch(r"[0-9a-fA-F]{40}", value):
        return value.lower()
    raise ValueError("unsupported paper identifier; use DOI:10.../... or ARXIV:... or a Semantic Scholar ID")


def fetch_papers(identifier: str, direction: str, offset: int, limit: int, fetch_bytes) -> dict:
    identifier = paper_id(identifier)
    if direction not in {"references", "citations"} or offset < 0 or not 1 <= limit <= 20:
        raise ValueError("direction must be references/citations, offset nonnegative, and limit 1-20")
    url = API + quote(identifier, safe="") + "/" + direction + "?" + urlencode({
        "fields": "title,url,year,externalIds,openAccessPdf", "offset": offset, "limit": limit,
    })
    raw, _, _ = fetch_bytes(url)
    if len(raw) > 2_000_000:
        raise ValueError("citation response exceeds 2000000 bytes")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("invalid citation graph response")
    rows = []
    for edge in payload["data"][:limit]:
        if not isinstance(edge, dict):
            continue
        paper = edge.get("citedPaper" if direction == "references" else "citingPaper")
        if not isinstance(paper, dict) or not paper.get("title"):
            continue
        external = paper.get("externalIds") or {}
        pdf = paper.get("openAccessPdf") or {}
        target = pdf.get("url") or ("https://doi.org/" + external["DOI"] if external.get("DOI") else paper.get("url"))
        if not isinstance(target, str) or urlsplit(target).scheme not in {"https", "http"}:
            continue
        rows.append({"title": str(paper["title"])[:500], "url": target,
                     "year": paper.get("year"), "paper_id": paper.get("paperId"),
                     "doi": external.get("DOI"), "engine": "semantic-scholar-graph",
                     "source_type": "paper", "discovery": direction, "parent_paper": identifier})
    next_offset = payload.get("next")
    if not isinstance(next_offset, int) or isinstance(next_offset, bool) or next_offset <= offset:
        next_offset = None
    return {"seed_paper": identifier, "direction": direction, "offset": offset,
            "next_offset": next_offset, "results": rows,
            "providers_used": ["semantic_scholar"], "provider_mode": "semantic_scholar"}
