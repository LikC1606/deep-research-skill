"""Run-scoped task state, source snapshots and evidence; no network or model calls."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


MAX_TEXT_CHARS = 400_000
REF_RE = re.compile(r"REF-[0-9A-F]{16}\Z")


def root() -> Path:
    run = os.environ.get("DRBENCH_RUN_DIR") or os.environ.get("DEEP_RESEARCH_RUN_DIR")
    return Path(run or os.getcwd()) / ".deep-research"


def _source_dir(ref: str) -> Path:
    if not REF_RE.fullmatch(ref):
        raise ValueError("invalid source REF")
    return root() / "sources" / ref


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_json(path: Path, value) -> None:
    _atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def _lock():
    root().mkdir(parents=True, exist_ok=True)
    with (root() / "workspace.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def save_source(ref: str, result: dict, text: str) -> tuple[dict, str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise ValueError("source has no readable body")
    truncated = bool(result.get("truncated")) or len(text) > MAX_TEXT_CHARS
    text = text[:MAX_TEXT_CHARS]
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    folder = _source_dir(ref)
    metadata = {
        key: value for key, value in result.items()
        if not key.startswith("_") and key not in {"windows", "locators", "scope_state"}
    }
    metadata.update(
        ref=ref,
        content_hash=content_hash,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        body_path=str((folder / f"{content_hash}.txt").resolve()),
        extracted_chars=len(text),
        line_count=text.count("\n") + 1,
        truncated=truncated,
    )
    with _lock():
        _atomic_write(folder / f"{content_hash}.txt", text)
        _write_json(folder / f"{content_hash}.json", metadata)
        _write_json(folder / "metadata.json", metadata)
    return metadata, text


def load_source(ref: str, version: str | None = None) -> tuple[dict, str] | None:
    folder = _source_dir(ref)
    if version is not None and not re.fullmatch(r"[0-9a-f]{64}", version):
        raise ValueError("version must be a source content_hash")
    path = folder / (f"{version}.json" if version else "metadata.json")
    if not path.exists():
        return None
    metadata = json.loads(path.read_text(encoding="utf-8"))
    content_hash = metadata["content_hash"]
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        raise ValueError("invalid cached content hash")
    text = (folder / f"{content_hash}.txt").read_text(encoding="utf-8")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != content_hash:
        raise ValueError("cached source changed; use read --refresh to retrieve it again")
    return metadata, text


def _locator(text: str, start: int, end: int, metadata: dict) -> dict:
    return {
        "start_line": text.count("\n", 0, start) + 1,
        "end_line": text.count("\n", 0, max(start, end - 1)) + 1,
        "start_char": start,
        "end_char": end,
        "pages": [
            page["page"] for page in metadata.get("pages", [])
            if page["start_char"] < end and page["end_char"] > start
        ],
    }


def excerpts(
    metadata: dict, text: str, terms: list[str], window: int = 650,
    max_windows: int = 5, *, cache_hit: bool = True, intro_if_missing: bool = True,
) -> dict:
    # Round-robin across terms prevents an early frequent term hiding later ones.
    hits = [
        [match.start() for _, match in zip(range(max_windows), re.finditer(
            re.escape(term), text, re.IGNORECASE
        ))]
        for term in terms if term.strip()
    ]
    positions = []
    for rank in range(max_windows):
        for group in hits:
            if rank < len(group) and all(abs(group[rank] - old) > window for old in positions):
                positions.append(group[rank])
                if len(positions) == max_windows:
                    break
        if len(positions) == max_windows:
            break
    ranges = [(max(0, p - window), min(len(text), p + window)) for p in positions]
    if not ranges and intro_if_missing:
        ranges = [(0, min(len(text), window * 2))]
    return {
        **metadata,
        "cache_hit": cache_hit,
        "windows": [text[start:end] for start, end in ranges],
        "locators": [_locator(text, start, end, metadata) for start, end in ranges],
    }


def require_source(ref: str, version: str | None = None) -> tuple[dict, str]:
    source = load_source(ref, version)
    if source is None:
        raise ValueError("source snapshot not found in this run; read it first")
    return source


def open_source(ref: str, start_line: int = 1, count: int = 40, version: str | None = None) -> dict:
    metadata, text = require_source(ref, version)
    lines = text.split("\n")
    if start_line < 1 or start_line > len(lines) or not 1 <= count <= 120:
        raise ValueError("start-line must exist and count must be between 1 and 120")
    start = sum(len(line) + 1 for line in lines[:start_line - 1])
    selected = "\n".join(lines[start_line - 1:start_line - 1 + count])
    excerpt = selected[:12_000]
    return {
        **metadata, "cache_hit": True, "text": excerpt,
        "locator": _locator(text, start, start + len(excerpt), metadata),
        "excerpt_truncated": len(selected) > len(excerpt),
    }


def _evidence() -> dict:
    path = root() / "evidence.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _evidence_indexes(records: list[dict]) -> tuple[dict, dict]:
    contents, claims = {}, {}
    for record in records:
        contents.setdefault(record["content_hash"], set()).add(record["ref"])
        key = (record["need"], record["claim"])
        claims.setdefault(key, Counter())[record["relation"]] += 1
    return contents, claims


def review_evidence(
    *, need: str | None = None, claim: str | None = None, offset: int = 0, limit: int = 5,
) -> dict:
    if offset < 0 or not 1 <= limit <= 10:
        raise ValueError("offset must be nonnegative and limit must be between 1 and 10")
    if any(value is not None and not value.strip() for value in (need, claim)):
        raise ValueError("need and claim filters must be non-empty when supplied")
    with _lock():
        records = list(_evidence().values())
    contents, claims = _evidence_indexes(records)
    selected = [
        record for record in records
        if (need is None or record["need"] == need)
        and (claim is None or record["claim"] == claim)
    ]
    cards = []
    for record in selected[offset:offset + limit]:
        refs = sorted(contents[record["content_hash"]])
        relations = claims[(record["need"], record["claim"])]
        cards.append({
            **record,
            "same_text_ref_count": len(refs), "same_text_refs": refs[:10],
            "same_text_refs_truncated": len(refs) > 10,
            "claim_relations": {key: relations[key] for key in ("supports", "refutes", "context")},
            "has_mixed_relations": bool(relations["supports"] and relations["refutes"]),
        })
    counts = Counter(record["relation"] for record in selected)
    return {
        "workspace": str(root().resolve()), "filters": {"need": need, "claim": claim},
        "total": len(selected), "offset": offset,
        "next_offset": offset + len(cards) if offset + len(cards) < len(selected) else None,
        "source_ref_count": len({record["ref"] for record in selected}),
        "distinct_content_count": len({record["content_hash"] for record in selected}),
        "relation_counts": {key: counts[key] for key in ("supports", "refutes", "context")},
        "evidence": cards,
        "review_note": (
            "Equal hashes identify identical saved text, including partial or truncated text. "
            "Different text does not establish source independence. Relations are caller assessments; "
            "mixed relations for the same Need and exact claim require scope review, not automatic adjudication."
        ),
    }


def _task() -> dict | None:
    path = root() / "task.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _require_task(*, active: bool = False) -> dict:
    task = _task()
    if task is None:
        raise ValueError("initialize this task with init first")
    if active and task["state"] != "active":
        raise ValueError("task is stopped; use resume before changing progress")
    return task


def _save_task(task: dict) -> dict:
    task["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(root() / "task.json", task)
    return task


def init_task(deliverable: str, target: str, constraints: list[str] | None = None) -> dict:
    contract = {
        "deliverable": deliverable.strip(), "target": target.strip(),
        "constraints": [item.strip() for item in (constraints or [])],
    }
    if not contract["deliverable"] or not contract["target"] or any(
        not item for item in contract["constraints"]
    ):
        raise ValueError("deliverable, target and any constraints must be non-empty")
    with _lock():
        task = _task()
        if task is not None:
            if any(task[key] != value for key, value in contract.items()):
                raise ValueError("task contract differs; use a new DEEP_RESEARCH_RUN_DIR")
            return task
        return _save_task({
            "schema_version": 1, **contract, "state": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "needs": {}, "failures": [], "last_stop": None,
        })


def set_need(
    need: str, *, question: str | None = None, proof: str | None = None,
    priority: int | None = None, state: str | None = None, note: str | None = None,
    evidence_ids: list[str] | None = None,
) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", need):
        raise ValueError("Need ID must be 1-64 letters, digits, dots, hyphens or underscores")
    if priority is not None and not 1 <= priority <= 5:
        raise ValueError("priority must be between 1 (highest) and 5")
    if state is not None and state not in {"open", "partial", "blocked", "answered"}:
        raise ValueError("Need state must be open, partial, blocked or answered")
    if any(value is not None and not value.strip() for value in (question, proof, note)):
        raise ValueError("question, proof and note must be non-empty when supplied")
    if state is not None and note is None:
        raise ValueError("a state update requires a note explaining the answer or remaining gap")
    with _lock():
        task = _require_task(active=True)
        previous = task["needs"].get(need)
        if previous is None:
            if question is None or proof is None:
                raise ValueError("a new Need requires question and proof")
            record = {
                "question": question.strip(), "proof": proof.strip(), "priority": 3,
                "state": "open", "note": "", "evidence_ids": [],
            }
        else:
            record = dict(previous)
            for key, value in (("question", question), ("proof", proof)):
                if value is not None and value.strip() != record[key]:
                    raise ValueError("existing Need contract differs; register a separate Need ID")
        for key, value in (("priority", priority), ("state", state), ("note", note)):
            if value is not None:
                record[key] = value.strip() if isinstance(value, str) else value
        if evidence_ids is not None:
            record["evidence_ids"] = list(dict.fromkeys(evidence_ids))
        evidence = _evidence()
        selected = []
        for evidence_id in record["evidence_ids"]:
            card = evidence.get(evidence_id)
            if card is None or card["need"] != need:
                raise ValueError("each evidence ID must be a saved card for this Need")
            selected.append(card)
        if record["state"] == "answered" and (
            not record["note"] or not any(
                card["relation"] in {"supports", "refutes"} for card in selected
            )
        ):
            raise ValueError("answered requires a conclusion note and supports/refutes evidence")
        if previous == record:
            return {"id": need, **record}
        task["needs"][need] = record
        _save_task(task)
        return {"id": need, **record}


def record_failure(need: str, action: str, reason: str) -> dict:
    if not action.strip() or not reason.strip():
        raise ValueError("action and reason must be non-empty")
    with _lock():
        task = _require_task(active=True)
        if need not in task["needs"]:
            raise ValueError("register the Need before recording a failed attempt")
        record = {
            "need": need, "action": action.strip(), "reason": reason.strip(),
            "at": datetime.now(timezone.utc).isoformat(),
        }
        task["failures"].append(record)
        _save_task(task)
        return record


def stop_task(reason: str, *, complete: bool = False) -> dict:
    if not reason.strip():
        raise ValueError("a stop reason is required")
    with _lock():
        task = _require_task(active=True)
        if complete and (not task["needs"] or any(
            need["state"] != "answered" for need in task["needs"].values()
        )):
            raise ValueError("complete requires every registered checklist Need to be answered")
        task["state"] = "complete" if complete else "incomplete"
        task["last_stop"] = {
            "state": task["state"], "reason": reason.strip(),
            "at": datetime.now(timezone.utc).isoformat(),
        }
        return _save_task(task)


def resume_task() -> dict:
    with _lock():
        task = _require_task()
        if task["state"] == "active":
            return task
        task["state"] = "active"
        return _save_task(task)


def require_need(need: str) -> dict:
    with _lock():
        task = _require_task(active=True)
        if need not in task["needs"]:
            raise ValueError("register the Need before searching for it")
        return {"id": need, **task["needs"][need]}


def search_id(request: dict) -> str:
    return hashlib.sha256(json.dumps(
        request, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")).hexdigest()


def load_search(request: dict) -> dict | None:
    path = root() / "searches" / f"{search_id(request)}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_search(request: dict, result: dict) -> dict:
    record = {
        "id": search_id(request), "request": request, "result": result,
        "searched_at": datetime.now(timezone.utc).isoformat(),
    }
    with _lock():
        _write_json(root() / "searches" / f"{record['id']}.json", record)
    return record


def record_search_attempt(
    need: str, query: str, key: str, *, cache_hit: bool = False,
    new_ref_count: int = 0, error: str | None = None,
) -> dict:
    # An in-flight request may finish after stop; retain its outcome without
    # reopening the task or changing any Need state.
    with _lock():
        task = _require_task()
        if need not in task["needs"]:
            raise ValueError("register the Need before recording its search")
        record = {
            "need": need, "query": query, "search_id": key,
            "cache_hit": cache_hit, "new_ref_count": new_ref_count,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        if error is not None:
            record["error"] = error
            task["failures"].append({
                "need": need, "action": f"search {query}", "reason": error, "at": record["at"],
            })
        task.setdefault("searches", []).append(record)
        _save_task(task)
        return record


def record_evidence(
    ref: str, need: str, claim: str, quote: str, relation: str,
    *, start_line: int = 1, version: str | None = None,
) -> dict:
    if not all(value.strip() for value in (need, claim, quote)):
        raise ValueError("need, claim, and quote must be non-empty")
    if relation not in {"supports", "refutes", "context"}:
        raise ValueError("relation must be supports, refutes, or context")
    if len(quote) > 4_000:
        raise ValueError("quote exceeds 4000 characters; select a smaller passage")
    metadata, text = require_source(ref, version)
    lines = text.split("\n")
    if not 1 <= start_line <= len(lines):
        raise ValueError("start-line does not exist")
    offset = sum(len(line) + 1 for line in lines[:start_line - 1])
    start = text.find(quote, offset)
    if start < 0:
        raise ValueError("quote is not present verbatim in the saved source at/after start-line")
    record = {
        "ref": ref, "need": need, "claim": claim, "quote": quote,
        "relation": relation, "relation_assessed_by": "caller",
        "quote_verified": True, "content_scope": metadata["content_scope"],
        "source_truncated": metadata["truncated"],
        "content_hash": metadata["content_hash"],
        "source": metadata.get("url"),
        "locator": _locator(text, start, start + len(quote), metadata),
    }
    record["id"] = "EV-" + hashlib.sha256(
        json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16].upper()
    with _lock():
        task = _task()
        if task is not None and need not in task["needs"]:
            raise ValueError("register the Need before recording evidence in an initialized task")
        records = _evidence()
        records[record["id"]] = record
        _write_json(root() / "evidence.json", records)
    return record


def status() -> dict:
    sources = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(
        (root() / "sources").glob("*/metadata.json")
    )]
    with _lock():
        task = _task()
        records = list(_evidence().values())
    by_need = {}
    content_by_need = {}
    for record in records:
        counts = by_need.setdefault(record["need"], {"supports": 0, "refutes": 0, "context": 0})
        counts[record["relation"]] += 1
        content_by_need.setdefault(record["need"], {
            "supports": set(), "refutes": set(), "context": set(),
        })[record["relation"]].add(record["content_hash"])
    contents, claims = _evidence_indexes(records)
    summary = {
        "workspace": str(root().resolve()), "sources_read": len(sources),
        "source_scopes": dict(Counter(source["content_scope"] for source in sources)),
        "evidence_count": len(records), "evidence_by_need": by_need,
        "evidence_content_counts_by_need": {
            need: {relation: len(hashes) for relation, hashes in counts.items()}
            for need, counts in content_by_need.items()
        },
        "evidence_distinct_contents": len(contents),
        "duplicate_text_groups": sum(len(refs) > 1 for refs in contents.values()),
        "mixed_relation_claims": sum(bool(counts["supports"] and counts["refutes"])
                                     for counts in claims.values()),
        "review_note": "Content counts do not establish independence; use review to inspect saved evidence and scope.",
        "next_action": "Review remaining Needs and conflicts; recorded relations are caller assessments.",
    }
    summary.update(task=None, remaining_needs=[], next_need=None, completion_ready=False)
    if task is None:
        return summary
    summary["task"] = {
        **{key: value for key, value in task.items() if key not in {"failures", "searches"}},
        "failure_count": len(task["failures"]), "recent_failures": task["failures"][-10:],
        "search_count": len(task.get("searches", [])),
        "recent_searches": task.get("searches", [])[-10:],
    }
    remaining = sorted(
        (key for key, need in task["needs"].items() if need["state"] != "answered"),
        key=lambda key: task["needs"][key]["priority"],
    )
    next_need = next((key for key in remaining if task["needs"][key]["state"] != "blocked"), None)
    summary.update(
        remaining_needs=remaining, next_need=next_need,
        completion_ready=bool(task["needs"]) and not remaining,
        unregistered_evidence_needs=sorted(set(by_need) - set(task["needs"])),
    )
    if task["state"] != "active":
        summary["next_action"] = "Task is stopped; review last_stop and remaining_needs before resume."
    elif next_need:
        summary["next_action"] = f"Continue {next_need}; use its proof and gap note to choose the next action."
    elif remaining:
        summary["next_action"] = "All remaining Needs are blocked; review gaps or stop with an incomplete reason."
    elif not task["needs"]:
        summary["next_action"] = "Register every required checklist item as a Need before research."
    else:
        summary["next_action"] = "Review the full user checklist and conflicts before stop --complete."
    return summary
