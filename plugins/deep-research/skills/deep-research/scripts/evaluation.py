"""Prepare blind, reproducible report comparisons and validate reviewer judgments.

No model API, substring grading, or self-assigned quality scores are used here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random


DIMENSIONS = ("completeness", "citation_support", "source_quality", "synthesis")
RUBRIC = {
    "completeness": "Cover every checklist item; disclose missing facts without inventing values.",
    "citation_support": "Check actual passages, units, dates and scope; a reachable citation is insufficient.",
    "source_quality": "Use direct sources; do not count mirrors as independent or abstracts as full experiments.",
    "synthesis": "Explain conflicts, mechanisms and actionable limits instead of listing source summaries.",
}


def _read_json(path: Path):
    raw = path.read_bytes()
    if len(raw) > 600_000:
        raise ValueError("evaluation JSON exceeds 600000 bytes")
    return json.loads(raw)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def prepare(ws, reporting, case_path: str, a_path: str, b_path: str, output: str, *,
            seed: int = 0, a_run: str | None = None, b_run: str | None = None) -> dict:
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
        source_path = (path.parent / source["path"]).resolve()
        raw = source_path.read_bytes()
        if len(raw) > 600_000:
            raise ValueError("evaluation source exceeds 600000 bytes")
        corpus.append({"id": source["id"], "path": source["path"], "hash": _digest(raw),
                       "text": raw.decode("utf-8")})
    order = [("a", a_path, a_run), ("b", b_path, b_run)]
    random.Random(seed).shuffle(order)
    reports, key = {}, {}
    for label, (original, report_path, run) in zip(("A", "B"), order):
        text = reporting.read_report(report_path)
        reports[label] = {"text": text, "report_hash": _digest(text.encode())}
        measurements = None
        if run is not None:
            measured = Path(run) / "measurements.json"
            counts = Path(run) / ".adapter-counts.json"
            if measured.exists():
                measurements = {"provenance": "run_measurements", "values": _read_json(measured)}
            elif counts.exists():
                measurements = {"provenance": "adapter_counts", "values": _read_json(counts)}
        key[label] = {"input": original, "report_path": str(Path(report_path).resolve()),
                      "run": run, "measurements": measurements}
    comparison = {"schema_version": 1, "case_id": case["id"], "case_hash": _digest(path.read_bytes()),
                  "question": case["question"], "checklist": case["checklist"], "corpus": corpus,
                  "budget": case.get("budget"), "rubric": RUBRIC, "reports": reports,
                  "scoring": "0=absent/incorrect, 1=major gaps, 2=partial, 3=mostly meets, 4=fully meets"}
    destination = Path(output).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    ws._write_json(destination / "blind.json", comparison)
    ws._write_json(destination / "key.json", {"seed": seed, "labels": key})
    template = {"case_hash": comparison["case_hash"], "reviewer": "",
                "reports": {label: {"report_hash": value["report_hash"],
                                    "scores": {dim: None for dim in DIMENSIONS},
                                    "reasons": {dim: "" for dim in DIMENSIONS},
                                    "checklist": {item: {"status": "", "reason": ""} for item in ids}}
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
    if any(_digest(value["text"].encode()) != value["report_hash"] for value in comparison["reports"].values()):
        raise ValueError("blinded report text does not match its hash")
    if any(_digest(source["text"].encode()) != source["hash"] for source in comparison["corpus"]):
        raise ValueError("blinded corpus text does not match its hash")
    if judgments.get("case_hash") != comparison["case_hash"]:
        raise ValueError("judgments do not match this comparison's case hash")
    if not isinstance(judgments.get("reviewer"), str) or not judgments["reviewer"].strip():
        raise ValueError("identify the reviewer")
    rows = {}
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
        for item in comparison["checklist"]:
            judgment = checklist.get(item["id"], {})
            reason = judgment.get("reason")
            if judgment.get("status") not in {"direct", "inference", "missing", "incorrect"} or not isinstance(reason, str) or not reason.strip():
                raise ValueError("every checklist item requires direct/inference/missing/incorrect and a reason")
        rows[label] = {**key["labels"][label], "scores": rating["scores"], "reasons": rating["reasons"],
                       "checklist": checklist,
                       "direct_coverage": sum(checklist[item["id"]]["status"] == "direct" for item in comparison["checklist"]) / len(comparison["checklist"])}
    if judgments.get("preferred") not in {"A", "B", "tie"} or not isinstance(judgments.get("reason"), str) or not judgments["reason"].strip():
        raise ValueError("preferred must be A/B/tie, with a reason")
    result = {"case_id": comparison["case_id"], "case_hash": comparison["case_hash"],
              "reviewer": judgments["reviewer"], "reports": rows, "preferred": judgments["preferred"],
              "preferred_input": key["labels"][judgments["preferred"]]["input"] if judgments["preferred"] != "tie" else "tie",
              "reason": judgments["reason"], "assessment": "external_reviewer",
              "limits": "Scores reflect the named reviewer's judgments on this fixed corpus, not general benchmark performance."}
    ws._write_json(directory / "scores.json", result)
    return result
