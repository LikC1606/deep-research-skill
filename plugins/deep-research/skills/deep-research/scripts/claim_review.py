"""Persist explicit reviewer judgments; never infer entailment from citations."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re


KINDS = {"unclassified", "factual", "inference", "structural", "opinion"}
VERDICTS = {"not_assessed", "supported", "partial_support", "refuted", "insufficient_evidence",
            "read_error", "execution_error"}
CONTRIBUTIONS = {"not_assessed", "necessary", "helpful", "irrelevant", "contradicts"}
GAP_VERDICTS = VERDICTS - {"not_assessed", "supported"}
MAX_BYTES = 600_000


def _read_json(path: Path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("claim review exceeds 600000 bytes; split the report")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _evidence_key(card):
    return _hash([card["content_hash"], card["quote"].strip(), card["content_scope"], card["source_truncated"]])


def _snapshot_issue(ws, card) -> str | None:
    if card is None:
        return "evidence card not found"
    try:
        _, text = ws.require_source(card["ref"], card.get("snapshot_id", card["content_hash"]))
        locator = card["locator"]
        if text[locator["start_char"]:locator["end_char"]] != card["quote"]:
            return "saved quote no longer matches its locator"
    except (ValueError, OSError, KeyError) as exc:
        return str(exc)[:240]
    return None


def _inventory(reporting, text):
    return {f"C{index}": candidate for index, candidate in enumerate(reporting._sentences(text), 1)}


def _review_version(value):
    if not isinstance(value, dict):
        raise ValueError("claim review must be a JSON object")
    version = value.get("schema_version", 1)
    if type(version) is not int or version not in {1, 2}:
        raise ValueError("claim review schema_version must be 1 or 2")
    return version


def _template(report_hash, inventory, *, schema_version=1):
    _review_version({"schema_version": schema_version})
    template = {"report_hash": report_hash, "inventory_hash": _hash(inventory), "reviewer": "",
            "claims": [{"id": key, "candidate_id": key, "claim": value["sentence"],
                        "line": value["line"], "end_line": value["end_line"],
                        "kind": "unclassified", "verdict": "not_assessed", "rationale": "", "gap": "",
                        "support_mode": "not_assessed",
                        "bindings": [{"evidence_id": item, "implicit": False, "confirmed": True,
                                      "contribution": "not_assessed", "rationale": ""}
                                     for item in value["evidence_ids"]]}
                       for key, value in inventory.items()]}
    if schema_version == 2:
        template.update(schema_version=2,
                        candidate_lengths={key: len(value["sentence"]) for key, value in inventory.items()})
        for row in template["claims"]:
            row.update(span=[0, len(row["claim"])], support_origin="not_assessed", derivation=None)
    return template


def _verify_partitions(rows, lengths):
    if not isinstance(lengths, dict) or any(type(value) is not int or value < 1 for value in lengths.values()):
        raise ValueError("v2 claim review requires candidate_lengths")
    spans = {key: [] for key in lengths}
    for row in rows:
        span = row.get("span")
        candidate_id = row["candidate_id"]
        if (candidate_id not in spans or not isinstance(span, list) or len(span) != 2
                or any(type(value) is not int for value in span)
                or not 0 <= span[0] < span[1] <= lengths[candidate_id]
                or len(row["claim"]) != span[1] - span[0]):
            raise ValueError("span must be a nonempty Unicode character interval within its candidate")
        spans[candidate_id].append(span)
    for candidate_id, intervals in spans.items():
        cursor = 0
        for start, end in sorted(intervals):
            if start != cursor:
                raise ValueError("span partition must cover every candidate completely without gaps or overlaps")
            cursor = end
        if cursor != lengths[candidate_id]:
            raise ValueError("span partition must cover every candidate completely without gaps or overlaps")


def _support_origin(item, verdict, bindings):
    origin, derivation = item.get("support_origin"), item.get("derivation")
    supported = verdict in {"supported", "partial_support"}
    if (not isinstance(origin, str) or origin not in {"not_assessed", "source_stated", "derived"}
            or supported == (origin == "not_assessed")):
        raise ValueError("support_origin must distinguish source_stated/derived support from not_assessed")
    if origin != "derived":
        if derivation is not None:
            raise ValueError("derivation applies only to derived support")
        return {"support_origin": origin, "derivation": None}
    if not isinstance(derivation, dict):
        raise ValueError("derived support requires a derivation with cited premises, formula, units and assumptions")
    premises = derivation.get("premise_evidence_ids")
    formula, units, assumptions = (derivation.get(key) for key in ("formula", "units", "assumptions"))
    contributors = {binding["evidence_id"] for binding in bindings if binding["confirmed"]
                    and not binding["snapshot_issue"] and binding["contribution"] in {"necessary", "helpful"}}
    if (not isinstance(premises, list) or not premises or any(not isinstance(value, str) for value in premises)
            or len(set(premises)) != len(premises) or not set(premises) <= contributors
            or not isinstance(formula, str) or not formula.strip() or not isinstance(units, str) or not units.strip()
            or not isinstance(assumptions, list)
            or any(not isinstance(value, str) or not value.strip() for value in assumptions)):
        raise ValueError("derivation requires valid contributing premises, a formula, units and explicit assumptions")
    return {"support_origin": origin, "derivation": {"premise_evidence_ids": premises,
            "formula": formula, "units": units, "assumptions": assumptions}}


def _normalize(ws, judgments, report_hash, inventory):
    version = _review_version(judgments)
    if judgments.get("report_hash") != report_hash or judgments.get("inventory_hash") != _hash(inventory):
        raise ValueError("claim judgments do not match report/inventory hash")
    reviewer = judgments.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("identify the claim reviewer")
    claims = judgments.get("claims")
    if not isinstance(claims, list) or (inventory and not claims):
        raise ValueError("claims must cover every candidate; classify structural/opinion text explicitly")
    with ws._lock():
        evidence = ws._evidence()
    rows, seen, covered, checked = [], set(), set(), {}
    for item in claims:
        if not isinstance(item, dict):
            raise ValueError("each claim must be a JSON object")
        claim_id, candidate_id = item.get("id"), item.get("candidate_id")
        if (not isinstance(claim_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", claim_id)
                or claim_id in seen or not isinstance(candidate_id, str) or candidate_id not in inventory):
            raise ValueError("claim IDs must be unique and reference an existing candidate_id")
        seen.add(claim_id)
        covered.add(candidate_id)
        candidate = inventory[candidate_id]
        claim, kind, verdict = item.get("claim"), item.get("kind"), item.get("verdict")
        reason, gap = item.get("rationale"), item.get("gap", "")
        mode = item.get("support_mode", "not_assessed")
        if (not isinstance(claim, str) or not (claim if version == 2 else claim.strip())
                or claim not in candidate["sentence"]
                or kind not in KINDS or verdict not in VERDICTS
                or not isinstance(reason, str) or not isinstance(gap, str)
                or mode not in {"not_assessed", "direct", "joint"}):
            raise ValueError("claim requires exact candidate text, kind, verdict, rationale and support_mode")
        if version == 2:
            span = item.get("span")
            if (not isinstance(span, list) or len(span) != 2 or any(type(value) is not int for value in span)
                    or not 0 <= span[0] < span[1] <= len(candidate["sentence"])
                    or claim != candidate["sentence"][span[0]:span[1]]):
                raise ValueError("claim must match its exact nonempty Unicode character span")
        if kind != "unclassified" and not reason.strip():
            raise ValueError("classified claims require a reviewer rationale")
        if kind not in {"factual", "inference"} and (verdict != "not_assessed" or mode != "not_assessed"):
            raise ValueError("only factual/inference claims receive support verdicts")
        if verdict in GAP_VERDICTS and not gap.strip():
            raise ValueError("incomplete/error verdicts require a concrete gap")
        if verdict not in {"supported", "partial_support"} and mode != "not_assessed":
            raise ValueError("support_mode applies only to supported/partial_support judgments")
        bindings, bound, contributors = [], set(), 0
        if not isinstance(item.get("bindings"), list):
            raise ValueError("claim bindings must be a list")
        for binding in item["bindings"]:
            evidence_id = binding.get("evidence_id")
            implicit, confirmed = binding.get("implicit"), binding.get("confirmed")
            contribution, rationale = binding.get("contribution"), binding.get("rationale")
            if (not isinstance(evidence_id, str) or evidence_id in bound
                    or type(implicit) is not bool or type(confirmed) is not bool
                    or contribution not in CONTRIBUTIONS or not isinstance(rationale, str)):
                raise ValueError("bindings require unique evidence IDs, boolean confirmation and contribution/rationale")
            bound.add(evidence_id)
            if implicit != (evidence_id not in candidate["evidence_ids"]):
                raise ValueError("bindings absent from the candidate must be marked implicit")
            if not implicit and not confirmed:
                raise ValueError("explicit citations cannot be hidden by unconfirming them")
            if (not confirmed and contribution != "not_assessed") or (contribution != "not_assessed" and not rationale.strip()):
                raise ValueError("contributions require confirmed bindings and an explicit rationale")
            if contribution in {"necessary", "helpful"} and verdict not in {"supported", "partial_support"}:
                raise ValueError("supporting contributions require supported/partial_support judgment")
            card = evidence.get(evidence_id)
            if implicit and card is None:
                raise ValueError("implicit proposals require an existing evidence card")
            if evidence_id not in checked:
                checked[evidence_id] = _snapshot_issue(ws, card)
            issue = checked[evidence_id]
            if issue and contribution != "not_assessed":
                raise ValueError("cannot assess citation contribution from an invalid snapshot")
            contributors += confirmed and not issue and contribution in {"necessary", "helpful"}
            bindings.append({**binding, "evidence": card, "snapshot_issue": issue})
        if not set(candidate["evidence_ids"]) <= bound:
            raise ValueError("retain every explicit citation, including irrelevant ones")
        if verdict in {"supported", "partial_support"}:
            if mode == "not_assessed" or contributors < (2 if mode == "joint" else 1):
                raise ValueError("support verdict requires valid contributing citations; joint support requires two")
            if any(binding["confirmed"] and binding["snapshot_issue"] for binding in bindings):
                raise ValueError("resolve invalid cited snapshots before assigning support")
        if verdict == "refuted" and not any(binding["confirmed"] and not binding["snapshot_issue"] and
                                           binding["contribution"] == "contradicts" for binding in bindings):
            raise ValueError("refuted requires a valid confirmed contradicting citation")
        rows.append({"id": claim_id, "candidate_id": candidate_id, "claim": claim, "kind": kind,
                     "verdict": verdict, "rationale": reason, "gap": gap, "support_mode": mode,
                     "line": candidate["line"], "end_line": candidate["end_line"], "bindings": bindings})
        if version == 2:
            rows[-1].update(span=span, **_support_origin(item, verdict, bindings))
    if covered != set(inventory):
        raise ValueError("claims must cover every candidate; do not omit unsupported prose")
    record = {"schema_version": version, "report_hash": report_hash, "inventory_hash": _hash(inventory),
              "reviewer": reviewer.strip(), "claims": rows, "assessment": "external_reviewer"}
    if version == 2:
        record["candidate_lengths"] = {key: len(value["sentence"]) for key, value in inventory.items()}
        declared_lengths = judgments.get("candidate_lengths", record["candidate_lengths"])
        if (not isinstance(declared_lengths, dict)
                or any(type(value) is not int for value in declared_lengths.values())
                or declared_lengths != record["candidate_lengths"]):
            raise ValueError("candidate_lengths must match the original candidate inventory")
        _verify_partitions(rows, record["candidate_lengths"])
    return record


def load_review(ws, report_hash: str, review_hash: str | None = None):
    if not re.fullmatch(r"[0-9a-f]{64}", report_hash):
        raise ValueError("invalid report hash")
    folder = ws.root() / "claim-reviews" / report_hash
    if review_hash is None:
        latest = folder / "latest.json"
        if not latest.exists():
            return None
        review_hash = _read_json(latest)["review_hash"]
    if not re.fullmatch(r"[0-9a-f]{64}", review_hash):
        raise ValueError("invalid review hash")
    record = _read_json(folder / f"{review_hash}.json")
    if _hash(record) != review_hash or record["report_hash"] != report_hash:
        raise ValueError("saved claim review hash does not match")
    return {**record, "review_hash": review_hash}


def _candidate_metrics(record, rows):
    groups = {}
    for row in rows:
        groups.setdefault(row["candidate_id"], []).append(row)
    summary = {"unit": "candidate", "coverage_verified": False, "candidate_count": len(groups),
               **{key: None for key in ("eligible_candidates", "assessed_candidates", "fully_supported_candidates",
                   "unclassified_candidates", "fully_classified_candidates", "support_coverage",
                   "semantic_assessment_coverage", "source_stated_supported_candidates", "derived_supported_candidates")}}
    if _review_version(record) != 2:
        return summary
    _verify_partitions(rows, record.get("candidate_lengths"))
    eligible = assessed = supported = unclassified = stated = derived = 0
    for group in groups.values():
        unknown = any(row["kind"] == "unclassified" for row in group)
        unclassified += unknown
        facts = [row for row in group if row["kind"] in {"factual", "inference"}]
        if not facts:
            continue
        eligible += 1
        assessed += not unknown and all(row["effective_verdict"] not in {
            "not_assessed", "read_error", "execution_error"} for row in facts)
        complete = not unknown and all(row["effective_verdict"] == "supported" for row in facts)
        supported += complete
        if complete:
            origins = {row["support_origin"] for row in facts}
            stated += origins == {"source_stated"}
            derived += "derived" in origins
    return {**summary, "coverage_verified": True, "eligible_candidates": eligible,
            "assessed_candidates": assessed, "fully_supported_candidates": supported,
            "unclassified_candidates": unclassified, "fully_classified_candidates": len(groups) - unclassified,
            "support_coverage": supported / eligible if eligible else None,
            "semantic_assessment_coverage": assessed / eligible if eligible else None,
            "source_stated_supported_candidates": stated, "derived_supported_candidates": derived}


def metrics(ws, record):
    kinds, verdicts = Counter(), Counter()
    eligible = assessed = supported = citations = evaluated = useful = proposals = invalid = 0
    effective, checked = [], {}
    for row in record["claims"]:
        for binding in row["bindings"]:
            key = _hash(binding["evidence"])
            if key not in checked:
                checked[key] = _snapshot_issue(ws, binding["evidence"])
    for row in record["claims"]:
        row = {**row, "bindings": [{**binding, "snapshot_issue": checked[_hash(binding["evidence"])]}
                                   for binding in row["bindings"]]}
        broken = any(binding["confirmed"] and binding["snapshot_issue"] and
                     binding["contribution"] in {"necessary", "helpful", "contradicts"} for binding in row["bindings"])
        row["effective_verdict"] = "read_error" if broken and row["verdict"] in {"supported", "partial_support", "refuted"} else row["verdict"]
        kinds[row["kind"]] += 1
        verdicts[row["effective_verdict"]] += 1
        if row["kind"] in {"factual", "inference"}:
            eligible += 1
            assessed += row["effective_verdict"] not in {"not_assessed", "read_error", "execution_error"}
            supported += row["effective_verdict"] == "supported"
            for binding in row["bindings"]:
                if not binding["confirmed"]:
                    proposals += 1
                    continue
                citations += 1
                if binding["snapshot_issue"]:
                    invalid += 1
                elif binding["contribution"] != "not_assessed":
                    evaluated += 1
                    useful += binding["contribution"] in {"necessary", "helpful"}
        effective.append(row)
    version = _review_version(record)
    origins = Counter(row["support_origin"] for row in effective
                      if version == 2 and row["effective_verdict"] == "supported")
    return {"unit": "claim_row", "claim_count": len(effective), "kind_counts": dict(kinds), "verdict_counts": dict(verdicts),
            "eligible_claims": eligible, "assessed_claims": assessed, "fully_supported_claims": supported,
            "support_coverage": supported / eligible if eligible else None,
            "semantic_assessment_coverage": assessed / eligible if eligible else None,
            "unclassified_claims": kinds["unclassified"], "confirmed_citation_links": citations,
            "assessed_citation_links": evaluated, "contributing_citation_links": useful,
            "citation_contribution_rate": useful / evaluated if evaluated else None,
            "invalid_citation_links": invalid, "unconfirmed_implicit_proposals": proposals,
            "source_stated_supported_claims": origins["source_stated"] if version == 2 else None,
            "derived_supported_claims": origins["derived"] if version == 2 else None,
            "candidate_metrics": _candidate_metrics(record, effective),
            "limits": "Reviewer judgments only. Coverage uses factual/inference claims, not unknown or structural text. "
                      "Joint support does not require each source to entail the claim alone. Segmentation/classification may miss claims."}, effective


def claims(ws, reporting, path: str, *, judgments_path: str | None = None,
           schema_version: int | None = None, offset=0, limit=5):
    if offset < 0 or not 1 <= limit <= 10:
        raise ValueError("offset must be nonnegative and limit between 1 and 10")
    text = reporting.read_report(path)
    report_hash = hashlib.sha256(text.encode()).hexdigest()
    inventory = _inventory(reporting, text)
    folder = ws.root() / "claim-reviews" / report_hash
    judgments = _read_json(Path(judgments_path)) if judgments_path is not None else None
    if schema_version is not None:
        _review_version({"schema_version": schema_version})
    if judgments_path is not None:
        version = _review_version(judgments)
        if schema_version is not None and schema_version != version:
            raise ValueError("--schema-version conflicts with the judgments schema_version")
        record = None
    else:
        record = load_review(ws, report_hash)
        version = _review_version(record) if record else 1
        if schema_version is not None:
            version = schema_version
    template_path = folder / ("judgments.v2.template.json" if version == 2 else "judgments.template.json")
    if not template_path.exists():
        template = _template(report_hash, inventory, schema_version=version)
        if len(json.dumps(template, ensure_ascii=False, indent=2).encode()) >= MAX_BYTES:
            raise ValueError("claim review template exceeds 600000 bytes; split the report")
        ws._write_json(template_path, template)
    if judgments_path is not None:
        record = _normalize(ws, judgments, report_hash, inventory)
        if len(json.dumps(record, ensure_ascii=False, indent=2).encode()) >= MAX_BYTES:
            raise ValueError("claim review exceeds 600000 bytes; split the report")
        review_hash = _hash(record)
        with ws._lock():
            if not (folder / f"{review_hash}.json").exists():
                ws._write_json(folder / f"{review_hash}.json", record)
            record = load_review(ws, report_hash, review_hash)
            ws._write_json(folder / "latest.json", {"review_hash": review_hash})
    if record is None:
        record = _template(report_hash, inventory, schema_version=version)
        with ws._lock():
            evidence = ws._evidence()
        record["claims"] = [{**row, "bindings": [{**binding, "evidence": evidence.get(binding["evidence_id"])} for binding in row["bindings"]]}
                            for row in record["claims"]]
    summary, rows = metrics(ws, record)
    page, size = [], 0
    for row in rows[offset:offset + limit]:
        preview = {**row, "claim": row["claim"][:1600], "rationale": row["rationale"][:1000], "gap": row["gap"][:800],
                   "bindings": [{"evidence_id": binding["evidence_id"][:128], **{key: binding[key] for key in
                                 ("implicit", "confirmed", "contribution", "snapshot_issue")},
                                 "snapshot_id": (binding["evidence"] or {}).get("snapshot_id"),
                                 "ref": (binding["evidence"] or {}).get("ref")}
                                for binding in row["bindings"][:20]]}
        if row.get("derivation") is not None:
            derivation = row["derivation"]
            preview["derivation"] = {"premise_evidence_ids": [value[:128] for value in derivation["premise_evidence_ids"][:20]],
                                     "formula": derivation["formula"][:1600], "units": derivation["units"][:800],
                                     "assumptions": [value[:800] for value in derivation["assumptions"][:10]]}
            derived_preview = preview["derivation"]
            while len(json.dumps(derived_preview, ensure_ascii=False).encode()) > 8000:
                if derived_preview["assumptions"]:
                    derived_preview["assumptions"].pop()
                else:
                    for key in ("formula", "units"):
                        derived_preview[key] = derived_preview[key][:len(derived_preview[key]) // 2]
                    premises = derived_preview["premise_evidence_ids"]
                    derived_preview["premise_evidence_ids"] = premises[:max(1, len(premises) // 2)]
        preview["preview_truncated"] = preview != row
        length = len(json.dumps(preview, ensure_ascii=False).encode())
        if page and size + length > 40_000:
            break
        page.append(preview)
        size += length
    return {"schema_version": _review_version(record), "template_schema_version": version,
            "report_hash": report_hash, "review_hash": record.get("review_hash"), "reviewer": record.get("reviewer") or None,
            "template_path": str(template_path.resolve()),
            "review_path": str((folder / f"{record['review_hash']}.json").resolve()) if record.get("review_hash") else None,
            "total_candidates": len(inventory), "metrics": summary, "claims": page, "offset": offset,
            "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
            "note": "Optional semantic review, separate from structural audit. Implicit bindings require confirmation. "
                    "No Need is closed or changed by these judgments."}


def _gap_preview(ws, gap):
    value = {key: gap[key] for key in ("id", "report_hash", "claim_id", "need", "review_hash", "max_attempts",
                                      "no_progress_limit", "state")}
    value.update(claim=gap["claim"][:1600], gap=gap["gap"][:1000],
                 seen_evidence_count=len(gap["seen_evidence_ids"]),
                 attempts=[{key: (value[:1000] if isinstance(value, str) else value[:20] if isinstance(value, list) else value)
                            for key, value in item.items()} for item in gap["attempts"]],
                 artifact_path=str((ws.root() / "task.json").resolve()),
                 preview_truncated=True)
    if "span" in gap:
        value.update(candidate_id=gap["candidate_id"], span=gap["span"])
    if "stop_reason" in gap:
        value["stop_reason"] = gap["stop_reason"][:1000]
    if "resolved_review_hash" in gap:
        value["resolved_review_hash"] = gap["resolved_review_hash"]
    return value


def new_gap(ws, reporting, path: str, claim_id: str, need: str, *, max_attempts=3):
    if not 1 <= max_attempts <= 6:
        raise ValueError("gap max-attempts must be between 1 and 6")
    report_hash = hashlib.sha256(reporting.read_report(path).encode()).hexdigest()
    record = load_review(ws, report_hash)
    if record is None:
        raise ValueError("save explicit claim judgments before creating a gap")
    _, rows = metrics(ws, record)
    row = next((row for row in rows if row["id"] == claim_id), None)
    if row is None or row["kind"] not in {"factual", "inference"} or row["effective_verdict"] not in GAP_VERDICTS:
        raise ValueError("gap requires an explicitly reviewed incomplete/error factual or inference claim")
    description = row["gap"] or "Re-read the missing or invalid source snapshot before reassessing this claim."
    basis = {"report_hash": report_hash, "claim_id": claim_id, "need": need}
    if _review_version(record) == 2:
        basis.update(candidate_id=row["candidate_id"], span=row["span"])
    gap_id = "GAP-" + _hash(basis)[:16].upper()
    with ws._lock():
        task = ws._require_task(active=True)
        if need not in task["needs"]:
            raise ValueError("register the gap Need first")
        gaps = task.setdefault("review_gaps", {})
        if gap_id not in gaps:
            cards = [card for card in ws._evidence().values() if card["need"] == need]
            gaps[gap_id] = {"id": gap_id, **basis, "gap": description, "review_hash": record["review_hash"],
                            "claim": row["claim"], "max_attempts": max_attempts, "no_progress_limit": 2,
                            "state": "ready", "attempts": [],
                            "seen_evidence_ids": sorted(card["id"] for card in cards),
                            "seen_evidence_keys": sorted({_evidence_key(card) for card in cards})}
        else:
            gaps[gap_id].update(gap=description, review_hash=record["review_hash"])
        ws._save_task(task)
        return _gap_preview(ws, gaps[gap_id])


def reserve_gap_search(ws, gap_id, query, need=None):
    if not query.strip() or len(query) > 2000:
        raise ValueError("gap query must contain 1-2000 characters")
    normalized = " ".join(query.casefold().split())
    with ws._lock():
        task = ws._require_task(active=True)
        gap = task.get("review_gaps", {}).get(gap_id)
        if gap is None:
            raise ValueError("unknown review gap")
        if need is not None and need != gap["need"]:
            raise ValueError("search Need does not match the review gap")
        record = load_review(ws, gap["report_hash"])
        if record is None or record["review_hash"] != gap["review_hash"]:
            raise ValueError("claim review changed; refresh the gap before searching")
        if gap["state"] != "ready" or len(gap["attempts"]) >= gap["max_attempts"]:
            raise ValueError("gap cannot search: resolve the pending outcome or stop at its bound")
        if any(row["normalized_query"] == normalized for row in gap["attempts"]):
            raise ValueError("gap query repeats a prior attempt; pivot to a concrete missing fact")
        attempt = {"id": len(gap["attempts"]) + 1, "query": query, "normalized_query": normalized,
                   "outcome": "pending", "search_finished": False}
        gap["attempts"].append(attempt)
        gap["state"] = "searching"
        ws._save_task(task)
        return gap["need"], attempt["id"]


def _gap_state(gap):
    attempts = gap["attempts"]
    no_progress = 0
    for item in reversed(attempts):
        if item["outcome"] not in {"no_progress", "error"}:
            break
        no_progress += 1
    if len(attempts) >= gap["max_attempts"] or no_progress >= gap["no_progress_limit"]:
        return "stopped"
    return "ready"


def finish_gap_search(ws, gap_id, attempt_id, *, result=None, error=None):
    with ws._lock():
        task = ws._require_task()
        gap = task["review_gaps"][gap_id]
        attempt = gap["attempts"][attempt_id - 1]
        attempt["search_finished"] = True
        if error is not None:
            attempt.update(outcome="error", reason=str(error)[:1000])
        else:
            attempt.update(search_id=result["search_id"], cache_hit=result["cache_hit"],
                           new_ref_count=result["new_ref_count"])
        if gap["state"] == "searching":
            gap["state"] = _gap_state(gap) if error is not None else "awaiting_review"
        ws._save_task(task)
        return {"gap_id": gap_id, "gap_state": gap["state"], "attempt": attempt_id,
                "next_action": "Read candidates and record gap-result with new evidence or no_progress. New refs are not evidence."}


def gap_result(ws, gap_id, outcome, reason, evidence_ids=None):
    if outcome not in {"progress", "no_progress", "resolved", "stop"} or not reason.strip() or len(reason) > 2000:
        raise ValueError("gap result requires progress/no_progress/resolved/stop and a reason of 1-2000 characters")
    evidence_ids = evidence_ids or []
    with ws._lock():
        task = ws._require_task()
        gap = task.get("review_gaps", {}).get(gap_id)
        if gap is None:
            raise ValueError("unknown review gap")
        if outcome == "stop":
            gap.update(state="stopped", stop_reason=reason)
        else:
            if gap["state"] != "awaiting_review":
                raise ValueError("a finished search must await its evidence outcome")
            cards = ws._evidence()
            if len(set(evidence_ids)) != len(evidence_ids):
                raise ValueError("gap evidence IDs must be unique")
            for item in evidence_ids:
                card = cards.get(item)
                if card is None or card["need"] != gap["need"] or _snapshot_issue(ws, card):
                    raise ValueError("gap evidence must be valid snapshots for the same Need")
            new_ids = set(evidence_ids) - set(gap["seen_evidence_ids"])
            keys = {_evidence_key(cards[item]) for item in evidence_ids}
            if outcome in {"progress", "resolved"} and (not new_ids or not keys - set(gap["seen_evidence_keys"])):
                raise ValueError("progress requires newly read evidence, not new links or repeated cards")
            if outcome == "resolved":
                record = load_review(ws, gap["report_hash"])
                if record is None:
                    raise ValueError("resolution requires a new explicit supported review using the new evidence")
                _, rows = metrics(ws, record)
                row = next((row for row in rows if row["id"] == gap["claim_id"]), None)
                if (row is None or row["claim"] != gap["claim"] or row["effective_verdict"] != "supported"
                        or ("span" in gap and (row.get("span") != gap["span"]
                                              or row["candidate_id"] != gap["candidate_id"]))
                        or not new_ids <= {
                    item["evidence_id"] for item in row["bindings"] if item["confirmed"] and
                    item["contribution"] in {"necessary", "helpful"} and not item["snapshot_issue"]
                }):
                    raise ValueError("resolution requires a new explicit supported review of the same claim/span using the new evidence")
                gap["resolved_review_hash"] = record["review_hash"]
            gap["attempts"][-1].update(outcome=outcome, reason=reason, evidence_ids=evidence_ids,
                                        assessment="caller")
            gap["seen_evidence_ids"] = sorted(set(gap["seen_evidence_ids"]) | set(evidence_ids))
            gap["seen_evidence_keys"] = sorted(set(gap["seen_evidence_keys"]) | keys)
            gap["state"] = "resolved" if outcome == "resolved" else _gap_state(gap)
        ws._save_task(task)
        return _gap_preview(ws, gap)
