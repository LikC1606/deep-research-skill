"""Freeze calibration packets and compare external judgments with declared expectations."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import re


VERDICTS = {"supported", "refuted", "insufficient_evidence", "missing", "not_assessable"}
MAX_BYTES = 600_000
REVIEW_INSTRUCTIONS = {
    "task": "Assess the report's substantive assertion for every criterion against this packet's corpus. "
            "Use only the packet and judge each report independently. Rewording, order, length and repetition earn no extra factual credit.",
    "verdicts": {"supported": "The assertion or explicitly stated evidence limit is supported by the corpus or a valid transparent derivation.",
                 "refuted": "The corpus contradicts the report's assertion, including a numerically incorrect derivation.",
                 "insufficient_evidence": "The report asserts something the corpus neither establishes nor contradicts.",
                 "missing": "The report does not address this criterion.",
                 "not_assessable": "You cannot assess this item; explain the limitation. It remains an expected calibration check."},
    "support_origin": "For supported only: source_stated for explicit source facts or evidence limits, including linked identity fields; "
                      "derived for arithmetic, threshold application or analytic recommendations. Otherwise use not_assessed. "
                      "Several sources can state facts jointly; their number does not determine origin.",
    "evidence": "Provide a short reason and an exact report_quote for supported, refuted and insufficient_evidence. "
                "For supported/refuted, cite the corpus source_ids that establish your judgment. "
                "A missing field in the corpus does not by itself refute an assertion or prove nonexistence. "
                "Use source IDs in the packet; report citations to old local paths do not authorize external reads.",
}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _read_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("calibration JSON exceeds 600000 bytes")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key!r}")
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError("calibration JSON requires finite numbers")
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("calibration JSON requires finite numbers")
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("calibration JSON contains an unpaired Unicode surrogate") from exc
    return value


def _version(value):
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("calibration requires schema_version=1")


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is not None


def _verdict(value):
    if not isinstance(value, dict) or not isinstance(value.get("verdict"), str) or value["verdict"] not in VERDICTS:
        raise ValueError("each criterion requires an explicit calibration verdict")
    origins = {"source_stated", "derived"} if value["verdict"] == "supported" else {"not_assessed"}
    if not isinstance(value.get("support_origin"), str) or value["support_origin"] not in origins:
        raise ValueError("support_origin must distinguish source-stated/derived support from not_assessed")
    return {name: value[name] for name in ("verdict", "support_origin")}


def _expectations(value, criteria):
    if not isinstance(value, dict) or set(value) != set(criteria):
        raise ValueError("expected judgments must match every criterion exactly")
    for item in value.values():
        if not isinstance(item, dict) or set(item) != {"verdict", "support_origin"}:
            raise ValueError("expected judgments require only verdict and support_origin")
        _verdict(item)
    return value


def _suite(evaluation, path):
    path = Path(path).resolve()
    suite = _read_json(path)
    _version(suite)
    if not _id(suite.get("id")) or not _nonempty(suite.get("case")):
        raise ValueError("calibration suite requires an id and case path")
    criteria = suite.get("criteria")
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 50:
        raise ValueError("calibration requires 1-50 atomic criteria")
    ids = []
    for item in criteria:
        if (not isinstance(item, dict) or not _id(item.get("id")) or item["id"] in ids
                or not _nonempty(item.get("requirement"))):
            raise ValueError("criteria require unique IDs and a requirement")
        ids.append(item["id"])
    variants = suite.get("variants")
    if not isinstance(variants, list) or not 1 <= len(variants) <= 30:
        raise ValueError("calibration requires 1-30 frozen variants")
    anchor = suite.get("anchor")
    if not isinstance(anchor, dict):
        raise ValueError("calibration requires a frozen anchor")
    _expectations(anchor.get("expected"), ids)
    seen = {"anchor"}
    for variant in variants:
        if (not isinstance(variant, dict) or not _id(variant.get("id")) or variant["id"] in seen
                or variant.get("kind") not in {"error", "control"} or not _nonempty(variant.get("description"))):
            raise ValueError("variants require unique IDs, kind and description")
        seen.add(variant["id"])
        expected = _expectations(variant.get("expected"), ids)
        targets = variant.get("targets")
        if (not isinstance(targets, list) or any(not isinstance(item, str) for item in targets)
                or len(set(targets)) != len(targets) or not set(targets) <= set(ids)):
            raise ValueError("variant targets must be unique criterion IDs")
        changed = {item for item in ids if expected[item] != anchor["expected"][item]}
        if (variant["kind"] == "control" and (targets or changed)
                or variant["kind"] == "error" and (not changed or changed != set(targets))):
            raise ValueError("controls preserve all expectations; errors change exactly their declared targets")
    if {item["kind"] for item in variants} != {"error", "control"}:
        raise ValueError("calibration requires both error variants and equivalent controls")
    case_path, case, corpus, _ = evaluation._case_material(str(path.parent / suite["case"]))
    artifacts, content_expectations = {}, {}
    for name, item in [("anchor", anchor)] + [(item["id"], item) for item in variants]:
        if not _nonempty(item.get("path")) or not isinstance(item.get("sha256"), str):
            raise ValueError("frozen reports require path and sha256")
        with (path.parent / item["path"]).open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) > MAX_BYTES or digest != item["sha256"]:
            raise ValueError("frozen report hash does not match or report exceeds 600000 bytes")
        text = raw.decode("utf-8")
        if not text.strip():
            raise ValueError("frozen report must not be empty")
        if digest in content_expectations and content_expectations[digest] != item["expected"]:
            raise ValueError("identical report hashes cannot have conflicting expectations")
        content_expectations[digest] = item["expected"]
        artifacts[name] = {"text": text, "report_hash": digest}
    basis = {"suite": suite, "suite_hash": _hash(suite),
             "case_hash": hashlib.sha256(case_path.read_bytes()).hexdigest(),
             "corpus_hash": _hash([{name: item[name] for name in ("id", "hash")} for item in corpus])}
    return basis, case, corpus, artifacts


def prepare(ws, evaluation, case_suite_path, output, *, seed=0):
    if type(seed) is not int:
        raise ValueError("calibration seed must be an integer")
    basis, case, corpus, artifacts = _suite(evaluation, case_suite_path)
    suite = basis["suite"]
    rng = random.Random(seed)
    assigned, used = [[], []], set()
    order = list(artifacts)
    rng.shuffle(order)
    for offset in range(0, len(order), 2):
        pair = order[offset:offset + 2]
        for index in range(2):
            frame_id = "F" + f"{rng.getrandbits(64):016X}"
            while frame_id in used:
                frame_id = "F" + f"{rng.getrandbits(64):016X}"
            used.add(frame_id)
            labels = dict(zip(("A", "B"), pair if index == 0 else pair[::-1]))
            if index == 1 and len(pair) == 1:
                labels = {"B": pair[0]}
            assigned[index].append({"id": frame_id,
                                    "labels": labels})
    packets, templates, assignments = [], [], {}
    for index, frames in enumerate(assigned, 1):
        rng.shuffle(frames)
        assignment = {frame["id"]: {"labels": frame["labels"]} for frame in frames}
        packet = {"schema_version": 1, "suite_id": suite["id"], "basis_hash": _hash(basis),
                  "assignment_hash": _hash(assignment), "question": case["question"],
                  "review_instructions": REVIEW_INSTRUCTIONS,
                  "corpus": [{name: item[name] for name in ("id", "text", "hash")} for item in corpus],
                  "criteria": [{name: item[name] for name in ("id", "requirement")} for item in suite["criteria"]],
                  "frames": [{"id": frame["id"], "reports": {label: artifacts[artifact]
                              for label, artifact in frame["labels"].items()}} for frame in frames]}
        packet["packet_hash"] = _hash(packet)
        template = {"schema_version": 1, "packet_hash": packet["packet_hash"], "reviewer": "",
                    "frames": {frame["id"]: {label: {"report_hash": report["report_hash"],
                        "criteria": {item["id"]: {"verdict": "", "support_origin": "", "reason": "",
                                                   "report_quote": "", "source_ids": []}
                                     for item in suite["criteria"]}}
                        for label, report in frame["reports"].items()} for frame in packet["frames"]}}
        packets.append(packet)
        templates.append(template)
        assignments[packet["packet_hash"]] = assignment
    key = {"schema_version": 1, "seed": seed, "basis": basis, "assignments": assignments,
           "packet_hashes": [packet["packet_hash"] for packet in packets]}
    for value in [key, *packets, *templates]:
        if len(json.dumps(value, ensure_ascii=False, indent=2).encode()) > MAX_BYTES:
            raise ValueError("calibration packet exceeds 600000 bytes; split the suite")
    destination = Path(output).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    ws._write_json(destination / "key.json", key)
    for index, (packet, template) in enumerate(zip(packets, templates), 1):
        ws._write_json(destination / f"blind-{index}.json", packet)
        ws._write_json(destination / f"judgments-{index}.template.json", template)
    return {"calibration_dir": str(destination),
            "blind_packets": [str(destination / f"blind-{index}.json") for index in (1, 2)],
            "judgment_templates": [str(destination / f"judgments-{index}.template.json") for index in (1, 2)],
            "note": "Give each packet and its template to the same reviewer in separate fresh contexts. "
                    "Keep key.json and suite expectations private. This command does not call or calibrate a model."}


def _judged(value, report, criteria, source_ids):
    if not isinstance(value, dict) or value.get("report_hash") != report["report_hash"]:
        raise ValueError("judgments do not match the blinded report hash")
    ratings = value.get("criteria")
    if not isinstance(ratings, dict) or set(ratings) != set(criteria):
        raise ValueError("criterion judgments must match exactly")
    for item in ratings.values():
        _verdict(item)
        quote, sources = item.get("report_quote"), item.get("source_ids")
        if (not _nonempty(item.get("reason")) or not isinstance(quote, str)
                or quote and quote not in report["text"] or not isinstance(sources, list)
                or any(not isinstance(source, str) for source in sources)
                or len(set(sources)) != len(sources) or not set(sources) <= source_ids):
            raise ValueError("each criterion requires a reason, exact report_quote and valid unique source_ids")
        if (item["verdict"] in {"supported", "refuted", "insufficient_evidence"} and not quote.strip()
                or item["verdict"] in {"supported", "refuted"} and not sources):
            raise ValueError("assessed assertions require a report_quote; supported/refuted also require source_ids")
    return ratings


def score(ws, directory, judgments_paths):
    directory = Path(directory).resolve()
    if not isinstance(judgments_paths, (list, tuple)) or len(judgments_paths) != 2:
        raise ValueError("calibration requires judgments for both blind packets")
    key = _read_json(directory / "key.json")
    _version(key)
    basis = key["basis"]
    suite = basis["suite"]
    criteria = [item["id"] for item in suite["criteria"]]
    variants = {item["id"]: item for item in suite["variants"]}
    expected = {"anchor": suite["anchor"]["expected"], **{item: value["expected"] for item, value in variants.items()}}
    packets = {}
    for index in (1, 2):
        packet = _read_json(directory / f"blind-{index}.json")
        _version(packet)
        packet_hash = packet.get("packet_hash")
        if (packet_hash != _hash({name: value for name, value in packet.items() if name != "packet_hash"})
                or packet_hash not in key["packet_hashes"] or packet_hash in packets
                or packet["basis_hash"] != _hash(basis)
                or packet["assignment_hash"] != _hash(key["assignments"][packet_hash])):
            raise ValueError("calibration packet or private assignment hash does not match")
        for frame in packet["frames"]:
            assignment = key["assignments"][packet_hash][frame["id"]]
            for label, report in frame["reports"].items():
                artifact = assignment["labels"][label]
                frozen = suite["anchor"] if artifact == "anchor" else variants[artifact]
                if hashlib.sha256(report["text"].encode()).hexdigest() != report["report_hash"] or report["report_hash"] != frozen["sha256"]:
                    raise ValueError("blinded report does not match its frozen hash")
        if any(hashlib.sha256(item["text"].encode()).hexdigest() != item["hash"] for item in packet["corpus"]):
            raise ValueError("calibration corpus hash does not match")
        packets[packet_hash] = packet
    submissions = [_read_json(path) for path in judgments_paths]
    reviewers, configs, hashes = set(), [], []
    for value in submissions:
        _version(value)
        if not _nonempty(value.get("reviewer")):
            raise ValueError("identify the calibration reviewer")
        reviewers.add(value["reviewer"])
        configs.append(value.get("reviewer_config"))
        hashes.append(value.get("packet_hash"))
    if len(reviewers) != 1 or _hash(configs[0]) != _hash(configs[1]):
        raise ValueError("both packets must use the same reviewer and configuration")
    if len(set(hashes)) != 2 or set(hashes) != set(packets):
        raise ValueError("submit each of the two blind packet hashes exactly once")
    mismatches, occurrences = [], defaultdict(list)
    total = matched = control_total = control_matched = target_total = target_matched = 0
    for submitted in submissions:
        packet_hash = submitted["packet_hash"]
        packet = packets[packet_hash]
        frames = submitted.get("frames")
        if not isinstance(frames, dict) or set(frames) != {frame["id"] for frame in packet["frames"]}:
            raise ValueError("frame judgments must match exactly")
        source_ids = {item["id"] for item in packet["corpus"]}
        for frame in packet["frames"]:
            judgments = frames[frame["id"]]
            if not isinstance(judgments, dict) or set(judgments) != set(frame["reports"]):
                raise ValueError("each frame requires exactly its blinded report labels")
            assignment = key["assignments"][packet_hash][frame["id"]]
            for label, report in frame["reports"].items():
                artifact = assignment["labels"][label]
                variant = variants.get(artifact)
                ratings = _judged(judgments[label], report, criteria, source_ids)
                for criterion, rating in ratings.items():
                    actual = _verdict(rating)
                    correct = actual == expected[artifact][criterion]
                    total += 1
                    matched += correct
                    if variant and variant["kind"] == "control":
                        control_total += 1
                        control_matched += correct
                    if variant and criterion in variant["targets"]:
                        target_total += 1
                        target_matched += correct
                    location = {"packet_hash": packet_hash, "frame_id": frame["id"], "label": label}
                    occurrences[(report["report_hash"], criterion)].append({**location, "artifact": artifact, **actual})
                    if not correct:
                        mismatches.append({**location, "artifact": artifact, "criterion_id": criterion,
                                           "expected": expected[artifact][criterion], "actual": actual})
    inconsistent = []
    for (report_hash, criterion), values in occurrences.items():
        if len({(item["verdict"], item["support_origin"]) for item in values}) > 1:
            inconsistent.append({"report_hash": report_hash, "criterion_id": criterion, "occurrences": values})
    judgment_hashes = [_hash(value) for value in submissions]
    result = {"schema_version": 1, "suite_id": suite["id"], "suite_hash": basis["suite_hash"],
              "reviewer": next(iter(reviewers)), "reviewer_config": configs[0],
              "packet_hashes": key["packet_hashes"], "judgment_hashes": judgment_hashes,
              "passed": total == matched and not inconsistent,
              "expected_checks": total, "matched_checks": matched, "mismatches": mismatches,
              "errors": {"passed": target_total == target_matched, "target_checks": target_total,
                         "matched_target_checks": target_matched},
              "controls": {"passed": control_total == control_matched, "expected_checks": control_total,
                           "matched_checks": control_matched},
              "label_consistency": {"passed": not inconsistent, "inconsistencies": inconsistent},
              "assessment": "external_reviewer_calibration", "quality_improvement_assessed": False,
              "limits": "Calibration covers these declared errors and controls only. It does not establish general reviewer "
                        "accuracy, verify reviewer identity or model settings, or measure research quality or model costs."}
    for value, digest in zip(submissions, judgment_hashes):
        ws._write_json(directory / "reviews" / f"{digest}.json", value)
    ws._write_json(directory / "calibration.json", result)
    return result
