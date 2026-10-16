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
SPEC = importlib.util.spec_from_file_location("evaluation_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)
evaluation, ws = research.evaluation, research.workspace


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        env = mock.patch.dict(os.environ, {"DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
                                           "DEEP_RESEARCH_SOURCE_POLICY": "local_only"})
        env.start()
        self.addCleanup(env.stop)
        (self.folder / "source.txt").write_text("12 participants; no control group.", encoding="utf-8")
        self.case = self.folder / "case.json"
        self.case.write_text(json.dumps({
            "id": "trial", "question": "What can this trial establish?",
            "checklist": [{"id": "N1", "requirement": "Sample size"},
                          {"id": "N2", "requirement": "Causal limits"}],
            "sources": [{"id": "S1", "path": "source.txt"}],
            "budget": {"network_calls": 0, "source_reads": 1},
        }))
        self.a, self.b = self.folder / "baseline.md", self.folder / "candidate.md"
        self.a.write_text("12 participants. [S1]", encoding="utf-8")
        self.b.write_text("12 participants; no control, so no causal claim. [S1]", encoding="utf-8")

    def prepare(self, name="comparison", **kwargs):
        result = evaluation.prepare(ws, research.reporting, str(self.case), str(self.a), str(self.b),
                                    str(self.folder / name), **kwargs)
        return Path(result["comparison_dir"])

    def judgments(self, directory):
        result = json.loads((directory / "judgments.template.json").read_text())
        result.update(reviewer="independent-test-reviewer", preferred="tie", reason="Fixture review only.")
        for rating in result["reports"].values():
            rating["scores"] = {dim: 3 for dim in evaluation.DIMENSIONS}
            rating["reasons"] = {dim: "Fixture evidence reviewed." for dim in evaluation.DIMENSIONS}
            rating["checklist"] = {need: {"status": "direct", "reason": "Checked supplied passage."}
                                   for need in ("N1", "N2")}
        return result

    def score(self, directory, judgments):
        path = directory / "judgments.json"
        path.write_text(json.dumps(judgments), encoding="utf-8")
        return evaluation.score(ws, str(directory), str(path))

    def test_reproducible_blinding_hashes_and_no_report_path_in_packet(self):
        first, second = self.prepare(seed=7), self.prepare("repeat", seed=7)
        packet = json.loads((first / "blind.json").read_text())
        self.assertEqual(packet, json.loads((second / "blind.json").read_text()))
        self.assertEqual(packet["corpus"][0]["hash"], hashlib.sha256((self.folder / "source.txt").read_bytes()).hexdigest())
        self.assertEqual(packet["corpus"][0]["path"], "source.txt")
        raw = (first / "blind.json").read_text()
        self.assertNotIn(str(self.folder), raw)
        self.assertNotIn("baseline.md", raw)
        self.assertNotIn("candidate.md", raw)
        key = json.loads((first / "key.json").read_text())
        for label, identity in key["labels"].items():
            expected = self.a if identity["input"] == "a" else self.b
            self.assertEqual(packet["reports"][label]["report_hash"], hashlib.sha256(expected.read_bytes()).hexdigest())

    def test_preparation_does_not_assign_scores_and_requires_a_reviewer(self):
        directory = self.prepare()
        self.assertFalse((directory / "scores.json").exists())
        template = json.loads((directory / "judgments.template.json").read_text())
        self.assertTrue(all(value is None for value in template["reports"]["A"]["scores"].values()))
        with self.assertRaisesRegex(ValueError, "identify the reviewer"):
            self.score(directory, template)
        self.assertFalse((directory / "scores.json").exists())

    def test_case_report_and_changed_packet_hashes_are_checked(self):
        directory = self.prepare()
        original = self.judgments(directory)
        for field in ("case", "report"):
            invalid = copy.deepcopy(original)
            if field == "case":
                invalid["case_hash"] = "wrong"
            else:
                invalid["reports"]["A"]["report_hash"] = "wrong"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "hash"):
                self.score(directory, invalid)
        packet_path = directory / "blind.json"
        packet = json.loads(packet_path.read_text())
        for field in ("reports", "corpus"):
            changed = copy.deepcopy(packet)
            target = changed["reports"]["A"] if field == "reports" else changed["corpus"][0]
            target["text"] += " tampered"
            packet_path.write_text(json.dumps(changed))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "hash"):
                self.score(directory, original)
        packet_path.write_text(json.dumps(packet))

    def test_all_dimensions_and_checklist_items_require_explicit_judgments(self):
        directory = self.prepare()
        valid = self.judgments(directory)
        mutations = [
            lambda value: value["reports"]["A"]["scores"].update(completeness=True),
            lambda value: value["reports"]["A"]["scores"].update(completeness=5),
            lambda value: value["reports"]["A"]["reasons"].update(completeness=""),
            lambda value: value["reports"]["A"]["checklist"].pop("N2"),
            lambda value: value["reports"]["A"]["checklist"]["N1"].update(reason=None),
            lambda value: value["reports"]["A"]["checklist"]["N1"].update(status="supported"),
            lambda value: value.update(preferred="a"),
            lambda value: value.update(reason=None),
        ]
        for index, change in enumerate(mutations):
            invalid = copy.deepcopy(valid)
            change(invalid)
            with self.subTest(mutation=index), self.assertRaises(ValueError):
                self.score(directory, invalid)
        self.assertFalse((directory / "scores.json").exists())

    def test_scores_reveal_identity_and_missing_costs_remain_unknown(self):
        counts = self.folder / "run"
        counts.mkdir()
        (counts / ".adapter-counts.json").write_text('{"read": 2}')
        directory = self.prepare(a_run=str(counts), b_run=str(self.folder / "unmeasured"))
        judgments = self.judgments(directory)
        judgments["preferred"] = "A"
        judgments["reports"]["A"]["checklist"]["N2"]["status"] = "inference"
        result = self.score(directory, judgments)
        self.assertEqual(result["assessment"], "external_reviewer")
        self.assertEqual(result["preferred_input"], result["reports"]["A"]["input"])
        self.assertEqual(result["reports"]["A"]["direct_coverage"], .5)
        rows = {value["input"]: value for value in result["reports"].values()}
        self.assertEqual(rows["a"]["measurements"]["values"], {"read": 2})
        self.assertIsNone(rows["b"]["measurements"])

    def test_case_requires_nonempty_unique_items_and_existing_output_is_preserved(self):
        case = json.loads(self.case.read_text())
        invalid_cases = [
            {**case, "checklist": []},
            {**case, "checklist": [case["checklist"][0]] * 2},
            {**case, "checklist": [{"id": "N1"}]},
            {**case, "sources": case["sources"] * 2},
        ]
        for index, invalid in enumerate(invalid_cases):
            self.case.write_text(json.dumps(invalid))
            with self.subTest(case=index), self.assertRaises(ValueError):
                self.prepare()
        self.case.write_text(json.dumps(case))
        directory = self.prepare()
        original = (directory / "blind.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual((directory / "blind.json").read_bytes(), original)

    def test_http_counts_do_not_hide_fixed_corpus_search_and_read_costs(self):
        case = json.loads(self.case.read_text())
        case["budget"].update(search_calls=2)
        self.case.write_text(json.dumps(case))
        public = self.folder / "researcher"
        evaluation.pack_case(ws, str(self.case), str(public))
        research._ref_search("participants", 5, None, None, None, "direct", corpus_path=str(public / "case.json"))
        research.fixed_corpus.read(ws, str(public / "case.json"), "S1", lambda value: research._ref_read(value, [], 100))
        research.base.metrics.snapshot()
        directory = self.prepare(a_run=str(self.folder))
        key = json.loads((directory / "key.json").read_text())
        measurement = next(row["measurements"] for row in key["labels"].values() if row["input"] == "a")
        self.assertEqual(measurement["provenance"], "fixed_corpus_tool_trace")
        self.assertEqual((measurement["values"]["search_calls"], measurement["values"]["source_reads"]), (1, 1))
        self.assertEqual(measurement["adapter_usage"]["values"]["requests"], 0)
        self.assertIsNone(measurement["values"]["cost_usd"])

    def test_platform_measurements_preserve_the_separate_adapter_counts(self):
        (self.folder / "measurements.json").write_text('{"model":"recorded-model", "cost_usd":null}')
        (self.folder / ".adapter-counts.json").write_text('{"requests":3, "request_seconds":1.5}')
        directory = self.prepare(a_run=str(self.folder))
        key = json.loads((directory / "key.json").read_text())
        measurement = next(row["measurements"] for row in key["labels"].values() if row["input"] == "a")
        self.assertEqual(measurement["values"], {"model": "recorded-model", "cost_usd": None})
        self.assertEqual(measurement["adapter_usage"]["values"]["requests"], 3)

    def test_comparison_keeps_v2_spans_and_derivations_private_and_rechecks_snapshots(self):
        ws.init_task("Review participant arithmetic", "trial")
        ws.set_need("N1", question="How many participants?", proof="The original trial record")
        ref = "REF-0000000000000001"
        source = (self.folder / "source.txt").read_text(encoding="utf-8")
        ws.save_source(ref, {"url": str(self.folder / "source.txt"), "content_scope": "local_file"}, source)
        card = ws.record_evidence(ref, "N1", "12 participants", source, "supports")
        self.a.write_text(f"12 participants; twice that is 24. [{card['id']}]", encoding="utf-8")
        prepared = research.claim_review.claims(ws, research.reporting, str(self.a), schema_version=2)
        judgments = json.loads(Path(prepared["template_path"]).read_text(encoding="utf-8"))
        judgments["reviewer"] = "private-span-reviewer"
        first = judgments["claims"][0]
        sentence = first["claim"]
        position = sentence.index(";")
        second = copy.deepcopy(first)
        for row in (first, second):
            row.update(kind="factual", verdict="supported", support_mode="direct", support_origin="source_stated",
                       rationale="Reviewed the full segment against its saved source.")
            row["bindings"][0].update(contribution="necessary", rationale="Supplies the participant count.")
        first.update(claim=sentence[:position], span=[0, position])
        second.update(id="C1-derived", kind="inference", claim=sentence[position:], span=[position, len(sentence)],
                      support_origin="derived", derivation={"premise_evidence_ids": [card["id"]],
                          "formula": "12 * 2 = 24", "units": "participants", "assumptions": []})
        judgments["claims"] = [first, second]
        path = self.folder / "semantic.json"
        path.write_text(json.dumps(judgments), encoding="utf-8")
        saved = research.claim_review.claims(ws, research.reporting, str(self.a), judgments_path=str(path))
        saved_bytes = Path(saved["review_path"]).read_bytes()

        def compared(name):
            directory = self.prepare(name, a_run=str(self.folder), semantic_review=research.claim_review)
            key = json.loads((directory / "key.json").read_text(encoding="utf-8"))
            private = next(row["claim_review"] for row in key["labels"].values() if row["input"] == "a")
            blind = (directory / "blind.json").read_text(encoding="utf-8")
            self.assertNotIn("private-span-reviewer", blind)
            self.assertNotIn("support_origin", blind)
            return private

        private = compared("semantic-comparison")
        self.assertEqual(private["schema_version"], 2)
        self.assertEqual(private["metrics"]["candidate_metrics"]["derived_supported_candidates"], 1)
        self.assertEqual(private["metrics"]["candidate_metrics"]["source_stated_supported_candidates"], 0)
        for expected, actual in zip(judgments["claims"], private["claims"]):
            for key in ("candidate_id", "span", "support_origin", "derivation"):
                self.assertEqual(actual[key], expected[key])
        snapshot = ws.root() / "sources" / ref / f"{card['content_hash']}.txt"
        snapshot.write_text("Corrupt historical source.", encoding="utf-8")
        private = compared("semantic-corrupt-comparison")
        self.assertEqual(private["metrics"]["candidate_metrics"]["fully_supported_candidates"], 0)
        self.assertTrue(all(row["verdict"] == "read_error" for row in private["claims"]))
        self.assertEqual(Path(saved["review_path"]).read_bytes(), saved_bytes)

    def test_compare_and_score_cli_flow(self):
        def cli(*args):
            result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[:1500])
            return json.loads(result.stdout)

        output = self.folder / "cli-comparison"
        prepared = cli("compare", str(self.case), "--a", str(self.a), "--b", str(self.b), "--output", str(output))
        judgments = self.judgments(output)
        path = output / "review.json"
        path.write_text(json.dumps(judgments))
        scored = cli("score", prepared["comparison_dir"], "--judgments", str(path))
        self.assertEqual(scored["preferred_input"], "tie")
        self.assertTrue((output / "scores.json").exists())

    def add_rubric(self):
        case = json.loads(self.case.read_text())
        case["evaluator"] = "private.json"
        self.case.write_text(json.dumps(case))
        (self.folder / "private.json").write_text(json.dumps({"criteria": [
            {"id": "R1", "weight": 2, "requirement": "Check causal limits",
             "reference": "PRIVATE ANSWER: control absent", "source_ids": ["S1"]}]}))

    def test_researcher_export_contains_only_task_and_allowlisted_corpus(self):
        self.add_rubric()
        output = self.folder / "researcher"
        evaluation.pack_case(ws, str(self.case), str(output))
        public = json.loads((output / "case.json").read_text())
        self.assertEqual(set(public), {"id", "question", "budget", "sources"})
        self.assertEqual(sorted(str(p.relative_to(output)) for p in output.rglob("*") if p.is_file()),
                         ["case.json", "corpus/S1.txt"])
        self.assertEqual((output / public["sources"][0]["path"]).read_bytes(), (self.folder / "source.txt").read_bytes())
        self.assertNotIn("PRIVATE ANSWER", (output / "case.json").read_text())
        case = json.loads(self.case.read_text())
        case["sources"][0]["path"] = "private.json"
        self.case.write_text(json.dumps(case))
        with self.assertRaisesRegex(ValueError, "evaluator reference"):
            evaluation.pack_case(ws, str(self.case), str(self.folder / "bad"))

    def test_task_criteria_require_exact_items_quotes_sources_and_packet_hash(self):
        self.add_rubric()
        directory = self.prepare()
        valid = self.judgments(directory)
        packet = json.loads((directory / "blind.json").read_text())
        for label, rating in valid["reports"].items():
            rating["criteria"]["R1"] = {"status": "met", "reason": "Explicitly reviewed.",
                                          "report_quote": packet["reports"][label]["text"], "source_ids": ["S1"]}
        for mutate in (lambda x: x["reports"]["A"]["criteria"].clear(),
                       lambda x: x["reports"]["A"]["criteria"].update(extra={}),
                       lambda x: x["reports"]["A"]["criteria"]["R1"].update(report_quote="invented text"),
                       lambda x: x["reports"]["A"]["criteria"]["R1"].update(source_ids=["unknown"])):
            invalid = copy.deepcopy(valid)
            mutate(invalid)
            with self.assertRaises(ValueError):
                self.score(directory, invalid)
        result = self.score(directory, valid)
        self.assertEqual(result["reports"]["A"]["rubric_summary"]["score_on_assessed"], 1)
        valid["reports"]["A"]["criteria"]["R1"]["status"] = "not_assessable"
        result = self.score(directory, valid)
        self.assertIsNone(result["reports"]["A"]["rubric_summary"]["score_on_assessed"])
        packet["task_rubric"][0]["reference"] = "tampered"
        (directory / "blind.json").write_text(json.dumps(packet))
        with self.assertRaisesRegex(ValueError, "hash"):
            self.score(directory, valid)

    def test_duplicate_json_fields_are_rejected(self):
        self.case.write_text('{"id":"first", "id":"second"}')
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            self.prepare()

    def test_authored_bilingual_cases_export_without_reference_answers(self):
        for name in ("pricing-hard-zh", "trial-hard-en", "multihop-long-en"):
            case = SCRIPT.parent.parent / "evals" / name / "case.json"
            output = self.folder / name
            evaluation.pack_case(ws, str(case), str(output))
            public = json.loads((output / "case.json").read_text())
            self.assertEqual(len(public["sources"]), 6 if name == "multihop-long-en" else 5)
            self.assertNotIn("evaluator", public)
            self.assertNotIn("checklist", public)


if __name__ == "__main__":
    unittest.main()
