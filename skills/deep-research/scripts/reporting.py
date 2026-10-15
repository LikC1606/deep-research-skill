"""Local outlines, synthesis packets and structural citation auditing.

The caller supplies the workspace module so all commands share its run and lock.
No model verdict or semantic support is inferred from matching text or numbers.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from markdown_it import MarkdownIt


EVIDENCE_MARKER = re.compile(r"\[(EV-[A-Za-z0-9_-]+)\]")
URL = re.compile(r"https?://[^\s<>\]\)]+")
BOUNDARY = re.compile(r"[。！？!?]|\.(?=\s|\[|$)")
WHITESPACE = re.compile(r"\s*")
MARKDOWN = MarkdownIt("commonmark").enable("table").disable("text_join")
MAX_REPORT_BYTES = 600_000
MAX_PACKET_BYTES = 48_000


def _evidence_token(state, silent):
    """Reserve draft EV markers so adjacent reference links cannot consume them."""
    match = EVIDENCE_MARKER.match(state.src, state.pos)
    if match is None:
        return False
    if not silent:
        state.push("text", "", 0).content = match[0]
    state.pos = match.end()
    return True


MARKDOWN.inline.ruler.before("link", "evidence_marker", _evidence_token)


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _section_id(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value) or value in {".", ".."}:
        raise ValueError("section ID must be 1-64 letters, digits, dots, hyphens or underscores")


def set_section(ws, section: str, title: str, needs: list[str], *, perspective: str | None = None,
                order: int | None = None, reason: str = "") -> dict:
    _section_id(section)
    if not title.strip() or not needs or (order is not None and order < 1):
        raise ValueError("section requires a title, registered Needs and positive order")
    with ws._lock():
        task = ws._require_task(active=True)
        if any(need not in task["needs"] for need in needs):
            raise ValueError("every section Need must be registered in this task")
        plan = _load(ws.root() / "outline.json", {"schema_version": 1, "sections": {}, "revisions": []})
        old = plan["sections"].get(section)
        value = {
            "title": title.strip(), "needs": list(dict.fromkeys(needs)),
            "perspective": perspective.strip() if perspective is not None else old["perspective"] if old else "",
            "order": order if order is not None else old["order"] if old else len(plan["sections"]) + 1,
        }
        if value == old:
            return {"id": section, **value}
        if old is not None and not reason.strip():
            raise ValueError("revising a section requires a reason based on findings or gaps")
        plan["sections"][section] = value
        plan["revisions"].append({"section": section, "before": old, "after": value,
                                  "reason": reason.strip(), "at": datetime.now(timezone.utc).isoformat()})
        ws._write_json(ws.root() / "outline.json", plan)
        return {"id": section, **value, "revision": len(plan["revisions"])}


def outline(ws) -> dict:
    with ws._lock():
        task = ws._require_task()
        plan = _load(ws.root() / "outline.json", {"sections": {}, "revisions": []})
        evidence = list(ws._evidence().values())
    reviews = ws._need_reviews(task["needs"], evidence)
    sections, assigned = [], set()
    for key, section in sorted(plan["sections"].items(), key=lambda item: item[1]["order"]):
        assigned.update(section["needs"])
        sections.append({
            "id": key, **section,
            "remaining_needs": [need for need in section["needs"]
                                if task["needs"][need]["state"] != "answered" or need in reviews],
            "needs_requiring_review": [need for need in section["needs"] if need in reviews],
            "needs_without_evidence": [need for need in section["needs"]
                                       if not any(card["need"] == need for card in evidence)],
        })
    return {"sections": sections, "unassigned_needs": [need for need in task["needs"] if need not in assigned],
            "revision": len(plan["revisions"]), "last_revision": plan["revisions"][-1:]}


def _section_context(ws, section: str) -> tuple[dict, dict, list[dict], str]:
    _section_id(section)
    task = ws._require_task()
    plan = _load(ws.root() / "outline.json", {"sections": {}})
    if section not in plan["sections"]:
        raise ValueError("register the section before writing synthesis or requesting a packet")
    chapter = plan["sections"][section]
    needs = {need: task["needs"][need] for need in chapter["needs"]}
    cards = [card for card in ws._evidence().values() if card["need"] in needs]
    basis = _hash({"section": chapter, "needs": needs, "evidence_ids": sorted(card["id"] for card in cards)})
    return chapter, needs, cards, basis


def save_synthesis(ws, section: str, summary: str, evidence_ids: list[str], *,
                   inferences: list[str] | None = None, limitations: list[str] | None = None) -> dict:
    if not summary.strip() or len(summary) > 12_000 or not evidence_ids:
        raise ValueError("synthesis requires a summary of 1-12000 characters and evidence IDs")
    if any(not item.strip() for item in [*(inferences or []), *(limitations or [])]):
        raise ValueError("inferences and limitations must be non-empty")
    with ws._lock():
        ws._require_task(active=True)
        _, _, cards, basis = _section_context(ws, section)
        available = {card["id"] for card in cards}
        if not set(evidence_ids) <= available:
            raise ValueError("synthesis evidence must belong to a Need assigned to this section")
        value = {"section": section, "summary": summary.strip(),
                 "evidence_ids": list(dict.fromkeys(evidence_ids)), "inferences": inferences or [],
                 "limitations": limitations or [], "basis_hash": basis,
                 "available_evidence_ids": sorted(available), "assessed_by": "caller",
                 "updated_at": datetime.now(timezone.utc).isoformat()}
        ws._write_json(ws.root() / "syntheses" / f"{section}.json", value)
        return value


def _preview(value, characters: int, items: int):
    if isinstance(value, str):
        return value[:characters]
    if isinstance(value, list):
        return [_preview(item, characters, items) for item in value[:items]]
    if isinstance(value, dict):
        return {key: _preview(item, characters, items) for key, item in value.items()}
    return value


def _json_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode()) + 1


def _packet_context_preview(context: dict) -> dict:
    if _json_bytes(context) <= 12_000:
        return context
    for characters, items in ((800, 20), (400, 10), (200, 5), (100, 2), (64, 1)):
        value = _preview({**context, "needs": dict(list(context["needs"].items())[:items])}, characters, items)
        if _json_bytes(value) <= 12_000:
            return value
    raise ValueError("section context cannot fit a packet preview; inspect the saved packet")


def packet(ws, section: str, *, offset: int = 0, limit: int = 5) -> dict:
    if offset < 0 or not 1 <= limit <= 10:
        raise ValueError("offset must be nonnegative and limit must be between 1 and 10")
    with ws._lock():
        chapter, needs, cards, basis = _section_context(ws, section)
        synthesis = _load(ws.root() / "syntheses" / f"{section}.json", None)
    selected = set(synthesis["evidence_ids"]) if synthesis else set()
    reviews = ws._need_reviews(needs, cards)
    # All relations are included, even when the caller selected only supporting cards.
    evidence = [{**card, "selected_in_synthesis": card["id"] in selected} for card in cards]
    context = {
        "section": {"id": section, **chapter}, "needs": needs,
        "remaining_needs": [need for need, value in needs.items() if value["state"] != "answered" or need in reviews],
        "needs_requiring_review": list(reviews),
        "needs_without_evidence": [need for need in needs if not any(card["need"] == need for card in cards)],
        "synthesis": synthesis,
        "new_evidence_ids": [card["id"] for card in cards
                             if synthesis and card["id"] not in synthesis["available_evidence_ids"]],
    }
    counts = Counter(card["relation"] for card in cards)
    full = {**context, "basis_hash": basis,
            "synthesis_stale": synthesis is not None and synthesis["basis_hash"] != basis,
            "relation_counts": {key: counts[key] for key in ("supports", "refutes", "context")},
            "total_needs": len(needs), "total_evidence": len(cards),
            "remaining_need_count": len(context["remaining_needs"]),
            "needs_requiring_review_count": len(reviews),
            "needs_without_evidence_count": len(context["needs_without_evidence"]),
            "new_evidence_count": len(context["new_evidence_ids"]), "evidence": evidence}
    artifact = ws.root() / "packets" / f"{_hash(full)}.json"
    if not artifact.exists():
        ws._write_json(artifact, full)
    preview = _packet_context_preview(context)
    result = {**full, **preview, "evidence": [], "offset": offset,
              "next_offset": offset if offset < len(cards) else None,
              "packet_path": str(artifact.resolve()), "response_byte_limit": MAX_PACKET_BYTES,
              "context_preview_truncated": preview != context, "preview_truncated": preview != context,
              "page_limited_by_bytes": False,
              "note": "Synthesis is caller-authored; counts do not establish independent support. "
                      "Follow next_offset to review all evidence and counterevidence. "
                      "Full context and unabridged evidence are saved at packet_path."}
    for card in evidence[offset:offset + limit]:
        value = {**card, "claim": card["claim"][:800], "source": (card["source"] or "")[:600]}
        truncated = value != card
        value["preview_truncated"] = truncated
        page = [*result["evidence"], value]
        candidate = {**result, "evidence": page,
                     "next_offset": offset + len(page) if offset + len(page) < len(cards) else None,
                     "preview_truncated": result["preview_truncated"] or truncated}
        if _json_bytes(candidate) > MAX_PACKET_BYTES:
            if not result["evidence"]:
                raise ValueError(f"evidence card exceeds packet preview budget; inspect {artifact}")
            result["page_limited_by_bytes"] = True
            break
        result = candidate
    return result


def read_report(path: str) -> str:
    raw = Path(path).read_bytes()
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("report exceeds 600000 bytes")
    return raw.decode("utf-8")


def _audit_preview(binding: dict) -> dict:
    """Keep complete claims and evidence in the artifact, bound the CLI preview."""
    value = {**binding, "sentence": binding["sentence"][:1600],
             "evidence_ids": binding["evidence_ids"][:20],
             "mentioned_urls": [url[:600] for url in binding["mentioned_urls"][:20]],
             "issues": [item[:400] for item in binding["issues"][:20]],
             "evidence": [{**card, "claim": card["claim"][:400], "quote": card["quote"][:800],
                            "source": card["source"][:600]} for card in binding["evidence"][:5]]}
    value["preview_truncated"] = value != binding
    return value


def _markdown_blocks(text: str):
    """Keep paragraphs and table rows together, with their source line ranges."""
    heading, header, row, row_map = False, False, None, None
    for token in MARKDOWN.parse(text):
        if token.type in {"heading_open", "heading_close"}:
            heading = token.type == "heading_open"
        elif token.type in {"thead_open", "thead_close"}:
            header = token.type == "thead_open"
        elif token.type == "tr_open":
            row, row_map = [], token.map
        elif token.type == "tr_close":
            if row and not header:
                yield row_map, row
            row = None
        elif token.type == "inline" and not heading:
            if row is not None:
                row.append(token.children or [])
            else:
                yield token.map, [token.children or []]


def _inline_view(groups):
    """Render a review preview and mask code, escaped text and link labels."""
    chunks, masks, links, position, active_link = [], [], [], 0, None

    def append(value, *, ignored=False):
        nonlocal position
        chunks.append(value)
        masks.append(re.sub(r"[^\n]", " ", value) if ignored else value)
        position += len(value)

    for index, children in enumerate(groups):
        if index:
            append(" | ")
        for child in children:
            if child.type == "link_open":
                active_link = (position, child.attrGet("href") or "")
                append("[", ignored=True)
            elif child.type == "link_close" and active_link is not None:
                start, href = active_link
                append(f"]({href})", ignored=True)
                links.append((start, position, href))
                active_link = None
            elif child.type in {"softbreak", "hardbreak"}:
                append("\n", ignored=active_link is not None)
            elif child.type == "code_inline":
                append(child.markup + child.content + child.markup, ignored=True)
            elif child.type == "text_special":
                # Preserve escapes/entities instead of reconstructing a live EV marker.
                append(child.markup or child.content, ignored=True)
            elif child.type == "html_inline":
                append(child.content, ignored=True)
            elif child.type == "image":
                append("![image]", ignored=True)
            else:
                append(child.content or child.markup, ignored=active_link is not None)
    return "".join(chunks), "".join(masks), links


def _sentences(text: str):
    """Markdown structure is parsed; sentence boundaries remain heuristic."""
    for source_lines, groups in _markdown_blocks(text):
        display, prose, links = _inline_view(groups)
        urls = [(left, right, url) for left, right, url in links if url.startswith(("http://", "https://"))]
        bare = list(URL.finditer(prose))
        urls.extend((match.start(), match.end(), MARKDOWN.normalizeLink(match[0].rstrip(".,;:!?。；，！")))
                    for match in bare)
        urls.sort(key=lambda item: item[0])
        url_positions = [item[0] for item in urls]
        annotations = {match.start(): match.end() for match in EVIDENCE_MARKER.finditer(prose)}
        annotations.update({left: right for left, right, _ in links})
        # URLs contain punctuation that must not split the surrounding sentence.
        pieces, previous = [], 0
        for match in bare:
            pieces.extend((prose[previous:match.start()], " " * (match.end() - match.start())))
            previous = match.end()
        pieces.append(prose[previous:])
        boundaries = "".join(pieces)

        def candidate(left, right):
            return {"line": source_lines[0] + 1, "end_line": source_lines[1],
                    "sentence": display[left:right].strip(),
                    "evidence_ids": list(dict.fromkeys(EVIDENCE_MARKER.findall(prose[left:right]))),
                    "mentioned_urls": list(dict.fromkeys(url for _, _, url in urls[
                        bisect_left(url_positions, left):bisect_left(url_positions, right)]))}

        start = 0
        for boundary in BOUNDARY.finditer(boundaries):
            if boundary.start() < start:
                continue
            end = boundary.end()
            # A citation after punctuation, including a wrapped line, belongs here.
            while (following := WHITESPACE.match(display, end).end()) in annotations:
                end = annotations[following]
            if display[start:end].strip():
                yield candidate(start, end)
            start = end
        if display[start:].strip() and (prose[start:].strip() or any(left >= start for left, _, _ in links)):
            yield candidate(start, len(display))


def audit_report(ws, path: str, *, offset: int = 0, limit: int = 10) -> dict:
    if offset < 0 or not 1 <= limit <= 20:
        raise ValueError("offset must be nonnegative and limit must be between 1 and 20")
    text = read_report(path)
    with ws._lock():
        evidence = ws._evidence()
    citations = {MARKDOWN.normalizeLink(url) for url in _load(ws.root().parent / ".deep-research-citations.json", [])}
    bindings, checked = [], {}
    for candidate in _sentences(text):
        ids = candidate["evidence_ids"]
        issues, cards = [], []
        if not ids:
            issues.append("unbound_text")
        for evidence_id in ids:
            card = evidence.get(evidence_id)
            if card is None:
                issues.append(f"dangling_evidence:{evidence_id}")
                continue
            key = (card["ref"], card.get("snapshot_id", card["content_hash"]))
            try:
                if key not in checked:
                    checked[key] = ws.require_source(*key)
                _, body = checked[key]
                locator = card["locator"]
                if body[locator["start_char"]:locator["end_char"]] != card["quote"]:
                    raise ValueError("saved quote no longer matches its locator")
            except (ValueError, OSError, KeyError) as exc:
                issues.append(f"invalid_snapshot:{evidence_id}:{str(exc)[:160]}")
            if card["relation"] != "supports":
                issues.append(f"{card['relation']}_relation:{evidence_id}")
            cards.append({key: card[key] for key in (
                "id", "need", "claim", "relation", "source", "ref", "content_hash",
                "quote", "locator", "content_scope", "source_truncated",
            )})
            if "snapshot_id" in card:
                cards[-1]["snapshot_id"] = card["snapshot_id"]
        for url in candidate["mentioned_urls"]:
            if url not in citations:
                issues.append(f"unread_url:{url}")
            elif ids and url not in {MARKDOWN.normalizeLink(card["source"]) for card in cards}:
                issues.append(f"url_evidence_mismatch:{url}")
        bindings.append({**candidate, "evidence": cards, "issues": issues,
                         "semantic_verdict": "not_assessed"})
    report_hash = hashlib.sha256(text.encode()).hexdigest()
    counts = Counter(issue.split(":", 1)[0] for binding in bindings for issue in binding["issues"])
    result = {"report_path": str(Path(path).resolve()), "report_hash": report_hash,
              "total_candidates": len(bindings), "bound_candidates": sum(bool(item["evidence_ids"]) for item in bindings),
              "issue_counts": dict(counts), "structural_issues_found": bool(counts),
              "semantic_verdict": "not_assessed", "segmentation": "markdown_sentence_candidates",
              "sentence_representation": "normalized_markdown_with_source_block_lines",
              "bindings": bindings}
    artifact = ws.root() / "audits" / f"{report_hash}.json"
    ws._write_json(artifact, result)
    page, page_bytes = [], 0
    for binding in bindings[offset:offset + limit]:
        preview = _audit_preview(binding)
        size = len(json.dumps(preview, ensure_ascii=False).encode())
        if page and page_bytes + size > 40_000:
            break
        page.append(preview)
        page_bytes += size
    return {**result, "bindings": page, "offset": offset,
            "next_offset": offset + len(page) if offset + len(page) < len(bindings) else None,
            "audit_path": str(artifact.resolve()),
            "note": "Unbound text is a review candidate, not a proven factual error. All semantic support needs review."}
