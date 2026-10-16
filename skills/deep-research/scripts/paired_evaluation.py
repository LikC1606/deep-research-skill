"""Predeclare offline paired runs and retain two independent factual reviews.

This is a local protocol recorder, not a model runner, identity verifier or sandbox.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import time


VARIANTS = ("a", "b")
OFFLINE_COMMANDS = {"init", "need", "status", "search", "read", "find", "open", "evidence", "review",
                    "section", "outline", "synthesis", "packet", "failure", "stop", "resume"}
MEASURES = ("end_to_end_seconds", "model_tokens", "cost_usd")
LIMITS = ("Public development cases; no general quality, speed or cost improvement follows from these runs. "
          "Declared model/configuration/context identities are not authenticated. Resource exports and CLI traces "
          "are not an OS sandbox and cannot detect reads or tools outside the recorder. CLI/tool seconds and the "
          "protocol start/finish interval are not end-to-end model measurements.")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    return _digest(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode())


def _now():
    return datetime.now(timezone.utc).isoformat()


def _toolchain():
    versions = {}
    for package in ("requests", "beautifulsoup4", "lxml", "markdown-it-py"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return {"python": sys.version, "executable": sys.executable, "packages": versions,
            "recorder_sha256": _digest(Path(__file__).read_bytes())}


@contextmanager
def _lock(directory):
    with (directory / ".paired.lock").open("a") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _store(ws, cal, path, value):
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode()
    if len(raw) > cal.MAX_BYTES:
        raise ValueError("paired record exceeds 600000 bytes; split the study")
    if path.exists():
        if _hash(cal._read_json(path)) != _hash(value):
            raise ValueError(f"immutable paired record differs: {path.name}")
    else:
        ws._atomic_write(path, raw.decode() + "\n")


def _sealed(value):
    return {**value, "record_hash": _hash(value)}


def _read_sealed(cal, path):
    value = cal._read_json(path)
    if value.get("record_hash") != _hash({k: v for k, v in value.items() if k != "record_hash"}):
        raise ValueError("paired record hash does not match")
    return value


def _files(root, *, source=False):
    if root.is_symlink():
        raise ValueError("skill runtime root must not be a symlink")
    result = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part == "__pycache__" or part.endswith((".pyc", ".pyo")) for part in rel.parts):
            continue
        if source and any(part.startswith(".") for part in rel.parts):
            continue
        allowed = (len(rel.parts) == 1 and rel.name in {"SKILL.md", "requirements.txt"}
                   or rel.parts[0] in {"scripts", "references", "agents", "assets"})
        if path.is_symlink():
            if allowed:
                raise ValueError("skill runtime resources must not be symlinks")
            continue
        if path.is_file() and (allowed or not source):
            if not allowed:
                raise ValueError("unexpected file in frozen runtime snapshot")
            result[rel.as_posix()] = _digest(path.read_bytes())
    if not {"SKILL.md", "scripts/research.py"} <= set(result):
        raise ValueError("skill snapshot requires SKILL.md and scripts/research.py")
    if len(result) > 1000 or sum((root / name).stat().st_size for name in result) > 5_000_000:
        raise ValueError("skill runtime snapshot exceeds resource budget")
    return result


def _configuration(cal, value):
    expected = {"model_id", "reasoning", "settings", "tool_versions", "max_report_characters", "stop_conditions"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("run_config requires model_id, reasoning, settings, tool_versions, max_report_characters and stop_conditions")
    if value["model_id"] is not None and not cal._nonempty(value["model_id"]):
        raise ValueError("model_id must be a nonempty string or null")
    if value["reasoning"] is not None and not isinstance(value["reasoning"], (str, dict)):
        raise ValueError("reasoning must be a string, object or null")
    if isinstance(value["reasoning"], str) and not cal._nonempty(value["reasoning"]):
        raise ValueError("use null for an unknown reasoning setting")
    if value["settings"] is not None and not isinstance(value["settings"], dict):
        raise ValueError("settings must be an object or null")
    if (not isinstance(value["tool_versions"], dict) or not value["tool_versions"]
            or any(not cal._nonempty(k) or v is not None and not cal._nonempty(v)
                   for k, v in value["tool_versions"].items())):
        raise ValueError("declare tool_versions, using null for unknown versions")
    if type(value["max_report_characters"]) is not int or not 1 <= value["max_report_characters"] <= 100_000:
        raise ValueError("max_report_characters must be an integer from 1 to 100000")
    if (not isinstance(value["stop_conditions"], list) or not value["stop_conditions"]
            or any(not cal._nonempty(item) for item in value["stop_conditions"])):
        raise ValueError("declare nonempty stop_conditions")
    return value


def _public(case):
    return {"id": case["id"], "question": case["question"], "budget": case["budget"],
            "sources": [{"id": item["id"], "path": f"corpus/{item['id']}.txt", "hash": item["hash"]}
                        for item in case["corpus"]]}


def _load(cal, directory):
    root = Path(directory).resolve()
    manifest = cal._read_json(root / "manifest.json")
    if manifest.get("manifest_hash") != _hash({k: v for k, v in manifest.items() if k != "manifest_hash"}):
        raise ValueError("paired manifest hash does not match")
    if _hash(manifest["recorder_toolchain"]) != _hash(_toolchain()):
        raise ValueError("paired recorder implementation or tool versions changed")
    for variant, identity in manifest["variants"].items():
        if _hash(_files(root / "researcher" / "variants" / variant)) != identity["resource_hash"]:
            raise ValueError("frozen skill resource hash does not match")
    for case in manifest["cases"].values():
        pack = root / "researcher" / "cases" / case["id"]
        if pack.resolve() != pack or (pack / "case.json").is_symlink():
            raise ValueError("frozen researcher inputs must not alias another directory")
        if _hash(cal._read_json(pack / "case.json")) != _hash(_public(case)):
            raise ValueError("frozen researcher case hash does not match")
        for item in case["corpus"]:
            path = pack / "corpus" / f"{item['id']}.txt"
            if path.is_symlink() or _digest(path.read_bytes()) != item["hash"] or _digest(item["text"].encode()) != item["hash"]:
                raise ValueError("frozen corpus hash does not match")
    return root, manifest


def _pair(manifest, pair_id):
    result = next((p for p in manifest["pairs"] if p["id"] == pair_id), None)
    if result is None:
        raise ValueError("unknown planned pair")
    return result


def _paths(root, pair_id, variant):
    if variant not in VARIANTS:
        raise ValueError("variant must be a or b")
    run, record = root / "runs" / pair_id / variant, root / "records" / pair_id / variant
    if run.resolve() != run or record.resolve() != record:
        raise ValueError("each planned run and record directory must be unique, without path aliases")
    return run, record


def prepare(ws, evaluation, cal, config_path, output):
    source = Path(config_path).resolve()
    config = cal._read_json(source)
    cal._version(config)
    if not cal._id(config.get("id")) or set(config.get("variants", {})) != set(VARIANTS):
        raise ValueError("paired config requires an id and variants a/b")
    common = _configuration(cal, config.get("run_config"))
    repeats, seed = config.get("repeats"), config.get("seed", 0)
    if type(repeats) is not int or not 1 <= repeats <= 5 or type(seed) is not int:
        raise ValueError("declare repeats (1-5) and an integer seed")
    entries = config.get("cases")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 10:
        raise ValueError("paired config requires 1-10 cases")
    reviewers = config.get("reviewers")
    if not isinstance(reviewers, list) or len(reviewers) != 2:
        raise ValueError("predeclare exactly two reviewer slots")
    frozen_reviewers = {}
    for reviewer in reviewers:
        if (not isinstance(reviewer, dict) or not cal._id(reviewer.get("id"))
                or reviewer["id"] in frozen_reviewers or set(reviewer) != {"id", "config", "calibration"}
                or reviewer["config"] is not None and not isinstance(reviewer["config"], dict)):
            raise ValueError("reviewers require distinct IDs, config object/null and calibration path/null")
        basis = None
        if reviewer["calibration"] is not None:
            path = (source.parent / reviewer["calibration"]).resolve()
            basis = cal._read_json(path)
            if basis.get("passed") is not True or _hash(basis.get("reviewer_config")) != _hash(reviewer["config"]):
                raise ValueError("reviewer calibration must pass with the declared reviewer configuration")
            basis = {"sha256": _digest(path.read_bytes()), "result": basis}
        frozen_reviewers[reviewer["id"]] = {"config": reviewer["config"], "calibration": basis,
            "calibration_status": "unavailable" if basis is None else
                                  "passed_config_unknown" if reviewer["config"] is None else "passed_declared_config"}
    cases, packs = {}, {}
    for entry in entries:
        path = (source.parent / entry["case"]).resolve()
        cal._read_json(path)
        _, case, corpus, _ = evaluation._case_material(str(path))
        if not cal._id(case["id"]) or case["id"] in cases:
            raise ValueError("paired case IDs must be safe and unique")
        budget = case.get("budget", {})
        if (any(type(budget.get(k)) is not int or not 0 <= budget[k] <= 100 for k in ("search_calls", "source_reads"))
                or type(budget.get("network_calls")) is not int or budget["network_calls"] != 0):
            raise ValueError("paired cases require explicit search/read budgets and network_calls=0")
        rubric_path = (source.parent / entry["rubric"]).resolve()
        rubric = cal._read_json(rubric_path)
        cal._version(rubric)
        criteria = rubric.get("criteria")
        ids = set()
        if not isinstance(criteria, list) or not 1 <= len(criteria) <= 50:
            raise ValueError("paired rubric requires 1-50 atomic criteria")
        for item in criteria:
            if (not isinstance(item, dict) or set(item) != {"id", "requirement", "reference", "source_ids", "critical"}
                    or not cal._id(item["id"]) or item["id"] in ids or not cal._nonempty(item["requirement"])
                    or not cal._nonempty(item["reference"]) or type(item["critical"]) is not bool
                    or not isinstance(item["source_ids"], list) or not item["source_ids"]
                    or any(not isinstance(s, str) for s in item["source_ids"])
                    or len(set(item["source_ids"])) != len(item["source_ids"])
                    or not set(item["source_ids"]) <= {s["id"] for s in corpus}):
                raise ValueError("atomic criteria require unique IDs, requirement, reference, sources and critical boolean")
            ids.add(item["id"])
        cases[case["id"]] = {"id": case["id"], "question": case["question"], "budget": budget,
            "case_sha256": _digest(path.read_bytes()), "rubric_sha256": _digest(rubric_path.read_bytes()),
            "original_evaluator_sha256": _digest((path.parent / case["evaluator"]).read_bytes()) if case.get("evaluator") else None,
            "corpus": [{k: s[k] for k in ("id", "hash", "text")} for s in corpus], "criteria": criteria}
        if sum(len(s["text"].encode()) for s in corpus) > 500_000:
            raise ValueError("combined fixed corpus exceeds 500000 bytes")
        packs[case["id"]] = path
    destination = Path(output).resolve()
    if destination.exists():
        raise FileExistsError("paired output must be a new directory")
    variant_sources = {v: (source.parent / p).resolve() for v, p in config["variants"].items()}
    if any(destination.is_relative_to(p) for p in variant_sources.values()):
        raise ValueError("paired output must be outside variant source directories")
    inventories = {v: _files(p, source=True) for v, p in variant_sources.items()}
    rng, pairs = random.Random(seed), []
    for case_id in cases:
        first = rng.sample(list(VARIANTS), 2)
        for index in range(repeats):
            pairs.append({"id": f"{case_id}-r{index + 1}", "case_id": case_id, "repeat": index + 1,
                          "order": first if index % 2 == 0 else list(reversed(first))})
    rng.shuffle(pairs)
    manifest = {"schema_version": 1, "id": config["id"], "created_at": _now(), "seed": seed,
                "variants": {v: {"files": inv, "resource_hash": _hash(inv)} for v, inv in inventories.items()},
                "resource_scope": "SKILL.md, requirements.txt, scripts, references, agents, assets; excludes tests/evals and hidden/runtime files",
                "run_config": common, "run_config_hash": _hash(common), "recorder_toolchain": _toolchain(),
                "cases": cases, "reviewers": frozen_reviewers, "pairs": pairs,
                "planned_pairs": len(pairs), "planned_runs": len(pairs) * 2, "limits": LIMITS}
    manifest["manifest_hash"] = _hash(manifest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".paired-", dir=destination.parent))
    try:
        for variant, inventory in inventories.items():
            for name, digest in inventory.items():
                target = staging / "researcher" / "variants" / variant / name
                target.parent.mkdir(parents=True, exist_ok=True)
                raw = (variant_sources[variant] / name).read_bytes()
                if _digest(raw) != digest:
                    raise ValueError("skill resources changed during freeze")
                target.write_bytes(raw)
        for case_id, path in packs.items():
            evaluation.pack_case(ws, str(path), str(staging / "researcher" / "cases" / case_id))
        _store(ws, cal, staging / "manifest.json", manifest)
        _load(cal, staging)
        if destination.exists():
            raise FileExistsError("paired output already exists")
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"study_dir": str(destination), "manifest_hash": manifest["manifest_hash"],
            "planned_pairs": len(pairs), "planned_runs": len(pairs) * 2, "pairs": pairs, "limits": LIMITS}


def _contexts(cal, root):
    values = []
    for pattern in ("runs/*/*/start.json", "reviews/*/*/receipt.json"):
        for path in root.glob(pattern):
            values.append(_read_sealed(cal, path)["context_id"])
    return values


def start(ws, cal, directory, pair_id, variant, context_id, config_path):
    root, manifest = _load(cal, directory)
    pair = _pair(manifest, pair_id)
    run, record = _paths(root, pair_id, variant)
    actual = _configuration(cal, cal._read_json(config_path))
    if not cal._nonempty(context_id) or _hash(actual) != manifest["run_config_hash"]:
        raise ValueError("declare a fresh context and the exact frozen run configuration")
    with _lock(root):
        path = run / "start.json"
        if path.exists():
            _, _, existing = _started(cal, root, manifest, pair_id, variant)
            if existing["context_id"] != context_id or existing["run_config_hash"] != _hash(actual):
                raise ValueError("run already assigned to a different context/configuration")
        else:
            if context_id in _contexts(cal, root):
                raise ValueError("each researcher and reviewer requires a distinct fresh context")
            if variant == pair["order"][1] and not (root / "records" / pair_id / pair["order"][0] / "finish.json").exists():
                raise ValueError("follow the predeclared within-pair run order")
            if run.exists():
                raise ValueError("run directory must be new and unique")
            run.mkdir(parents=True)
            _store(ws, cal, path, _sealed({"schema_version": 1, "manifest_hash": manifest["manifest_hash"],
                "pair_id": pair_id, "variant": variant, "context_id": context_id, "run": str(run),
                "resource_hash": manifest["variants"][variant]["resource_hash"],
                "run_config": actual, "run_config_hash": _hash(actual), "started_at": _now()}))
    return {"run_dir": str(run), "skill_dir": str(root / "researcher" / "variants" / variant),
            "case": str(root / "researcher" / "cases" / pair["case_id"] / "case.json"),
            "config": manifest["run_config"], "note": "Use only this skill, case pack and run in a fresh context. "
            "Keep manifest, evaluators, other runs and reviewer materials outside the researcher's inputs."}


def _started(cal, root, manifest, pair_id, variant):
    run, record = _paths(root, pair_id, variant)
    value = _read_sealed(cal, run / "start.json")
    if (value["manifest_hash"] != manifest["manifest_hash"] or value["run"] != str(run)
            or value["pair_id"] != pair_id or value["variant"] != variant
            or value["resource_hash"] != manifest["variants"][variant]["resource_hash"]
            or value["run_config_hash"] != manifest["run_config_hash"]
            or _hash(value["run_config"]) != manifest["run_config_hash"]):
        raise ValueError("run start does not match the frozen assignment/configuration")
    return run, record, value


def run_tool(ws, cal, directory, pair_id, variant, arguments):
    root, manifest = _load(cal, directory)
    pair = _pair(manifest, pair_id)
    arguments = list(arguments)
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if (not arguments or arguments[0] not in OFFLINE_COMMANDS
            or any(arg == "--corpus" or arg.startswith("--corpus=") for arg in arguments)):
        raise ValueError("use an offline research command; paired-tool supplies the frozen corpus")
    with _lock(root):
        run, record, _ = _started(cal, root, manifest, pair_id, variant)
        if (record / "finish.json").exists():
            raise ValueError("run has already finished")
        if arguments[0] in {"search", "read"}:
            arguments += ["--corpus", str(root / "researcher" / "cases" / pair["case_id"] / "case.json")]
        environment = {**os.environ, "DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": str(run),
            "DRBENCH_SOURCE_POLICY": "local_only", "DEEP_RESEARCH_SOURCE_POLICY": "local_only",
            "DRBENCH_MAX_SEARCH": "", "DRBENCH_MAX_READ": "", "DRBENCH_MAX_REQUESTS": "",
            "DEEP_RESEARCH_MAX_SEARCH": "0", "DEEP_RESEARCH_MAX_READ": "0", "DEEP_RESEARCH_MAX_REQUESTS": "0"}
        started = time.monotonic()
        try:
            result = subprocess.run([sys.executable, "-B", str(root / "researcher" / "variants" / variant / "scripts/research.py"),
                                     *arguments], cwd=run, env=environment, capture_output=True, text=True, timeout=40)
            returncode, stdout, stderr = result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            returncode, stdout, stderr = 124, "", "offline research command exceeded 40 seconds"
        commands = cal._read_json(run / "commands.json") if (run / "commands.json").exists() else []
        commands.append({"arguments": arguments, "returncode": returncode,
                         "elapsed_cli_seconds": time.monotonic() - started, "completed_at": _now()})
        ws._write_json(run / "commands.json", commands)
    try:
        output = json.loads(stdout)
    except ValueError:
        output = stdout
    return {"returncode": returncode, "tool_output": output, "stderr": stderr[:4000]}


def _measurements(cal, path):
    empty = {name: None for name in MEASURES}
    if path is None:
        return {"scope": "whole_research_run", "source": None, **empty}
    value = cal._read_json(path)
    if (not isinstance(value, dict) or set(value) != {"scope", "source", *MEASURES}
            or value["scope"] != "whole_research_run"):
        raise ValueError("measurements require whole_research_run scope, source and three explicit measurement fields")
    for name in MEASURES:
        number = value[name]
        if number is not None and (type(number) not in {int, float} or not math.isfinite(number) or number < 0
                                   or name == "model_tokens" and type(number) is not int):
            raise ValueError("measured values must be finite nonnegative numbers; tokens must be an integer")
    if value["source"] is not None and not cal._nonempty(value["source"]):
        raise ValueError("measurement source must be a nonempty string or null")
    if any(value[name] is not None for name in MEASURES) and not cal._nonempty(value["source"]):
        raise ValueError("known measurements require their actual provenance source")
    return value


def _trace(cal, run, case, *, required):
    path = run / ".deep-research" / "corpus-trace.json"
    if not path.exists():
        if required:
            raise ValueError("completed research requires a fixed corpus trace")
        return None
    trace = cal._read_json(path)
    expected = {"case_id": case["id"], "question_hash": _digest(case["question"].encode()),
                "corpus_hash": _hash({s["id"]: s["hash"] for s in case["corpus"]}), "budget": case["budget"]}
    if any(_hash(trace.get(k)) != _hash(v) for k, v in expected.items()):
        raise ValueError("run trace does not match frozen case, corpus or budget")
    operations = trace.get("operations")
    if type(trace.get("schema_version")) is not int or trace["schema_version"] != 1 or trace.get("scope") != "fixed_corpus_tools_only":
        raise ValueError("require a versioned fixed corpus tool trace")
    if not isinstance(operations, list) or required and not operations:
        raise ValueError("completed research requires recorded corpus operations")
    if required and not any(o.get("kind") == "read" and o.get("state") == "completed" for o in operations):
        raise ValueError("completed research requires at least one successful corpus Read")
    for operation in operations:
        allowed_states = {"completed", "error"} if required else {"completed", "error", "pending"}
        if operation.get("kind") not in {"search", "read"} or operation.get("state") not in allowed_states:
            raise ValueError("trace contains unknown or pending corpus operations")
        source_ids = {s["id"] for s in case["corpus"]}
        if operation["kind"] == "read" and operation.get("source_id") not in source_ids:
            raise ValueError("trace read requires a known corpus source ID")
        seconds = operation.get("elapsed_seconds")
        if operation["state"] == "pending" and seconds is None:
            continue
        if type(seconds) not in {int, float} or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("trace requires measured nonnegative tool seconds")
        if operation["kind"] == "search" and operation["state"] == "completed":
            found = operation.get("source_ids")
            if not isinstance(found, list) or any(not isinstance(s, str) or s not in source_ids for s in found):
                raise ValueError("trace search requires valid corpus source IDs")
    if any(sum(op["kind"] == kind for op in operations) > case["budget"][key]
           for kind, key in (("search", "search_calls"), ("read", "source_reads"))):
        raise ValueError("fixed corpus trace exceeds the frozen budget")
    return trace


def _finished(cal, root, manifest, pair_id, variant):
    run, record = _paths(root, pair_id, variant)
    path = record / "finish.json"
    if not path.exists():
        return None
    value = _read_sealed(cal, path)
    _, _, started = _started(cal, root, manifest, pair_id, variant)
    if value["manifest_hash"] != manifest["manifest_hash"] or value["start_hash"] != started["record_hash"]:
        raise ValueError("finish receipt does not match its planned run")
    for name, digest in value["artifacts"].items():
        if _digest((run / name).read_bytes()) != digest:
            raise ValueError("finished run artifact hash does not match")
    if value["report_hash"] is not None and _digest((record / "report.md").read_bytes()) != value["report_hash"]:
        raise ValueError("frozen report hash does not match")
    return value


def finish(ws, cal, directory, pair_id, variant, *, status, reason, report_path=None, measurements_path=None):
    root, manifest = _load(cal, directory)
    pair = _pair(manifest, pair_id)
    if status not in {"completed", "budget_exhausted", "failed"} or not cal._nonempty(reason):
        raise ValueError("finish requires completed/budget_exhausted/failed and an explicit stop reason")
    with _lock(root):
        run, record, started = _started(cal, root, manifest, pair_id, variant)
        text, digest, report_rel = None, None, None
        if report_path is not None:
            report = Path(report_path).resolve()
            if not report.is_relative_to(run):
                raise ValueError("report must belong to this unique run directory")
            raw = report.read_bytes()
            text = raw.decode("utf-8")
            if not text.strip() or len(raw) > 500_000 or len(text) > manifest["run_config"]["max_report_characters"]:
                raise ValueError("report is empty or exceeds the frozen character/byte limit")
            digest, report_rel = _digest(raw), report.relative_to(run).as_posix()
        if status == "completed" and text is None:
            raise ValueError("completed research requires a report")
        trace = _trace(cal, run, manifest["cases"][pair["case_id"]], required=status == "completed")
        measured = _measurements(cal, measurements_path)
        existing = _finished(cal, root, manifest, pair_id, variant)
        if existing is not None:
            if (existing["status"], existing["reason"], existing["report_hash"], _hash(existing["measurements"])) != (
                    status, reason, digest, _hash(measured)):
                raise ValueError("immutable finish receipt differs")
            return existing
        artifacts = {name: _digest((run / name).read_bytes()) for name in
                     ("commands.json", ".deep-research/corpus-trace.json", ".adapter-counts.json") if (run / name).exists()}
        if report_rel is not None:
            artifacts[report_rel] = digest
        commands = cal._read_json(run / "commands.json") if (run / "commands.json").exists() else []
        operations = trace["operations"] if trace else []
        value = _sealed({"schema_version": 1, "manifest_hash": manifest["manifest_hash"],
            "start_hash": started["record_hash"], "pair_id": pair_id, "variant": variant,
            "status": status, "reason": reason, "finished_at": _now(), "report_hash": digest,
            "report_length": None if text is None else {"characters": len(text), "utf8_bytes": len(text.encode())},
            "artifacts": artifacts, "measurements": measured,
            "cli_usage": {"calls": len(commands), "elapsed_cli_seconds": sum(c["elapsed_cli_seconds"] for c in commands),
                          "scope": "recorded_cli_subprocesses_only"},
            "corpus_usage": None if trace is None else {"search_calls": sum(o["kind"] == "search" for o in operations),
                "source_reads": sum(o["kind"] == "read" for o in operations),
                "elapsed_tool_seconds": sum(o["elapsed_seconds"] for o in operations)
                    if all(o.get("elapsed_seconds") is not None for o in operations) else None,
                "scope": "fixed_corpus_tools_only"},
            "protocol_span_seconds": (datetime.fromisoformat(_now()) - datetime.fromisoformat(started["started_at"])).total_seconds()})
        if text is not None:
            ws._atomic_write(record / "report.md", text)
        _store(ws, cal, record / "finish.json", value)
        return value


def _packets(cal, root, manifest, pair_id):
    pair = _pair(manifest, pair_id)
    case = manifest["cases"][pair["case_id"]]
    runs = {v: _finished(cal, root, manifest, pair_id, v) for v in VARIANTS}
    if any(r is None or r["report_hash"] is None for r in runs.values()):
        raise ValueError("both planned runs require frozen reports before blind review")
    order = random.Random(manifest["seed"] + manifest["pairs"].index(pair)).sample(list(VARIANTS), 2)
    values = {}
    for index, (reviewer, identity) in enumerate(manifest["reviewers"].items()):
        labels = dict(zip(("A", "B"), order if index == 0 else reversed(order)))
        reports = {label: {"text": (root / "records" / pair_id / v / "report.md").read_bytes().decode("utf-8"),
                           "report_hash": runs[v]["report_hash"]} for label, v in labels.items()}
        packet = {"schema_version": 1, "question": case["question"], "corpus": case["corpus"],
                  "criteria": case["criteria"], "reports": reports,
                  "instructions": {**cal.REVIEW_INSTRUCTIONS,
                      "preference": "First assess each atomic factual item for both reports, then compare decision usefulness. "
                                    "Choose A/B/tie with a reason; length/repetition alone earn no factual credit. "
                                    "Do not consult another reviewer or previous judgments."},
                  "reviewer_slot": reviewer, "reviewer_config": identity["config"]}
        packet["packet_hash"] = _hash(packet)
        template = {"schema_version": 1, "packet_hash": packet["packet_hash"], "reviewer": reviewer,
                    "reviewer_config": identity["config"], "context_id": "",
                    "reports": {label: {"report_hash": report["report_hash"], "criteria": {
                        c["id"]: {"verdict": "", "support_origin": "", "reason": "", "report_quote": "", "source_ids": []}
                        for c in case["criteria"]}} for label, report in reports.items()}, "preferred": "", "reason": ""}
        values[reviewer] = (packet, template, {"labels": labels, "run_hashes": {v: r["record_hash"] for v, r in runs.items()}})
    return values


def blind(ws, cal, directory, pair_id):
    root, manifest = _load(cal, directory)
    with _lock(root):
        packets = _packets(cal, root, manifest, pair_id)
        paths, templates = [], []
        for reviewer, (packet, template, key) in packets.items():
            destination = root / "comparisons" / pair_id / reviewer
            _store(ws, cal, destination / "blind.json", packet)
            _store(ws, cal, destination / "judgments.template.json", template)
            _store(ws, cal, destination / "key.json", key)
            paths.append(str(destination / "blind.json"))
            templates.append(str(destination / "judgments.template.json"))
    return {"blind_packets": paths, "judgment_templates": templates,
            "note": "Give each reviewer only their blind packet/template in a new independent context. Keep keys, manifest and other judgments private."}


def _review_result(cal, packet, key, value):
    if (type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("packet_hash") != packet["packet_hash"] or value.get("reviewer") != packet["reviewer_slot"]
            or _hash(value.get("reviewer_config")) != _hash(packet["reviewer_config"])
            or not cal._nonempty(value.get("context_id")) or set(value.get("reports", {})) != {"A", "B"}):
        raise ValueError("review requires the exact packet hash, reviewer configuration, reports and fresh context")
    if value.get("preferred") not in {"A", "B", "tie"} or not cal._nonempty(value.get("reason")):
        raise ValueError("choose A/B/tie with a decision-usefulness reason after factual assessment")
    rows = {}
    for label, report in packet["reports"].items():
        ratings = cal._judged(value["reports"][label], report, [c["id"] for c in packet["criteria"]],
                              {s["id"] for s in packet["corpus"]})
        rows[key["labels"][label]] = {"criteria": ratings, "expected_items": len(ratings),
            "assessed_items": sum(r["verdict"] != "not_assessable" for r in ratings.values()),
            "supported_items": sum(r["verdict"] == "supported" for r in ratings.values()),
            "critical_errors": [c["id"] for c in packet["criteria"] if c["critical"] and
                                ratings[c["id"]]["verdict"] in {"refuted", "insufficient_evidence"}],
            "critical_omissions": [c["id"] for c in packet["criteria"] if c["critical"] and ratings[c["id"]]["verdict"] == "missing"]}
    return {"reports": rows, "fully_assessed": all(r["assessed_items"] == r["expected_items"] for r in rows.values()),
            "preferred_variant": "tie" if value["preferred"] == "tie" else key["labels"][value["preferred"]],
            "reason": value["reason"]}


def _check_packet(cal, root, pair_id, reviewer, packet, key):
    destination = root / "comparisons" / pair_id / reviewer
    if (_hash(cal._read_json(destination / "blind.json")) != _hash(packet)
            or _hash(cal._read_json(destination / "key.json")) != _hash(key)):
        raise ValueError("blind packet or private assignment hash does not match")


def review(ws, cal, directory, pair_id, reviewer_id, judgments_path):
    root, manifest = _load(cal, directory)
    with _lock(root):
        packets = _packets(cal, root, manifest, pair_id)
        if reviewer_id not in packets:
            raise ValueError("unknown predeclared reviewer")
        packet, _, key = packets[reviewer_id]
        _check_packet(cal, root, pair_id, reviewer_id, packet, key)
        value = cal._read_json(judgments_path)
        result = _review_result(cal, packet, key, value)
        digest = _hash(value)
        target = root / "reviews" / pair_id / reviewer_id
        receipt = target / "receipt.json"
        if receipt.exists():
            old = _read_sealed(cal, receipt)
            if old["judgment_hash"] != digest:
                raise ValueError("reviewer slot already contains an immutable independent judgment")
            if _hash(cal._read_json(target / f"{digest}.json")) != digest or _hash(old["result"]) != _hash(result):
                raise ValueError("saved review hash does not match")
            return old
        if value["context_id"] in _contexts(cal, root):
            raise ValueError("each researcher and reviewer requires a distinct fresh context")
        _store(ws, cal, target / f"{digest}.json", value)
        saved = _sealed({"schema_version": 1, "manifest_hash": manifest["manifest_hash"],
            "packet_hash": packet["packet_hash"], "reviewer": reviewer_id, "context_id": value["context_id"],
            "judgment_hash": digest, "result": result})
        _store(ws, cal, receipt, saved)
        return saved


def _costs(records, expected):
    result = {}
    for name in MEASURES:
        known = [r["measurements"][name] for r in records if r is not None and r["measurements"][name] is not None]
        result[name] = {"expected_runs": expected, "measured_runs": len(known),
                        "observed_sum": sum(known) if known else None,
                        "total": sum(known) if len(known) == expected else None}
    return result


def summary(ws, cal, directory):
    root, manifest = _load(cal, directory)
    pairs, all_records = [], []
    for pair in manifest["pairs"]:
        pid = pair["id"]
        runs = {v: _finished(cal, root, manifest, pid, v) for v in VARIANTS}
        all_records.extend(runs.values())
        reviews = {}
        for reviewer in manifest["reviewers"]:
            path = root / "reviews" / pid / reviewer / "receipt.json"
            if path.exists():
                record = _read_sealed(cal, path)
                packet, _, key = _packets(cal, root, manifest, pid)[reviewer]
                _check_packet(cal, root, pid, reviewer, packet, key)
                value = cal._read_json(path.parent / f"{record['judgment_hash']}.json")
                result = _review_result(cal, packet, key, value)
                if (record["manifest_hash"] != manifest["manifest_hash"] or record["packet_hash"] != packet["packet_hash"]
                        or _hash(value) != record["judgment_hash"] or _hash(result) != _hash(record["result"])):
                    raise ValueError("saved independent review does not match frozen inputs")
                reviews[reviewer] = result
        completed = all(r is not None and r["status"] == "completed" for r in runs.values())
        fully = completed and len(reviews) == 2 and all(r["fully_assessed"] for r in reviews.values())
        preferred = [r["preferred_variant"] for r in reviews.values()]
        consensus = (preferred[0] if len(set(preferred)) == 1 else "disagreement") if fully else (
                     "pending" if len(reviews) < 2 else "incomplete")
        disagreements = []
        if len(reviews) == 2:
            first, second = reviews.values()
            for variant in VARIANTS:
                for cid, rating in first["reports"][variant]["criteria"].items():
                    other = second["reports"][variant]["criteria"][cid]
                    if cal._verdict(rating) != cal._verdict(other):
                        disagreements.append({"variant": variant, "criterion_id": cid,
                                              "judgments": {name: r["reports"][variant]["criteria"][cid]
                                                            for name, r in reviews.items()}})
        pairs.append({**pair, "runs": runs, "reviews": reviews, "completed": completed, "fully_assessed": fully,
                      "consensus": consensus, "factual_disagreements": disagreements, "costs": _costs(list(runs.values()), 2)})
    by_case = {}
    for case_id in manifest["cases"]:
        group = [p for p in pairs if p["case_id"] == case_id]
        by_case[case_id] = {"planned_pairs": len(group), "completed_pairs": sum(p["completed"] for p in group),
            "fully_assessed_pairs": sum(p["fully_assessed"] for p in group),
            "full_assessment_rate": sum(p["fully_assessed"] for p in group) / len(group),
            "consensus": {outcome: sum(p["consensus"] == outcome for p in group)
                          for outcome in ("a", "b", "tie", "disagreement", "pending", "incomplete")},
            "reviewer_preferences": {reviewer: {outcome: sum(p["completed"] and reviewer in p["reviews"]
                and p["reviews"][reviewer]["fully_assessed"] and p["reviews"][reviewer]["preferred_variant"] == outcome
                for p in group) for outcome in ("a", "b", "tie")} for reviewer in manifest["reviewers"]},
            "critical_errors": {reviewer: {variant: [
                {"pair_id": p["id"], "criterion_id": cid} for p in group if reviewer in p["reviews"]
                for cid in p["reviews"][reviewer]["reports"][variant]["critical_errors"]]
                for variant in VARIANTS} for reviewer in manifest["reviewers"]},
            "critical_omissions": {reviewer: {variant: [
                {"pair_id": p["id"], "criterion_id": cid} for p in group if reviewer in p["reviews"]
                for cid in p["reviews"][reviewer]["reports"][variant]["critical_omissions"]]
                for variant in VARIANTS} for reviewer in manifest["reviewers"]},
            "report_lengths": {variant: {"expected_reports": len(group),
                "observed_reports": sum(p["runs"][variant] is not None and p["runs"][variant]["report_length"] is not None for p in group),
                "reports": [{"pair_id": p["id"], **p["runs"][variant]["report_length"]} for p in group
                            if p["runs"][variant] is not None and p["runs"][variant]["report_length"] is not None]}
                for variant in VARIANTS},
            "costs": _costs([r for p in group for r in p["runs"].values()], 2 * len(group))}
    config = manifest["run_config"]
    result = {"schema_version": 1, "manifest_hash": manifest["manifest_hash"],
              "planned_pairs": len(pairs), "planned_runs": len(pairs) * 2,
              "completed_pairs": sum(p["completed"] for p in pairs),
              "fully_assessed_pairs": sum(p["fully_assessed"] for p in pairs),
              "full_assessment_rate": sum(p["fully_assessed"] for p in pairs) / len(pairs),
              "expected_fact_items": sum(len(manifest["cases"][p["case_id"]]["criteria"]) * 4 for p in pairs),
              "assessed_fact_items": sum(row["assessed_items"] for p in pairs for r in p["reviews"].values() for row in r["reports"].values()),
              "comparability": {"frozen_resources_and_budgets_verified": True,
                  "declared_model_id": config["model_id"], "same_model": True if config["model_id"] else None,
                  "same_reasoning": True if config["reasoning"] is not None else None,
                  "same_settings": True if config["settings"] is not None else None,
                  "identity_verification": "declarations_only", "os_isolation_verified": False},
              "reviewer_calibration": {r: v["calibration_status"] for r, v in manifest["reviewers"].items()},
              "by_case": by_case, "pairs": pairs, "costs": _costs(all_records, len(pairs) * 2), "limits": LIMITS}
    ws._write_json(root / "summary.json", result)
    return result
