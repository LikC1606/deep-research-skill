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


if __name__ == "__main__":
    unittest.main()
