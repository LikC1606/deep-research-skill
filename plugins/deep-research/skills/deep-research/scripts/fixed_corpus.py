"""Bounded offline FTS5 retrieval and measured researcher tool traces."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def load_case(case_path):
    path = Path(case_path).resolve()
    with path.open("rb") as stream:
        raw = stream.read(600_001)
    if len(raw) > 600_000:
        raise ValueError("corpus manifest exceeds 600000 bytes")
    case = json.loads(raw)
    if (set(case) - {"id", "question", "budget", "sources"} or not case.get("sources")
            or not case.get("id") or not case.get("question")):
        raise ValueError("use case-pack researcher inputs, without evaluator/checklist data")
    budget = case.get("budget", {})
    if any(type(budget.get(key)) is not int or not 0 <= budget[key] <= 100
           for key in ("search_calls", "source_reads")) or budget.get("network_calls") != 0:
        raise ValueError("fixed corpus requires network_calls=0 and explicit search_calls/source_reads budgets (0-100)")
    sources, size = {}, 0
    for item in case["sources"]:
        source_path = (path.parent / item["path"]).resolve()
        if not source_path.is_relative_to(path.parent):
            raise ValueError("corpus source must stay inside the researcher directory")
        with source_path.open("rb") as stream:
            raw = stream.read(600_001)
        size += len(raw)
        if size > 500_000:
            raise ValueError("combined fixed corpus exceeds 500000 bytes")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != item.get("hash") or item["id"] in sources:
            raise ValueError("corpus source hash mismatch or duplicate ID")
        if not raw.strip():
            raise ValueError("corpus sources must have readable text")
        sources[item["id"]] = {"path": str(source_path), "hash": digest, "text": raw.decode("utf-8")}
    identity = {"case_id": case["id"], "question_hash": hashlib.sha256(case["question"].encode()).hexdigest(),
                "corpus_hash": _hash({key: value["hash"] for key, value in sources.items()}), "budget": budget}
    return identity, sources


def _begin(ws, identity, kind, detail):
    path = ws.root() / "corpus-trace.json"
    with ws._lock():
        trace = json.loads(path.read_text()) if path.exists() else {
            "schema_version": 1, **identity, "operations": [], "scope": "fixed_corpus_tools_only"}
        if any(trace.get(key) != value for key, value in identity.items()):
            raise ValueError("use a separate run directory for a different corpus/task/budget")
        key = "search_calls" if kind == "search" else "source_reads"
        if sum(item["kind"] == kind for item in trace["operations"]) >= identity["budget"][key]:
            raise RuntimeError(f"fixed corpus {key} budget exhausted")
        index = len(trace["operations"])
        trace["operations"].append({"kind": kind, **detail, "state": "pending",
                                    "at": datetime.now(timezone.utc).isoformat()})
        ws._write_json(path, trace)
    return index, time.monotonic()


def _finish(ws, index, started, result=None, error=None):
    path = ws.root() / "corpus-trace.json"
    with ws._lock():
        trace = json.loads(path.read_text())
        trace["operations"][index].update(state="error" if error else "completed",
                                            elapsed_seconds=time.monotonic() - started, **(result or {}))
        if error:
            trace["operations"][index]["error"] = str(error)[:400]
        ws._write_json(path, trace)


def _tokens(text):
    # Space Han characters for FTS5's standard unicode61 tokenizer.
    return re.findall(r"[^\W_]+", re.sub(r"([\u3400-\u9fff])", r" \1 ", text), re.UNICODE)


def search(ws, case_path, query, limit):
    if not query.strip() or len(query) > 2000:
        raise ValueError("corpus query must contain 1-2000 characters")
    identity, sources = load_case(case_path)
    index, started = _begin(ws, identity, "search", {"query": query})
    try:
        terms = list(dict.fromkeys(_tokens(query)))[:100]
        rows = []
        with sqlite3.connect(":memory:") as database:
            database.execute("CREATE VIRTUAL TABLE docs USING fts5(source_id UNINDEXED, body)")
            database.executemany("INSERT INTO docs VALUES (?, ?)",
                                 ((key, " ".join(_tokens(value["text"]))) for key, value in sources.items()))
            if terms:
                match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
                rows = database.execute("SELECT source_id, snippet(docs, 1, '', '', '...', 24) "
                                        "FROM docs WHERE docs MATCH ? ORDER BY rank, rowid LIMIT ?",
                                        (match, max(1, min(limit, 12)))).fetchall()
        _finish(ws, index, started, {"source_ids": [key for key, _ in rows]})
        return {"results": [{"source_id": key, "url": sources[key]["path"],
                              "title": sources[key]["text"].splitlines()[0][:200], "snippet": snippet[:1200]}
                             for key, snippet in rows], "providers_used": ["local_fts5"], **identity}
    except Exception as exc:
        _finish(ws, index, started, error=exc)
        raise


def read(ws, case_path, source, reader):
    identity, sources = load_case(case_path)
    selected = next((key for key, value in sources.items()
                     if source == key or source == value["path"]), None)
    if selected is None:
        raise ValueError("corpus read requires a source ID or exact path from this researcher manifest")
    index, started = _begin(ws, identity, "read", {"source_id": selected})
    try:
        result = reader(sources[selected]["path"])
        _finish(ws, index, started, {"source_id": selected, "snapshot_id": result["snapshot_id"], "ref": result["ref"]})
        return {**result, "corpus_source_id": selected, "corpus_hash": identity["corpus_hash"]}
    except Exception as exc:
        _finish(ws, index, started, error=exc)
        raise
