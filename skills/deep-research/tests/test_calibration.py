import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "research.py"
SPEC = importlib.util.spec_from_file_location("calibration_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        env = mock.patch.dict(os.environ, {
            "DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
            "DEEP_RESEARCH_SOURCE_POLICY": "local_only",
        })
        env.start()
        self.addCleanup(env.stop)
        self.calibration = getattr(research, "calibration", None)
        self.anchor_text = ("The study enrolled 12 participants. [S1]\n"
                            "Whether recovery was caused by treatment remains uncertain. [S2]")
        self.error_text = self.anchor_text.replace("12 participants", "99 participants")
        self.control_text = self.anchor_text.replace(
            "The study enrolled 12 participants.", "Twelve participants enrolled in the study.")
        self.brief_text = self.anchor_text.replace(
            "The study enrolled 12 participants.", "Enrollment: 12 participants.")
        sources = ["The study enrolled 12 participants.",
                   "There was no control group. Recovery cannot establish a treatment effect.",
                   "The follow-up lasted three months.", "Enrollment stopped in June.",
                   "The study used a single clinical site.", "No randomized trial was conducted."]
        for index, text in enumerate(sources, 1):
            (self.folder / f"source-{index}.txt").write_text(text, encoding="utf-8")
        self.case = self.folder / "case.json"
        self.case.write_text(json.dumps({
            "id": "small-trial", "question": "What does the trial establish?",
            "checklist": [{"id": "N1", "requirement": "Participant count"},
                          {"id": "N2", "requirement": "Causal evidence"}],
            "sources": [{"id": f"S{index}", "path": f"source-{index}.txt"}
                        for index in range(1, 7)],
        }), encoding="utf-8")
        self.expected = {
            "N1": {"verdict": "supported", "support_origin": "source_stated"},
            "N2": {"verdict": "insufficient_evidence", "support_origin": "not_assessed"},
        }
        error_expected = copy.deepcopy(self.expected)
        error_expected["N1"] = {"verdict": "refuted", "support_origin": "not_assessed"}
        self.report_expectations = {}
        self.reports = {}
        for name, text, expected in (("anchor", self.anchor_text, self.expected),
                                     ("count-error", self.error_text, error_expected),
                                     ("wording-control", self.control_text, self.expected),
                                     ("brief-control", self.brief_text, self.expected)):
            path = self.folder / f"{name}.md"
            path.write_text(text, encoding="utf-8")
            report_hash = digest(path.read_bytes())
            self.reports[name] = {"path": path.name, "sha256": report_hash,
                                  "expected": copy.deepcopy(expected)}
            self.report_expectations[report_hash] = copy.deepcopy(expected)
        self.suite_data = {
            "schema_version": 1, "id": "trial-calibration", "case": self.case.name,
            "criteria": [{"id": "N1", "requirement": "Participant count"},
                         {"id": "N2", "requirement": "Causal evidence"}],
            "anchor": self.reports["anchor"],
            "variants": [
                {"id": "count-error", "kind": "error", **self.reports["count-error"],
                 "description": "Replace 12 participants with 99.", "targets": ["N1"]},
                {"id": "wording-control", "kind": "control", **self.reports["wording-control"],
                 "description": "Change only wording.", "targets": []},
                {"id": "brief-control", "kind": "control", **self.reports["brief-control"],
                 "description": "Compress the participant-count sentence.", "targets": []},
            ],
        }
        self.suite = self.folder / "suite.json"
        self.write_suite()

    def write_suite(self):
        self.suite.write_text(json.dumps(self.suite_data), encoding="utf-8")

    def prepare(self, name="calibration", seed=7):
        self.assertIsNotNone(self.calibration, "research must expose the calibration extension")
        result = self.calibration.prepare(research.workspace, research.evaluation,
                                          str(self.suite), str(self.folder / name), seed=seed)
        self.assertEqual(Path(result["calibration_dir"]), self.folder / name)
        self.assertEqual(len(result["blind_packets"]), 2)
        self.assertEqual(len(result["judgment_templates"]), 2)
        return result

    def packets(self, prepared):
        return [json.loads(Path(path).read_text(encoding="utf-8"))
                for path in prepared["blind_packets"]]

    def judgments(self, prepared):
        values = []
        for packet in self.packets(prepared):
            judgments = {"schema_version": 1, "packet_hash": packet["packet_hash"],
                         "reviewer": "independent-fixture-reviewer", "frames": {}}
            for frame in packet["frames"]:
                ratings = {}
                for label, report in frame["reports"].items():
                    expectations = self.report_expectations[report["report_hash"]]
                    criteria = {}
                    for cid, expected in expectations.items():
                        quote = report["text"].splitlines()[0 if cid == "N1" else 1]
                        criteria[cid] = {**expected, "reason": "Compared the cited report and source passage.",
                                         "report_quote": quote, "source_ids": ["S1" if cid == "N1" else "S2"]}
                    ratings[label] = {"report_hash": report["report_hash"], "criteria": criteria}
                judgments["frames"][frame["id"]] = ratings
            values.append(judgments)
        return values

    def score(self, prepared, judgments, name="submitted"):
        paths = []
        for index, value in enumerate(judgments, 1):
            path = Path(prepared["calibration_dir"]) / f"{name}-{index}.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            paths.append(str(path))
        return self.calibration.score(research.workspace, prepared["calibration_dir"], paths)

    def ratings(self, judgments):
        for submitted in judgments:
            for frame in submitted["frames"].values():
                yield from frame.values()

    def test_blind_packets_hide_private_expectations_and_contain_full_frozen_corpus(self):
        prepared = self.prepare()
        forbidden = {"expected", "kind", "targets", "description", "anchor", "variant_id", "report_path"}

        def check_keys(value):
            if isinstance(value, dict):
                self.assertFalse(set(value) & forbidden)
                for child in value.values():
                    check_keys(child)
            elif isinstance(value, list):
                for child in value:
                    check_keys(child)

        for packet in self.packets(prepared):
            check_keys(packet)
            self.assertEqual(packet["criteria"], self.suite_data["criteria"])
            self.assertEqual(packet["question"], "What does the trial establish?")
            self.assertEqual(len(packet["corpus"]), 6)
            raw = json.dumps(packet)
            for private in (str(self.folder), "count-error", "wording-control", "brief-control", "anchor.md"):
                self.assertNotIn(private, raw)
            report_hashes = [report["report_hash"] for frame in packet["frames"]
                             for report in frame["reports"].values()]
            self.assertEqual(set(report_hashes), set(self.report_expectations))
            self.assertEqual(len(report_hashes), len(set(report_hashes)))
            for source in packet["corpus"]:
                original = self.folder / f"source-{source['id'][1:]}.txt"
                self.assertEqual(source["text"], original.read_text(encoding="utf-8"))
                self.assertEqual(source["hash"], digest(original.read_bytes()))
            without_hash = {key: value for key, value in packet.items() if key != "packet_hash"}
            self.assertEqual(packet["packet_hash"], digest(json.dumps(
                without_hash, ensure_ascii=False, sort_keys=True).encode("utf-8")))
        self.assertTrue((Path(prepared["calibration_dir"]) / "key.json").exists())
        self.assertFalse((Path(prepared["calibration_dir"]) / "calibration.json").exists())

    def test_label_swap_uses_same_reports_with_new_frame_ids_and_reversed_labels(self):
        first, second = self.packets(self.prepare())
        self.assertEqual(len(first["frames"]), 2)
        self.assertEqual(len(second["frames"]), 2)
        self.assertFalse({frame["id"] for frame in first["frames"]}
                         & {frame["id"] for frame in second["frames"]})
        pairs = {frozenset(report["report_hash"] for report in frame["reports"].values()): frame
                 for frame in second["frames"]}
        for frame in first["frames"]:
            counterpart = pairs[frozenset(report["report_hash"] for report in frame["reports"].values())]
            self.assertEqual(frame["reports"]["A"], counterpart["reports"]["B"])
            self.assertEqual(frame["reports"]["B"], counterpart["reports"]["A"])
            self.assertEqual(list(frame["reports"]), ["A", "B"])
            self.assertEqual(list(counterpart["reports"]), ["A", "B"])
            first_display = [report["report_hash"] for report in frame["reports"].values()]
            second_display = [report["report_hash"] for report in counterpart["reports"].values()]
            self.assertEqual(first_display, second_display[::-1])

    def test_odd_report_count_has_one_single_report_frame_and_swaps_its_actual_label(self):
        self.suite_data["variants"] = [variant for variant in self.suite_data["variants"]
                                       if variant["id"] != "brief-control"]
        self.write_suite()
        prepared = self.prepare()
        packets = self.packets(prepared)
        singles = []
        for index, packet in enumerate(packets):
            self.assertEqual(len(packet["frames"]), 2)
            reports = [report for frame in packet["frames"] for report in frame["reports"].values()]
            self.assertEqual(len(reports), 3)
            self.assertEqual(len({report["report_hash"] for report in reports}), 3)
            single, = [frame for frame in packet["frames"] if len(frame["reports"]) == 1]
            self.assertEqual(set(single["reports"]), {"A" if index == 0 else "B"})
            singles.append(next(iter(single["reports"].values())))
        self.assertEqual(singles[0], singles[1])
        judgments = self.judgments(prepared)
        result = self.score(prepared, judgments)
        self.assertTrue(result["passed"])
        self.assertEqual(result["expected_checks"], 3 * 2 * 2)
        self.assertEqual(result["controls"]["expected_checks"], 1 * 2 * 2)
        single_id = next(frame["id"] for frame in packets[0]["frames"] if len(frame["reports"]) == 1)
        judgments[0]["frames"][single_id]["B"] = copy.deepcopy(judgments[0]["frames"][single_id]["A"])
        with self.assertRaises(ValueError):
            self.score(prepared, judgments, "extra-single-label")

    def test_identical_report_hash_cannot_have_conflicting_curated_expectations(self):
        error = self.suite_data["variants"][0]
        error.update(path=self.suite_data["anchor"]["path"], sha256=self.suite_data["anchor"]["sha256"])
        self.write_suite()
        with self.assertRaises(ValueError):
            self.prepare()

    def test_consistency_groups_identical_report_hashes_even_under_distinct_artifact_ids(self):
        duplicate = self.suite_data["variants"][2]
        duplicate.update(path=self.suite_data["anchor"]["path"], sha256=self.suite_data["anchor"]["sha256"])
        self.write_suite()
        prepared = self.prepare()
        packets = self.packets(prepared)
        judgments = self.judgments(prepared)
        duplicate_hash = self.suite_data["anchor"]["sha256"]
        selected = next((frame, label) for frame in packets[0]["frames"]
                        for label, report in frame["reports"].items()
                        if report["report_hash"] == duplicate_hash)
        frame, label = selected
        pair = sorted(report["report_hash"] for report in frame["reports"].values())
        counterpart = next(other for other in packets[1]["frames"]
                           if sorted(report["report_hash"] for report in other["reports"].values()) == pair)
        for index, frame_id, actual_label in ((0, frame["id"], label),
                                               (1, counterpart["id"], "B" if label == "A" else "A")):
            judgments[index]["frames"][frame_id][actual_label]["criteria"]["N1"].update(
                verdict="insufficient_evidence", support_origin="not_assessed")
        result = self.score(prepared, judgments)
        self.assertFalse(result["passed"])
        self.assertFalse(result["label_consistency"]["passed"])

    def test_same_seed_reproduces_packets_and_different_seeds_change_blinding(self):
        first = self.packets(self.prepare())
        self.assertEqual(first, self.packets(self.prepare("repeated", seed=7)))
        alternatives = [self.packets(self.prepare(f"seed-{seed}", seed=seed)) for seed in (0, 1, 2)]
        self.assertTrue(any(packet != first for packet in alternatives))

    def test_frozen_report_hash_mismatches_are_rejected(self):
        for identity in self.reports:
            path = self.folder / self.reports[identity]["path"]
            original = path.read_bytes()
            path.write_bytes(original + b" altered")
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, "hash|sha256"):
                self.prepare(f"tampered-{identity}")
            path.write_bytes(original)

    def test_suite_expectations_require_exact_ids_valid_verdicts_and_origins(self):
        original = copy.deepcopy(self.suite_data)
        mutations = [
            lambda value: value.update(schema_version=True),
            lambda value: value["anchor"]["expected"].pop("N2"),
            lambda value: value["anchor"]["expected"].update(EXTRA=value["anchor"]["expected"]["N1"]),
            lambda value: value["anchor"]["expected"]["N1"].update(verdict="partial"),
            lambda value: value["anchor"]["expected"]["N2"].update(support_origin="derived"),
            lambda value: value["criteria"].append(copy.deepcopy(value["criteria"][0])),
        ]
        for index, mutate in enumerate(mutations):
            self.suite_data = copy.deepcopy(original)
            mutate(self.suite_data)
            self.write_suite()
            with self.subTest(mutation=index), self.assertRaises(ValueError):
                self.prepare(f"invalid-{index}")

    def test_controls_and_error_targets_cannot_silently_change_other_expected_items(self):
        original = copy.deepcopy(self.suite_data)
        for index in (0, 1):
            self.suite_data = copy.deepcopy(original)
            self.suite_data["variants"][index]["expected"]["N2"] = {
                "verdict": "supported", "support_origin": "source_stated"}
            self.write_suite()
            with self.subTest(variant=index), self.assertRaises(ValueError):
                self.prepare(f"invalid-target-{index}")

    def test_duplicate_suite_json_keys_are_rejected_and_existing_output_is_preserved(self):
        raw = self.suite.read_text(encoding="utf-8")
        self.suite.write_text(raw.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.prepare("duplicate-suite")
        self.suite.write_text(raw, encoding="utf-8")
        prepared = self.prepare()
        original = Path(prepared["blind_packets"][0]).read_bytes()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(Path(prepared["blind_packets"][0]).read_bytes(), original)

    def test_nonfinite_json_values_are_rejected_before_preparation(self):
        original = self.suite.read_text(encoding="utf-8")
        for index, token in enumerate(("NaN", "Infinity", "-Infinity", "1e999")):
            raw = original.replace("{", '{"metadata": ' + token + ",", 1)
            self.suite.write_text(raw, encoding="utf-8")
            with self.subTest(token=token), self.assertRaises(ValueError):
                self.prepare(f"nonfinite-{index}")

    def test_reviewer_config_rejects_boolean_number_equivalence(self):
        prepared = self.prepare()
        for first, second in ((False, 0), (True, 1), ([False], [0])):
            with self.subTest(first=first, second=second):
                judgments = self.judgments(prepared)
                judgments[0]["reviewer_config"] = {"seed": first}
                judgments[1]["reviewer_config"] = {"seed": second}
                with self.assertRaises(ValueError):
                    self.score(prepared, judgments)
                self.assertFalse((Path(prepared["calibration_dir"]) / "calibration.json").exists())
        judgments = self.judgments(prepared)
        judgments[0]["reviewer_config"] = {"seed": 0, "nested": {"enabled": False}}
        judgments[1]["reviewer_config"] = {"nested": {"enabled": False}, "seed": 0}
        self.assertTrue(self.score(prepared, judgments)["passed"])

    def test_nonfinite_reviewer_metadata_is_rejected_before_scoring_or_persisting(self):
        prepared = self.prepare()
        valid = self.judgments(prepared)
        for token in ("NaN", "Infinity", "-Infinity", "1e999"):
            paths = []
            for index, value in enumerate(valid):
                submitted = {**value, "reviewer_config": {"temperature": "NONFINITE_VALUE"}}
                raw = json.dumps(submitted).replace('"NONFINITE_VALUE"', token)
                path = Path(prepared["calibration_dir"]) / f"nonfinite-review-{index}.json"
                path.write_text(raw, encoding="utf-8")
                paths.append(str(path))
            with self.subTest(token=token), self.assertRaises(ValueError):
                self.calibration.score(research.workspace, prepared["calibration_dir"], paths)
            self.assertFalse((Path(prepared["calibration_dir"]) / "calibration.json").exists())

    def test_duplicate_surrogate_key_cli_returns_structured_error_without_secondary_crash(self):
        original = self.suite.read_text(encoding="utf-8")
        raw = original.replace("{", '{"\\ud800": 1, "\\ud800": 2,', 1)
        self.suite.write_text(raw, encoding="utf-8")
        process = subprocess.run([sys.executable, str(SCRIPT), "calibrate", str(self.suite),
                                  "--output", str(self.folder / "bad-surrogate")],
                                 capture_output=True, text=True, timeout=30)
        self.assertNotEqual(process.returncode, 0)
        self.assertNotIn("Traceback", process.stderr)
        self.assertIn("error", json.loads(process.stdout))
        self.assertFalse((self.folder / "bad-surrogate").exists())

    def test_perfect_explicit_judgments_pass_and_are_saved_without_changing_templates(self):
        prepared = self.prepare()
        originals = [Path(path).read_bytes() for path in prepared["judgment_templates"]]
        result = self.score(prepared, self.judgments(prepared))
        self.assertTrue(result["passed"])
        self.assertEqual(result["expected_checks"], result["matched_checks"])
        self.assertEqual(result["expected_checks"], 4 * 2 * 2)
        self.assertEqual(result["mismatches"], [])
        self.assertTrue(result["label_consistency"]["passed"])
        self.assertTrue(result["controls"]["passed"])
        self.assertEqual(result["controls"]["expected_checks"], 2 * 2 * 2)
        self.assertEqual(result["errors"]["target_checks"], 1 * 1 * 2)
        saved = json.loads((Path(prepared["calibration_dir"]) / "calibration.json").read_text())
        self.assertEqual(saved, result)
        self.assertEqual(originals, [Path(path).read_bytes() for path in prepared["judgment_templates"]])

    def test_transparent_derived_support_is_distinct_from_source_stated_and_can_pass(self):
        (self.folder / "source-1.txt").write_text("Group A had 6 participants and group B had 6 participants.",
                                                  encoding="utf-8")
        texts = {"anchor": "6 + 6 = 12 participants in total. [S1]",
                 "count-error": "6 + 6 = 99 participants in total. [S1]",
                 "wording-control": "The participant total is 12 (= 6 + 6). [S1]",
                 "brief-control": "Total: 6 + 6 = 12 participants. [S1]"}
        self.report_expectations.clear()
        for identity, first_line in texts.items():
            row = self.suite_data["anchor"] if identity == "anchor" else next(
                value for value in self.suite_data["variants"] if value["id"] == identity)
            path = self.folder / row["path"]
            path.write_text(first_line + "\n" + self.anchor_text.splitlines()[1], encoding="utf-8")
            row["sha256"] = digest(path.read_bytes())
            if identity != "count-error":
                row["expected"]["N1"]["support_origin"] = "derived"
            self.report_expectations[row["sha256"]] = copy.deepcopy(row["expected"])
        self.write_suite()
        prepared = self.prepare()
        result = self.score(prepared, self.judgments(prepared))
        self.assertTrue(result["passed"])
        self.assertEqual(result["expected_checks"], result["matched_checks"])

    def test_all_supported_judgments_fail_instead_of_receiving_full_credit(self):
        prepared = self.prepare()
        judgments = self.judgments(prepared)
        for rating in self.ratings(judgments):
            for criterion in rating["criteria"].values():
                criterion.update(verdict="supported", support_origin="source_stated")
        result = self.score(prepared, judgments)
        self.assertFalse(result["passed"])
        self.assertLess(result["matched_checks"], result["expected_checks"])
        self.assertTrue(result["mismatches"])

    def test_unknown_cannot_be_scored_as_refuted_and_origin_is_scored_separately(self):
        prepared = self.prepare()
        valid = self.judgments(prepared)
        for mutation in ("unknown-refuted", "wrong-origin"):
            judgments = copy.deepcopy(valid)
            for rating in self.ratings(judgments):
                if mutation == "unknown-refuted":
                    rating["criteria"]["N2"]["verdict"] = "refuted"
                elif rating["criteria"]["N1"]["verdict"] == "supported":
                    rating["criteria"]["N1"]["support_origin"] = "derived"
            with self.subTest(mutation=mutation):
                result = self.score(prepared, judgments, mutation)
                self.assertFalse(result["passed"])
                self.assertLess(result["matched_checks"], result["expected_checks"])

    def test_not_assessable_cannot_remove_expected_checks_or_improve_score(self):
        prepared = self.prepare()
        judgments = self.judgments(prepared)
        for rating in self.ratings(judgments):
            for criterion in rating["criteria"].values():
                criterion.update(verdict="not_assessable", support_origin="not_assessed")
        result = self.score(prepared, judgments)
        self.assertFalse(result["passed"])
        self.assertGreater(result["expected_checks"], 0)
        self.assertEqual(result["matched_checks"], 0)

    def test_missing_judgment_may_have_no_quote_but_does_not_receive_expected_credit(self):
        prepared = self.prepare()
        judgments = self.judgments(prepared)
        for rating in self.ratings(judgments):
            rating["criteria"]["N2"].update(verdict="missing", support_origin="not_assessed",
                                               report_quote="", source_ids=[])
        result = self.score(prepared, judgments)
        self.assertFalse(result["passed"])
        self.assertLess(result["matched_checks"], result["expected_checks"])

    def test_label_inconsistency_is_detected_after_mapping_report_identity(self):
        prepared = self.prepare()
        judgments = self.judgments(prepared)
        rating = next(iter(judgments[1]["frames"].values()))["A"]
        rating["criteria"]["N2"]["verdict"] = "refuted"
        result = self.score(prepared, judgments)
        self.assertFalse(result["passed"])
        self.assertFalse(result["label_consistency"]["passed"])

    def test_control_reports_cannot_gain_or_lose_factual_credit_from_rewording(self):
        prepared = self.prepare()
        judgments = self.judgments(prepared)
        control_hash = self.reports["wording-control"]["sha256"]
        for rating in self.ratings(judgments):
            if rating["report_hash"] == control_hash:
                rating["criteria"]["N1"].update(verdict="insufficient_evidence", support_origin="not_assessed")
        result = self.score(prepared, judgments)
        self.assertFalse(result["passed"])
        self.assertFalse(result["controls"]["passed"])
        self.assertTrue(result["label_consistency"]["passed"])

    def test_submissions_must_cover_both_distinct_packets_and_exact_frames_and_criteria(self):
        prepared = self.prepare()
        valid = self.judgments(prepared)
        with self.assertRaises(ValueError):
            self.score(prepared, [valid[0], valid[0]])
        mutations = [
            lambda value: value[0].update(reviewer=""),
            lambda value: value[1].update(reviewer="a-different-reviewer"),
            lambda value: value[0].update(schema_version=True),
            lambda value: value[0]["frames"].pop(next(iter(value[0]["frames"]))),
            lambda value: value[0]["frames"].update(EXTRA=next(iter(value[0]["frames"].values()))),
            lambda value: next(iter(value[0]["frames"].values()))["A"]["criteria"].pop("N2"),
            lambda value: next(iter(value[0]["frames"].values()))["A"]["criteria"].update(EXTRA={}),
            lambda value: next(iter(value[0]["frames"].values())).update(C={}),
        ]
        for index, mutate in enumerate(mutations):
            judgments = copy.deepcopy(valid)
            mutate(judgments)
            with self.subTest(mutation=index), self.assertRaises(ValueError):
                self.score(prepared, judgments, f"incomplete-{index}")

    def test_each_judgment_requires_a_reason_and_appropriate_exact_report_quote(self):
        prepared = self.prepare()
        valid = self.judgments(prepared)
        for cid, field, value in (("N1", "report_quote", ""),
                                  ("N1", "report_quote", "A fabricated quotation."),
                                  ("N2", "report_quote", ""),
                                  ("N1", "reason", "  ")):
            judgments = copy.deepcopy(valid)
            next(iter(judgments[0]["frames"].values()))["A"]["criteria"][cid][field] = value
            with self.subTest(criterion=cid, field=field, value=value), self.assertRaises(ValueError):
                self.score(prepared, judgments, f"invalid-{cid}-{field}")

    def test_source_ids_must_be_known_unique_and_present_for_support_or_refutation(self):
        prepared = self.prepare()
        valid = self.judgments(prepared)
        for source_ids in ([], ["UNKNOWN"], ["S1", "S1"]):
            judgments = copy.deepcopy(valid)
            next(iter(judgments[0]["frames"].values()))["A"]["criteria"]["N1"]["source_ids"] = source_ids
            with self.subTest(source_ids=source_ids), self.assertRaises(ValueError):
                self.score(prepared, judgments)

    def test_scoring_rechecks_packet_report_and_submission_hashes_and_duplicate_json(self):
        prepared = self.prepare()
        valid = self.judgments(prepared)
        packet_path = Path(prepared["blind_packets"][0])
        original = packet_path.read_text(encoding="utf-8")
        for target in ("corpus", "report"):
            packet = json.loads(original)
            altered = packet["corpus"][0] if target == "corpus" else packet["frames"][0]["reports"]["A"]
            altered["text"] += " tampered"
            packet_path.write_text(json.dumps(packet), encoding="utf-8")
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "hash"):
                self.score(prepared, valid)
        packet_path.write_text(original, encoding="utf-8")
        for field in ("packet_hash", "report_hash"):
            judgments = copy.deepcopy(valid)
            if field == "packet_hash":
                judgments[0][field] = "wrong"
            else:
                next(iter(judgments[0]["frames"].values()))["A"][field] = "wrong"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "hash"):
                self.score(prepared, judgments)
        paths = []
        for index, value in enumerate(valid):
            path = Path(prepared["calibration_dir"]) / f"duplicate-{index}.json"
            raw = json.dumps(value)
            if index == 0:
                raw = raw.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1)
            path.write_text(raw, encoding="utf-8")
            paths.append(str(path))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.calibration.score(research.workspace, prepared["calibration_dir"], paths)

    def test_calibrate_and_calibration_score_cli_round_trip(self):
        def cli(*args):
            process = subprocess.run([sys.executable, str(SCRIPT), *args],
                                     capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 0, (process.stdout + process.stderr)[:1500])
            return json.loads(process.stdout)

        output = self.folder / "cli-calibration"
        prepared = cli("calibrate", str(self.suite), "--output", str(output), "--seed", "12")
        paths = []
        for index, value in enumerate(self.judgments(prepared)):
            path = output / f"cli-judgments-{index}.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            paths.append(str(path))
        scored = cli("calibration-score", str(output), "--judgments", paths[0], "--judgments", paths[1])
        self.assertTrue(scored["passed"])
        self.assertTrue(scored["label_consistency"]["passed"])
        self.assertTrue((output / "calibration.json").exists())


if __name__ == "__main__":
    unittest.main()
