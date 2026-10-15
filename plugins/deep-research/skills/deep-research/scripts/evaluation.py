"""Prepare blind, reproducible report comparisons and validate reviewer judgments.

No model API, substring grading, or self-assigned quality scores are used here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
from types import SimpleNamespace


DIMENSIONS = ("completeness", "citation_support", "source_quality", "synthesis")
RUBRIC = {
    "completeness": "Cover every checklist item; disclose missing facts without inventing values.",
    "citation_support": "Check actual passages, units, dates and scope; a reachable citation is insufficient.",
    "source_quality": "Use direct sources; do not count mirrors as independent or abstracts as full experiments.",
    "synthesis": "Explain conflicts, mechanisms and actionable limits instead of listing source summaries.",
}


def _read_json(path: Path):
    with path.open("rb") as stream:
        raw = stream.read(600_001)
    if len(raw) > 600_000:
        raise ValueError("evaluation JSON exceeds 600000 bytes")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_hash(value) -> str:
    return _digest(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


def _case_material(case_path: str):
    path = Path(case_path).resolve()
    case = _read_json(path)
    if not case.get("id") or not case.get("question") or not case.get("checklist") or not case.get("sources"):
        raise ValueError("case requires id, question, nonempty checklist and sources")
    if any(not item.get("id") or not item.get("requirement") for item in case["checklist"]):
        raise ValueError("checklist items require id and requirement")
    ids = [item["id"] for item in case["checklist"]]
    if len(set(ids)) != len(ids):
        raise ValueError("checklist IDs must be unique")
    if len({item["id"] for item in case["sources"]}) != len(case["sources"]):
        raise ValueError("source IDs must be unique")
    corpus = []
    for source in case["sources"]:
        if (not isinstance(source["id"], str) or not 1 <= len(source["id"]) <= 64
                or not source["id"].replace("-", "").replace("_", "").isalnum()):
            raise ValueError("source IDs must be safe filenames")
        source_path = (path.parent / source["path"]).resolve()
        if case.get("evaluator") and source_path == (path.parent / case["evaluator"]).resolve():
            raise ValueError("evaluator reference cannot be a researcher source")
        with source_path.open("rb") as stream:
            raw = stream.read(600_001)
        if len(raw) > 600_000:
            raise ValueError("evaluation source exceeds 600000 bytes")
        corpus.append({"id": source["id"], "path": source["path"], "hash": _digest(raw),
                       "text": raw.decode("utf-8")})
    criteria = []
    if case.get("evaluator"):
        criteria = _read_json(path.parent / case["evaluator"]).get("criteria")
        if not isinstance(criteria, list) or not criteria:
            raise ValueError("evaluator requires nonempty criteria")
        seen, source_ids = set(), {source["id"] for source in corpus}
        for item in criteria:
            if (not item.get("id") or item["id"] in seen or not item.get("requirement")
                    or not item.get("reference") or type(item.get("weight")) is not int
                    or item["weight"] <= 0 or not item.get("source_ids")
                    or not set(item["source_ids"]) <= source_ids):
                raise ValueError("criteria require unique IDs, requirement, reference, positive weight and valid source_ids")
            seen.add(item["id"])
            groups = item.get("evidence_groups", [])
            if not isinstance(groups, list) or any(not isinstance(group, list) or not group or
                    len(set(group)) != len(group) or not set(group) <= source_ids for group in groups):
                raise ValueError("evidence_groups must contain nonempty groups of known source IDs")
    return path, case, corpus, criteria


def pack_case(ws, case_path: str, output: str) -> dict:
    """Export only allowlisted researcher inputs; evaluator data stays outside."""
    _, case, corpus, _ = _case_material(case_path)
    public = {key: case[key] for key in ("id", "question", "budget") if key in case}
    public["sources"] = [{"id": item["id"], "path": f"corpus/{item['id']}.txt", "hash": item["hash"]}
                         for item in corpus]
    destination = Path(output).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    for item in corpus:
        ws._atomic_write(destination / "corpus" / f"{item['id']}.txt", item["text"])
    ws._write_json(destination / "case.json", public)
    return {"researcher_dir": str(destination), "case_id": case["id"],
            "note": "Expose only this directory to the researcher. References/checklist/rubrics remain evaluator-only; "
                    "an export is not an OS sandbox or a held-out dataset."}


def prepare(ws, reporting, case_path: str, a_path: str, b_path: str, output: str, *,
            seed: int = 0, a_run: str | None = None, b_run: str | None = None, semantic_review=None) -> dict:
    path, case, corpus, criteria = _case_material(case_path)
    ids = [item["id"] for item in case["checklist"]]
    order = [("a", a_path, a_run), ("b", b_path, b_run)]
    random.Random(seed).shuffle(order)
    reports, key = {}, {}
    for label, (original, report_path, run) in zip(("A", "B"), order):
        text = reporting.read_report(report_path)
        reports[label] = {"text": text, "report_hash": _digest(text.encode())}
        measurements = None
        trace, reviewed = None, None
        if run is not None:
            measured = Path(run) / "measurements.json"
            counts = Path(run) / ".adapter-counts.json"
            if measured.exists():
                measurements = {"provenance": "run_measurements", "values": _read_json(measured)}
            elif counts.exists():
                measurements = {"provenance": "adapter_counts", "values": _read_json(counts)}
            trace_path = Path(run) / ".deep-research" / "corpus-trace.json"
            if trace_path.exists():
                trace = _read_json(trace_path)
                if (trace.get("case_id") != case["id"] or trace.get("budget") != case.get("budget")
                        or trace.get("question_hash") != _digest(case["question"].encode())
                        or trace.get("corpus_hash") != _json_hash({item["id"]: item["hash"] for item in corpus})):
                    raise ValueError("run trace does not match the case, corpus, question or budget")
                if measurements is None:
                    measurements = {"provenance": "fixed_corpus_tool_trace", "values": {
                        "search_calls": sum(item["kind"] == "search" for item in trace["operations"]),
                        "source_reads": sum(item["kind"] == "read" for item in trace["operations"]),
                        "elapsed_tool_seconds": sum(item.get("elapsed_seconds", 0) for item in trace["operations"]),
                        "scope": "fixed_corpus_tools_only", "cost_usd": None}}
            if semantic_review is not None:
                run_root = Path(run) / ".deep-research"
                run_workspace = SimpleNamespace(root=lambda: run_root,
                    require_source=lambda ref, version: ws.require_source(ref, version, run_root=run_root))
                record = semantic_review.load_review(run_workspace, reports[label]["report_hash"])
                if record is not None:
                    summary, claims = semantic_review.metrics(run_workspace, record)
                    reviewed = {"review_hash": record["review_hash"], "reviewer": record["reviewer"], "metrics": summary,
                                "claims": [{"id": claim["id"], "kind": claim["kind"], "verdict": claim["effective_verdict"],
                                            "bindings": [{name: binding[name] for name in (
                                                "evidence_id", "implicit", "confirmed", "contribution", "snapshot_issue")} |
                                                {"ref": (binding["evidence"] or {}).get("ref"),
                                                 "snapshot_id": (binding["evidence"] or {}).get("snapshot_id")}
                                                for binding in claim["bindings"]]} for claim in claims]}
        key[label] = {"input": original, "report_path": str(Path(report_path).resolve()),
                      "run": run, "measurements": measurements, "corpus_trace": trace, "claim_review": reviewed}
    comparison = {"schema_version": 1, "case_id": case["id"], "case_hash": _digest(path.read_bytes()),
                  "question": case["question"], "checklist": case["checklist"], "corpus": corpus,
                  "budget": case.get("budget"), "rubric": RUBRIC, "reports": reports,
                  "task_rubric": criteria,
                  "scoring": "0=absent/incorrect, 1=major gaps, 2=partial, 3=mostly meets, 4=fully meets"}
    packet_hash = _json_hash(comparison)
    key_packet = {"seed": seed, "labels": key, "packet_hash": packet_hash}
    if max(len(json.dumps(value, ensure_ascii=False, indent=2).encode()) for value in (comparison, key_packet)) >= 600_000:
        raise ValueError("combined comparison exceeds the evaluation byte budget")
    destination = Path(output).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    ws._write_json(destination / "blind.json", comparison)
    ws._write_json(destination / "key.json", key_packet)
    template = {"case_hash": comparison["case_hash"], "packet_hash": packet_hash, "reviewer": "",
                "reports": {label: {"report_hash": value["report_hash"],
                                    "scores": {dim: None for dim in DIMENSIONS},
                                    "reasons": {dim: "" for dim in DIMENSIONS},
                                    "checklist": {item: {"status": "", "reason": ""} for item in ids},
                                    "criteria": {item["id"]: {"status": "", "reason": "", "report_quote": "",
                                                              "source_ids": []} for item in criteria}}
                            for label, value in reports.items()},
                "preferred": "", "reason": ""}
    ws._write_json(destination / "judgments.template.json", template)
    return {"comparison_dir": str(destination), "blind_packet": str(destination / "blind.json"),
            "judgment_template": str(destination / "judgments.template.json"),
            "note": "Give only blind.json and the template to the reviewer; key.json reveals report identities. "
                    "Missing cost measurements remain unknown; this tool does not judge report quality."}


def score(ws, directory: str, judgments_path: str) -> dict:
    directory = Path(directory)
    comparison = _read_json(directory / "blind.json")
    key = _read_json(directory / "key.json")
    judgments = _read_json(Path(judgments_path))
    if key.get("packet_hash") and (key["packet_hash"] != _json_hash(comparison)
                                   or judgments.get("packet_hash") != key["packet_hash"]):
        raise ValueError("comparison packet hash does not match")
    if any(_digest(value["text"].encode()) != value["report_hash"] for value in comparison["reports"].values()):
        raise ValueError("blinded report text does not match its hash")
    if any(_digest(source["text"].encode()) != source["hash"] for source in comparison["corpus"]):
        raise ValueError("blinded corpus text does not match its hash")
    if judgments.get("case_hash") != comparison["case_hash"]:
        raise ValueError("judgments do not match this comparison's case hash")
    if not isinstance(judgments.get("reviewer"), str) or not judgments["reviewer"].strip():
        raise ValueError("identify the reviewer")
    rows = {}
    if set(judgments.get("reports", {})) != {"A", "B"}:
        raise ValueError("judgments require exactly reports A and B")
    for label in ("A", "B"):
        rating = judgments.get("reports", {}).get(label, {})
        if rating.get("report_hash") != comparison["reports"][label]["report_hash"]:
            raise ValueError("judgments do not match the blinded report hash")
        for dim in DIMENSIONS:
            value = rating.get("scores", {}).get(dim)
            reason = rating.get("reasons", {}).get(dim)
            if type(value) is not int or not 0 <= value <= 4 or not isinstance(reason, str) or not reason.strip():
                raise ValueError("each dimension requires an integer score 0-4 and an evidence-based reason")
        checklist = rating.get("checklist", {})
        if set(checklist) != {item["id"] for item in comparison["checklist"]}:
            raise ValueError("checklist judgments must match exactly")
        for item in comparison["checklist"]:
            judgment = checklist.get(item["id"], {})
            reason = judgment.get("reason")
            if judgment.get("status") not in {"direct", "inference", "missing", "incorrect"} or not isinstance(reason, str) or not reason.strip():
                raise ValueError("every checklist item requires direct/inference/missing/incorrect and a reason")
        criteria = rating.get("criteria", {})
        if set(criteria) != {item["id"] for item in comparison.get("task_rubric", [])}:
            raise ValueError("criterion judgments must match exactly")
        assessed_weight, earned, total_weight = 0, 0, 0
        for item in comparison.get("task_rubric", []):
            judgment = criteria[item["id"]]
            status, reason = judgment.get("status"), judgment.get("reason")
            quote, sources = judgment.get("report_quote"), judgment.get("source_ids")
            if (status not in {"met", "partial", "unmet", "not_assessable"}
                    or not isinstance(reason, str) or not reason.strip()
                    or not isinstance(quote, str) or (quote and quote not in comparison["reports"][label]["text"])
                    or not isinstance(sources, list) or len(set(sources)) != len(sources)
                    or not set(sources) <= {source["id"] for source in comparison["corpus"]}):
                raise ValueError("criterion requires status, reason, exact report_quote and valid source_ids")
            if status in {"met", "partial"} and (not quote.strip() or not sources):
                raise ValueError("met/partial criterion requires report_quote and source_ids")
            total_weight += item["weight"]
            if status != "not_assessable":
                assessed_weight += item["weight"]
                earned += item["weight"] * {"met": 1, "partial": .5, "unmet": 0}[status]
        identity = key["labels"][label]
        rows[label] = {**{name: value for name, value in identity.items() if name not in {"corpus_trace", "claim_review"}},
                       "trace_hash": _json_hash(identity["corpus_trace"]) if identity.get("corpus_trace") else None,
                       "scores": rating["scores"], "reasons": rating["reasons"],
                       "checklist": checklist,
                       "criteria": criteria,
                       "rubric_summary": {"total_weight": total_weight, "assessed_weight": assessed_weight,
                                          "score_on_assessed": earned / assessed_weight if assessed_weight else None},
                       "diagnostics": _diagnose(identity.get("corpus_trace"), comparison, criteria, identity.get("claim_review")),
                       "direct_coverage": sum(checklist[item["id"]]["status"] == "direct" for item in comparison["checklist"]) / len(comparison["checklist"])}
    if judgments.get("preferred") not in {"A", "B", "tie"} or not isinstance(judgments.get("reason"), str) or not judgments["reason"].strip():
        raise ValueError("preferred must be A/B/tie, with a reason")
    result = {"case_id": comparison["case_id"], "case_hash": comparison["case_hash"],
              "reviewer": judgments["reviewer"], "reports": rows, "preferred": judgments["preferred"],
              "preferred_input": key["labels"][judgments["preferred"]]["input"] if judgments["preferred"] != "tie" else "tie",
              "reason": judgments["reason"], "assessment": "external_reviewer",
              "comparability": _comparability(key["labels"]),
              "limits": "Scores reflect the named reviewer's judgments on this fixed corpus, not general benchmark performance."}
    ws._write_json(directory / "scores.json", result)
    return result


def _diagnose(trace, comparison, judgments, semantic=None):
    if trace is None:
        return {"available": False, "reason": "No recorded fixed-corpus tool trace; retrieval/read stages are unknown."}
    retrieved, read, snapshots = set(), set(), {}
    for operation in trace["operations"]:
        if operation.get("state") != "completed":
            continue
        if operation["kind"] == "search":
            retrieved.update(operation["source_ids"])
        elif operation["kind"] == "read":
            read.add(operation["source_id"])
            snapshots[(operation.get("ref"), operation.get("snapshot_id"))] = operation["source_id"]
    criteria, cited = {}, set()
    for item in comparison.get("task_rubric", []):
        judgment = judgments[item["id"]]
        sources = set(judgment["source_ids"])
        cited.update(sources)
        groups = item.get("evidence_groups")
        covers = lambda ids: all(set(group) & ids for group in groups)
        if not groups or judgment["status"] == "not_assessable":
            stage = "unknown"
        elif judgment["status"] == "met":
            stage = "meets_rubric" if covers(read) else "review_trace_mismatch"
        elif not covers(read):
            stage = "reading_gap" if covers(retrieved | read) else "retrieval_gap"
        else:
            stage = "citation_gap" if not covers(sources) else "evidence_use_gap"
        criteria[item["id"]] = {"stage": stage, "use_judgment": judgment["status"],
                                "cited_sources_assessed_by_reviewer": sorted(sources)}
    correctly_used, reviewed_citations = set(), set()
    if semantic is not None:
        for claim in semantic["claims"]:
            for binding in claim["bindings"]:
                source_id = snapshots.get((binding["ref"], binding["snapshot_id"]))
                if source_id is None or not binding["confirmed"]:
                    continue
                reviewed_citations.add(source_id)
                if (claim["kind"] in {"factual", "inference"} and claim["verdict"] == "supported"
                        and not binding["snapshot_issue"] and binding["contribution"] in {"necessary", "helpful"}):
                    correctly_used.add(source_id)
    return {"available": True, "retrieved_source_ids": sorted(retrieved), "read_source_ids": sorted(read),
            "direct_read_source_ids": sorted(read - retrieved), "cited_source_ids": sorted(cited),
            "claim_review_cited_source_ids": sorted(reviewed_citations) if semantic else None,
            "correctly_used_source_ids": sorted(correctly_used) if semantic else None,
            "claim_review": {name: semantic[name] for name in ("review_hash", "reviewer", "metrics")} if semantic else None,
            "cited_without_recorded_read": sorted(cited - read), "criteria": criteria,
            "limits": "Stages are diagnostics against declared evidence groups, not proof of a model failure. "
                      "Correct use requires an explicit supported claim with a confirmed contributing citation at the exact recorded snapshot. "
                      "Without claim review it remains unknown; classifications remain reviewer assessments. "
                      "Trace covers fixed-corpus tools, not unrecorded filesystem reads or other tools."}


def _comparability(labels):
    traces = [labels[label].get("corpus_trace") for label in ("A", "B")]
    models = [(labels[label].get("measurements") or {}).get("values", {}).get("model") for label in ("A", "B")]
    return {"same_corpus": traces[0]["corpus_hash"] == traces[1]["corpus_hash"] if all(traces) else None,
            "same_budget": traces[0]["budget"] == traces[1]["budget"] if all(traces) else None,
            "same_model": models[0] == models[1] if all(models) else None,
            "model_ids": models, "note": "Unknown stays null. Equal caps do not prove equal use or general quality gains."}
