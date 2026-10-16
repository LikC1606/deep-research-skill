import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "research.py"
SPEC = importlib.util.spec_from_file_location("paired_evaluation_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


class PairedEvaluationTests(unittest.TestCase):
    """Offline protocol checks; fixture judgments do not measure report quality."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        environment = mock.patch.dict(os.environ, {
            "DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": str(self.folder / "ambient-run"),
            "DRBENCH_SOURCE_POLICY": "local_only", "DEEP_RESEARCH_SOURCE_POLICY": "local_only",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.paired = getattr(research, "paired_evaluation", None)
        self.cases = {}
        for variant in ("a", "b"):
            skill = self.folder / f"variant-{variant}"
            (skill / "scripts").mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: fixture-research\ndescription: Local protocol fixture.\n---\n"
                f"Read the supplied documents. Variant {variant}.\n", encoding="utf-8")
            (skill / "scripts" / "research.py").write_text(
                "import json, os, sys\n"
                "print(json.dumps({'fixture': True, 'arguments': sys.argv[1:], 'cwd': os.getcwd(), "
                "'run': os.environ.get('DEEP_RESEARCH_RUN_DIR'), "
                "'source_policy': os.environ.get('DEEP_RESEARCH_SOURCE_POLICY')}))\n",
                encoding="utf-8")
        case_configs = []
        for index, count in enumerate((12, 16, 20), 1):
            case_id = f"trial-{index}"
            case_dir = self.folder / case_id
            case_dir.mkdir()
            text = f"The study enrolled {count} participants.\n"
            (case_dir / "source.txt").write_text(text, encoding="utf-8")
            case = {
                "id": case_id, "question": f"State and double the enrollment for trial {index}.",
                "budget": {"network_calls": 0, "search_calls": 2, "source_reads": 2},
                "checklist": [{"id": "N1", "requirement": "Enrollment and transparent arithmetic"}],
                "sources": [{"id": "S1", "path": "source.txt"}],
            }
            write_json(case_dir / "case.json", case)
            write_json(case_dir / "rubric.json", {"schema_version": 1, "criteria": [
                {"id": "R-count", "requirement": "State the enrollment.",
                 "reference": f"Enrollment is {count} participants.", "source_ids": ["S1"], "critical": True},
                {"id": "R-double", "requirement": "Double the enrollment with explicit arithmetic.",
                 "reference": f"{count} * 2 = {count * 2} participants.", "source_ids": ["S1"], "critical": False},
            ]})
            self.cases[case_id] = {"case": case, "count": count, "source": text}
            case_configs.append({"case": f"{case_id}/case.json", "rubric": f"{case_id}/rubric.json"})
        self.run_config = {
            "model_id": None, "reasoning": None, "settings": None,
            "tool_versions": {"orchestrator": None}, "max_report_characters": 12000,
            "stop_conditions": ["Stop after covering the requested facts or exhausting the common budget."],
        }
        self.config_data = {
            "schema_version": 1, "id": "small-paired-development", "variants": {"a": "variant-a", "b": "variant-b"},
            "cases": case_configs, "repeats": 2, "seed": 0, "run_config": self.run_config,
            "reviewers": [{"id": reviewer, "config": None, "calibration": None} for reviewer in ("J1", "J2")],
        }
        self.config_path = write_json(self.folder / "config.json", self.config_data)
        self.start_config = write_json(self.folder / "run-config.json", self.run_config)

    def prepare(self, name="plan"):
        self.assertIsNotNone(self.paired, "research must expose the paired_evaluation extension")
        directory = self.folder / name
        self.paired.prepare(research.workspace, research.evaluation, research.calibration,
                            str(self.config_path), str(directory))
        manifest = read_json(directory / "manifest.json")
        return directory, manifest

    def start(self, directory, pair, variant=None, context=None, config_path=None):
        variant = variant or pair["order"][0]
        return self.paired.start(
            research.workspace, research.calibration, str(directory), pair["id"], variant,
            context or f"writer-{pair['id']}-{variant}", str(config_path or self.start_config))

    def run_dir(self, directory, pair, variant):
        return directory / "runs" / pair["id"] / variant

    def frozen_case(self, directory, pair):
        return directory / "researcher" / "cases" / pair["case_id"] / "case.json"

    def trace(self, directory, pair, variant, operations=None):
        path = self.frozen_case(directory, pair)
        case = read_json(path)
        hashes = {source["id"]: digest((path.parent / source["path"]).read_bytes())
                  for source in case["sources"]}
        if operations is None:
            operations = [{"kind": "read", "source_id": "S1", "state": "completed",
                           "ref": "REF-0000000000000001", "snapshot_id": "0" * 64,
                           "at": "2026-10-16T01:00:00+00:00", "elapsed_seconds": 0.01}]
        trace = {
            "schema_version": 1, "case_id": case["id"], "question_hash": digest(case["question"].encode()),
            "corpus_hash": research.calibration._hash(hashes), "budget": case["budget"],
            "operations": operations, "scope": "fixed_corpus_tools_only",
        }
        return write_json(self.run_dir(directory, pair, variant) / ".deep-research" / "corpus-trace.json", trace)

    def report(self, directory, pair, variant):
        count = self.cases[pair["case_id"]]["count"]
        path = self.run_dir(directory, pair, variant) / "submitted-report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"The study enrolled {count} participants. [S1]\n"
                        f"Twice {count} is {count * 2} participants. [S1]\n", encoding="utf-8")
        return path

    def finish(self, directory, pair, variant, *, report_path=None, measurements=None, status="completed"):
        return self.paired.finish(
            research.workspace, research.calibration, str(directory), pair["id"], variant,
            status=status, reason="Disposable offline protocol fixture.",
            report_path=str(report_path) if report_path is not None else None,
            measurements_path=str(measurements) if measurements is not None else None)

    def complete_pair(self, directory, pair, measurements=None):
        reports = {}
        for variant in pair["order"]:
            self.start(directory, pair, variant)
            self.trace(directory, pair, variant)
            reports[variant] = self.report(directory, pair, variant)
            measurement_path = None
            if measurements is not None:
                measurement_path = write_json(self.run_dir(directory, pair, variant) / "measurements.json", measurements)
            self.finish(directory, pair, variant, report_path=reports[variant], measurements=measurement_path)
        return reports

    def blind(self, directory, pair):
        return self.paired.blind(research.workspace, research.calibration, str(directory), pair["id"])

    def judgments(self, prepared, index=0, *, preferred="tie", not_assessable=False):
        packet = read_json(prepared["blind_packets"][index])
        value = read_json(prepared["judgment_templates"][index])
        pair_id = Path(prepared["blind_packets"][index]).parent.parent.name
        value.update(reviewer=f"J{index + 1}", reviewer_config=None,
                     context_id=f"judge-{index + 1}-{pair_id}-{packet['packet_hash'][:8]}",
                     preferred=preferred, reason="Fixture usefulness judgment, not a model quality measurement.")
        for label, report in packet["reports"].items():
            for criterion_id, judgment in value["reports"][label]["criteria"].items():
                judgment.update(
                    verdict="not_assessable" if not_assessable else "supported",
                    support_origin="not_assessed" if not_assessable else (
                        "derived" if criterion_id == "R-double" else "source_stated"),
                    reason="Compared the complete assertion with the supplied source.",
                    report_quote="" if not_assessable else report["text"].splitlines()[
                        1 if criterion_id == "R-double" else 0],
                    source_ids=[] if not_assessable else ["S1"],
                )
        return value

    def review(self, directory, pair, reviewer, value, name=None):
        path = write_json(self.folder / (name or f"submission-{pair['id']}-{reviewer}.json"), value)
        return self.paired.review(research.workspace, research.calibration, str(directory),
                                  pair["id"], reviewer, str(path))

    def test_prepare_freezes_six_slots_and_counterbalances_repeat_order(self):
        directory, manifest = self.prepare()
        self.assertRegex(manifest["manifest_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(manifest["pairs"]), 6)
        self.assertEqual(len({pair["id"] for pair in manifest["pairs"]}), 6)
        for case_id in self.cases:
            pairs = [pair for pair in manifest["pairs"] if pair["case_id"] == case_id]
            self.assertEqual(len(pairs), 2)
            self.assertEqual(len({pair["repeat"] for pair in pairs}), 2)
            self.assertEqual({tuple(pair["order"]) for pair in pairs}, {("a", "b"), ("b", "a")})
        original = (directory / "manifest.json").read_bytes()
        with self.assertRaises((FileExistsError, ValueError)):
            self.prepare()
        self.assertEqual((directory / "manifest.json").read_bytes(), original)

    def test_plan_uses_frozen_inputs_after_original_resources_change(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        (self.folder / "variant-a" / "SKILL.md").write_text("Changed original skill.", encoding="utf-8")
        (self.folder / pair["case_id"] / "source.txt").write_text("Changed original source.", encoding="utf-8")
        self.start(directory, pair)
        frozen = read_json(self.frozen_case(directory, pair))
        self.assertEqual((self.frozen_case(directory, pair).parent / frozen["sources"][0]["path"]).read_text(),
                         self.cases[pair["case_id"]]["source"])

    def test_changed_frozen_skill_is_rejected_before_start(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        variant = pair["order"][0]
        (directory / "researcher" / "variants" / variant / "SKILL.md").write_text("Changed frozen skill.")
        with self.assertRaises(ValueError):
            self.start(directory, pair)

    def test_changed_frozen_corpus_is_rejected_before_start(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        path = self.frozen_case(directory, pair)
        case = read_json(path)
        (path.parent / case["sources"][0]["path"]).write_text("Changed frozen corpus.")
        with self.assertRaises(ValueError):
            self.start(directory, pair)

    def test_changed_manifest_is_rejected_before_start(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        manifest["run_config"]["stop_conditions"].append("Skip missing facts.")
        write_json(directory / "manifest.json", manifest)
        with self.assertRaises(ValueError):
            self.start(directory, pair)

    def test_run_config_must_match_frozen_values_and_json_types(self):
        self.run_config["settings"] = {"sampling": False}
        write_json(self.config_path, self.config_data)
        write_json(self.start_config, self.run_config)
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        invalid = copy.deepcopy(self.run_config)
        invalid["settings"]["sampling"] = 0
        wrong = write_json(self.folder / "wrong-run-config.json", invalid)
        with self.assertRaises(ValueError):
            self.start(directory, pair, config_path=wrong)
        self.start(directory, pair)

    def test_start_requires_declared_order_and_an_independent_context(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        first, second = pair["order"]
        with self.assertRaises(ValueError):
            self.start(directory, pair, second)
        self.start(directory, pair, first, context="shared-writer")
        with self.assertRaises(ValueError):
            self.start(directory, pair, first, context="another-writer")
        self.finish(directory, pair, first, status="failed")
        with self.assertRaises(ValueError):
            self.start(directory, pair, second, context="shared-writer")
        self.start(directory, pair, second, context="independent-writer")

    def test_start_rejects_a_run_directory_alias_to_the_other_variant(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        first, second = pair["order"]
        self.start(directory, pair, first)
        self.finish(directory, pair, first, status="failed")
        alias = self.run_dir(directory, pair, second)
        if alias.exists():
            alias.rmdir()
        alias.symlink_to(self.run_dir(directory, pair, first), target_is_directory=True)
        for context in (f"writer-{pair['id']}-{first}", "different-context"):
            with self.subTest(context=context), self.assertRaises(ValueError):
                self.start(directory, pair, second, context=context)

    def test_completed_requires_report_and_a_valid_completed_tool_trace(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        variant = pair["order"][0]
        self.start(directory, pair, variant)
        report = self.report(directory, pair, variant)
        with self.assertRaises(ValueError):
            self.finish(directory, pair, variant, report_path=report)
        trace_path = self.trace(directory, pair, variant)
        original = read_json(trace_path)
        for name, change in (
            ("pending", lambda value: value["operations"][0].update(state="pending")),
            ("wrong-task", lambda value: value.update(question_hash="f" * 64)),
            ("wrong-corpus", lambda value: value.update(corpus_hash="f" * 64)),
            ("over-budget", lambda value: value.update(operations=value["operations"] * 3)),
            ("no-operations", lambda value: value.update(operations=[])),
        ):
            invalid = copy.deepcopy(original)
            change(invalid)
            write_json(trace_path, invalid)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.finish(directory, pair, variant, report_path=report)
        write_json(trace_path, original)
        with self.assertRaises(ValueError):
            self.finish(directory, pair, variant)
        self.finish(directory, pair, variant, report_path=report)

    def test_completed_rejects_trace_operations_that_name_unknown_sources(self):
        for kind in ("read", "search"):
            with self.subTest(kind=kind):
                directory, manifest = self.prepare(f"unknown-source-{kind}")
                pair = manifest["pairs"][0]
                variant = pair["order"][0]
                self.start(directory, pair, variant)
                operation = {"kind": kind, "state": "completed", "elapsed_seconds": 0.01,
                             **({"source_id": "S-UNKNOWN", "ref": "REF-0000000000000001", "snapshot_id": "0" * 64}
                                if kind == "read" else {"query": "enrollment", "source_ids": ["S-UNKNOWN"]})}
                self.trace(directory, pair, variant, [operation])
                with self.assertRaises(ValueError):
                    self.finish(directory, pair, variant, report_path=self.report(directory, pair, variant))

    def test_completed_requires_at_least_one_successful_fixed_corpus_operation(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        variant = pair["order"][0]
        self.start(directory, pair, variant)
        self.trace(directory, pair, variant, [{"kind": "read", "state": "error", "source_id": "S1",
                                               "elapsed_seconds": 0.01, "error": "Disposable reader failure."}])
        with self.assertRaises(ValueError):
            self.finish(directory, pair, variant, report_path=self.report(directory, pair, variant))

    def test_failed_or_budget_exhausted_runs_preserve_pending_trace_with_unknown_tool_time(self):
        for status in ("failed", "budget_exhausted"):
            with self.subTest(status=status):
                directory, manifest = self.prepare(f"pending-{status}")
                pair = manifest["pairs"][0]
                variant = pair["order"][0]
                self.start(directory, pair, variant)
                trace_path = self.trace(directory, pair, variant, [{
                    "kind": "read", "state": "pending", "source_id": "S1",
                    "at": "2026-10-16T01:00:00+00:00",
                }])
                original = trace_path.read_bytes()
                receipt = self.finish(directory, pair, variant, status=status)
                self.assertEqual(receipt["status"], status)
                self.assertEqual(trace_path.read_bytes(), original)
                self.assertIsNone(receipt["corpus_usage"]["elapsed_tool_seconds"])
                summary = self.paired.summary(research.workspace, research.calibration, str(directory))
                self.assertEqual(summary["planned_pairs"], 6)
                self.assertEqual(summary["completed_pairs"], 0)
                self.assertEqual(summary["fully_assessed_pairs"], 0)
                self.start(directory, pair, pair["order"][1])

    def test_completed_rejects_reports_outside_its_own_run_and_output_cap(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        variant = pair["order"][0]
        self.start(directory, pair, variant)
        self.trace(directory, pair, variant)
        outside = self.folder / "outside.md"
        outside.write_text("A report outside its own run.", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.finish(directory, pair, variant, report_path=outside)
        report = self.report(directory, pair, variant)
        original = report.read_text()
        for text in (" ", "x" * 12001):
            report.write_text(text, encoding="utf-8")
            with self.subTest(length=len(text)), self.assertRaises(ValueError):
                self.finish(directory, pair, variant, report_path=report)
        report.write_text(original, encoding="utf-8")
        self.finish(directory, pair, variant, report_path=report)

    def test_finish_preserves_report_snapshot_and_detects_snapshot_changes(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        reports = self.complete_pair(directory, pair)
        variant = pair["order"][0]
        snapshot = directory / "records" / pair["id"] / variant / "report.md"
        original = snapshot.read_bytes()
        self.assertEqual(original, reports[variant].read_bytes())
        reports[variant].write_text("Changed original after completion.", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.blind(directory, pair)
        self.assertEqual(snapshot.read_bytes(), original)
        reports[variant].write_bytes(original)
        self.blind(directory, pair)
        snapshot.write_text("Changed frozen report.", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.blind(directory, pair)

    def test_blind_packets_contain_frozen_reports_and_hide_private_run_details(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        self.complete_pair(directory, pair)
        prepared = self.blind(directory, pair)
        self.assertEqual(len(prepared["blind_packets"]), 2)
        self.assertEqual(len(prepared["judgment_templates"]), 2)
        for packet_path, template_path in zip(prepared["blind_packets"], prepared["judgment_templates"]):
            packet, template = read_json(packet_path), read_json(template_path)
            self.assertEqual(set(packet["reports"]), {"A", "B"})
            self.assertEqual(template["packet_hash"], packet["packet_hash"])
            self.assertNotIn(str(self.folder), json.dumps(packet))
            self.assertNotIn("writer-", json.dumps(packet))
            for label, report in packet["reports"].items():
                self.assertEqual(report["report_hash"], digest(report["text"].encode()))
                self.assertEqual(template["reports"][label]["report_hash"], report["report_hash"])
                self.assertEqual(set(template["reports"][label]["criteria"]), {"R-count", "R-double"})

    def test_blind_report_text_matches_its_frozen_hash_with_crlf_line_endings(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        for variant in pair["order"]:
            self.start(directory, pair, variant)
            self.trace(directory, pair, variant)
            report = self.report(directory, pair, variant)
            report.write_bytes(report.read_bytes().replace(b"\n", b"\r\n"))
            self.finish(directory, pair, variant, report_path=report)
        prepared = self.blind(directory, pair)
        for path in prepared["blind_packets"]:
            for report in read_json(path)["reports"].values():
                self.assertEqual(report["report_hash"], digest(report["text"].encode()))
                self.assertIn("\r\n", report["text"])

    def test_reviews_preserve_two_independent_submissions_and_retry_is_idempotent(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        self.complete_pair(directory, pair)
        prepared = self.blind(directory, pair)
        first, second = self.judgments(prepared, 0), self.judgments(prepared, 1)
        self.review(directory, pair, "J1", first)
        self.review(directory, pair, "J2", second)
        before = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*.json")}
        self.review(directory, pair, "J1", first)
        after = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*.json")}
        self.assertEqual(before, after)
        saved = [read_json(path) for path in directory.rglob("*.json")]
        self.assertIn(first, saved)
        self.assertIn(second, saved)
        changed = copy.deepcopy(first)
        changed["reason"] = "A second conflicting submission from the same reviewer."
        with self.assertRaises(ValueError):
            self.review(directory, pair, "J1", changed, name="changed-submission.json")

    def test_review_rejects_missing_criteria_fabricated_quotes_and_wrong_identity(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        self.complete_pair(directory, pair)
        prepared = self.blind(directory, pair)
        original = self.judgments(prepared)
        changes = (
            ("missing-criterion", lambda value: value["reports"]["A"]["criteria"].pop("R-double")),
            ("wrong-report", lambda value: value["reports"]["A"].update(report_hash="f" * 64)),
            ("wrong-packet", lambda value: value.update(packet_hash="f" * 64)),
            ("wrong-reviewer", lambda value: value.update(reviewer="J2")),
            ("wrong-config", lambda value: value.update(reviewer_config={"model_id": "unfrozen-model"})),
            ("fabricated-quote", lambda value: value["reports"]["A"]["criteria"]["R-count"].update(
                report_quote="This passage was never in the report.")),
            ("unknown-source", lambda value: value["reports"]["A"]["criteria"]["R-count"].update(
                source_ids=["S99"])),
        )
        for name, change in changes:
            invalid = copy.deepcopy(original)
            change(invalid)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.review(directory, pair, "J1", invalid, name=f"{name}.json")
        self.review(directory, pair, "J1", original)

    def test_review_context_must_be_independent_of_writer_and_other_reviewer(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        self.complete_pair(directory, pair)
        prepared = self.blind(directory, pair)
        first = self.judgments(prepared, 0)
        first["context_id"] = f"writer-{pair['id']}-{pair['order'][0]}"
        with self.assertRaises(ValueError):
            self.review(directory, pair, "J1", first)
        first["context_id"] = "independent-judge-context"
        self.review(directory, pair, "J1", first)
        second = self.judgments(prepared, 1)
        second["context_id"] = first["context_id"]
        with self.assertRaises(ValueError):
            self.review(directory, pair, "J2", second)

    def test_unfinished_slots_stay_in_the_summary_denominator_and_are_not_ties(self):
        directory, _ = self.prepare()
        summary = self.paired.summary(research.workspace, research.calibration, str(directory))
        self.assertEqual(summary["planned_pairs"], 6)
        self.assertEqual(summary["planned_runs"], 12)
        self.assertEqual(summary["completed_pairs"], 0)
        self.assertEqual(summary["fully_assessed_pairs"], 0)
        self.assertEqual(summary["full_assessment_rate"], 0)
        self.assertEqual(len(summary["pairs"]), 6)
        for case in summary["by_case"].values():
            self.assertEqual(case["planned_pairs"], 2)
            self.assertEqual(case["consensus"]["pending"], 2)
            self.assertEqual(case["consensus"]["tie"], 0)
        for cost in summary["costs"].values():
            self.assertEqual(cost["expected_runs"], 12)
            self.assertEqual(cost["measured_runs"], 0)
            self.assertIsNone(cost["observed_sum"])
            self.assertIsNone(cost["total"])
        self.assertIsNone(summary["comparability"]["declared_model_id"])
        self.assertIsNone(summary["comparability"]["same_model"])

    def test_one_reviewer_does_not_complete_a_pair_or_create_a_consensus_tie(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        self.complete_pair(directory, pair)
        prepared = self.blind(directory, pair)
        self.review(directory, pair, "J1", self.judgments(prepared))
        summary = self.paired.summary(research.workspace, research.calibration, str(directory))
        self.assertEqual(summary["planned_pairs"], 6)
        self.assertEqual(summary["completed_pairs"], 1)
        self.assertEqual(summary["fully_assessed_pairs"], 0)
        row = next(item for item in summary["pairs"] if item["id"] == pair["id"])
        self.assertEqual(set(row["reviews"]), {"J1"})
        self.assertEqual(row["consensus"], "pending")
        self.assertEqual(summary["by_case"][pair["case_id"]]["consensus"]["tie"], 0)

    def test_all_not_assessable_never_counts_as_a_fully_assessed_tie(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        self.complete_pair(directory, pair)
        prepared = self.blind(directory, pair)
        for index, reviewer in enumerate(("J1", "J2")):
            self.review(directory, pair, reviewer, self.judgments(prepared, index, not_assessable=True))
        summary = self.paired.summary(research.workspace, research.calibration, str(directory))
        row = next(item for item in summary["pairs"] if item["id"] == pair["id"])
        self.assertTrue(row["completed"])
        self.assertFalse(row["fully_assessed"])
        self.assertEqual(row["consensus"], "incomplete")
        case = summary["by_case"][pair["case_id"]]
        self.assertEqual(case["fully_assessed_pairs"], 0)
        self.assertEqual(case["consensus"]["tie"], 0)
        for preferences in case["reviewer_preferences"].values():
            self.assertEqual(preferences, {"a": 0, "b": 0, "tie": 0})

    def test_summary_retains_preference_disagreement_and_critical_factual_errors(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        self.complete_pair(directory, pair)
        prepared = self.blind(directory, pair)
        for index, reviewer in enumerate(("J1", "J2")):
            template_path = Path(prepared["judgment_templates"][index])
            labels = read_json(template_path.parent / "key.json")["labels"]
            preferred = next(label for label, variant in labels.items() if variant == ("a" if index == 0 else "b"))
            value = self.judgments(prepared, index, preferred=preferred)
            if index == 1:
                label = next(label for label, variant in labels.items() if variant == "a")
                value["reports"][label]["criteria"]["R-count"].update(
                    verdict="refuted", support_origin="not_assessed",
                    reason="Synthetic contradictory judgment retained to exercise disagreement reporting.")
            self.review(directory, pair, reviewer, value)
        summary = self.paired.summary(research.workspace, research.calibration, str(directory))
        row = next(item for item in summary["pairs"] if item["id"] == pair["id"])
        self.assertTrue(row["fully_assessed"])
        self.assertEqual(row["consensus"], "disagreement")
        self.assertEqual(len(row["factual_disagreements"]), 1)
        self.assertEqual(row["factual_disagreements"][0]["criterion_id"], "R-count")
        self.assertEqual(row["reviews"]["J2"]["reports"]["a"]["critical_errors"], ["R-count"])
        case = summary["by_case"][pair["case_id"]]
        self.assertEqual(case["reviewer_preferences"]["J1"]["a"], 1)
        self.assertEqual(case["reviewer_preferences"]["J2"]["b"], 1)
        self.assertEqual(case["consensus"]["disagreement"], 1)

    def test_actual_tool_process_uses_frozen_inputs_and_cli_time_is_not_end_to_end_cost(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        for variant in pair["order"]:
            self.start(directory, pair, variant)
            result = self.paired.run_tool(research.workspace, research.calibration, str(directory),
                                          pair["id"], variant, ["read", "S1"])
            self.assertEqual(result["returncode"], 0)
            output = result["tool_output"]
            self.assertTrue(output["fixture"])
            self.assertEqual(output["arguments"], ["read", "S1", "--corpus", str(self.frozen_case(directory, pair))])
            self.assertEqual(Path(output["cwd"]), self.run_dir(directory, pair, variant))
            self.assertEqual(Path(output["run"]), self.run_dir(directory, pair, variant))
            self.assertEqual(output["source_policy"], "local_only")
            self.trace(directory, pair, variant)
            self.finish(directory, pair, variant, report_path=self.report(directory, pair, variant))
        summary = self.paired.summary(research.workspace, research.calibration, str(directory))
        row = next(item for item in summary["pairs"] if item["id"] == pair["id"])
        for variant in ("a", "b"):
            self.assertEqual(row["runs"][variant]["cli_usage"]["calls"], 1)
            self.assertGreater(row["runs"][variant]["cli_usage"]["elapsed_cli_seconds"], 0)
            self.assertGreater(row["runs"][variant]["report_length"]["characters"], 0)
            self.assertIsNone(row["runs"][variant]["measurements"]["end_to_end_seconds"])
        for cost in row["costs"].values():
            self.assertEqual(cost["expected_runs"], 2)
            self.assertEqual(cost["measured_runs"], 0)
            self.assertIsNone(cost["observed_sum"])
            self.assertIsNone(cost["total"])

    def test_partial_measured_costs_report_observed_sum_without_inventing_study_total(self):
        directory, manifest = self.prepare()
        pair = manifest["pairs"][0]
        self.complete_pair(directory, pair, measurements={
            "scope": "whole_research_run", "source": "Disposable explicit platform measurement fixture.",
            "end_to_end_seconds": 10, "model_tokens": 100, "cost_usd": 0.25,
        })
        summary = self.paired.summary(research.workspace, research.calibration, str(directory))
        row = next(item for item in summary["pairs"] if item["id"] == pair["id"])
        for name, expected in (("end_to_end_seconds", 20), ("model_tokens", 200), ("cost_usd", 0.5)):
            cost = summary["costs"][name]
            self.assertEqual(cost["expected_runs"], 12)
            self.assertEqual(cost["measured_runs"], 2)
            self.assertEqual(cost["observed_sum"], expected)
            self.assertIsNone(cost["total"])
            self.assertEqual(row["costs"][name]["total"], expected)


if __name__ == "__main__":
    unittest.main()
