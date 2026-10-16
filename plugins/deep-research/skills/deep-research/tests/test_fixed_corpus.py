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
SPEC = importlib.util.spec_from_file_location("corpus_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)
ws, corpus = research.workspace, research.fixed_corpus


class FixedCorpusTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.run = self.folder / "run"
        env = mock.patch.dict(os.environ, {"DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": str(self.run),
                                           "DEEP_RESEARCH_SOURCE_POLICY": "local_only"})
        env.start()
        self.addCleanup(env.stop)
        self.original = SCRIPT.parent.parent / "evals" / "pricing-hard-zh" / "case.json"
        research.evaluation.pack_case(ws, str(self.original), str(self.folder / "researcher"))
        self.case = self.folder / "researcher" / "case.json"

    def search(self, query="请求 存储 计费", limit=2, **kwargs):
        return research._ref_search(query, limit, None, None, None, "direct", corpus_path=str(self.case), **kwargs)

    def cli(self, *args):
        result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[:1200])
        return json.loads(result.stdout)

    def cli_error(self, *args):
        result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, (result.stdout + result.stderr)[:1200])
        return json.loads(result.stdout)["error"]

    def test_chinese_fts_search_is_offline_unread_and_cached(self):
        with mock.patch.object(research, "search", side_effect=AssertionError("network route called")):
            first = self.search()
            second = self.search()
        self.assertTrue(first["results"])
        self.assertFalse(first["results"][0]["citable"])
        self.assertEqual(first["providers_used"], ["local_fts5"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(research._load_citations(), [])
        trace = json.loads((ws.root() / "corpus-trace.json").read_text())
        self.assertEqual(len(trace["operations"]), 1)
        self.assertEqual(trace["operations"][0]["source_ids"], [row["source_id"] for row in first["results"]])

    def test_actual_cli_search_read_and_evidence_keep_source_identity(self):
        self.cli("init", "--deliverable", "Assess pricing", "--target", "Service v3")
        self.cli("need", "N1", "--question", "Current request fee", "--proof", "Current official price")
        result = self.cli("search", "请求", "--corpus", str(self.case), "--need", "N1")
        source = next(row for row in result["results"] if row["source_id"] == "S1")
        page = self.cli("read", source["ref"], "--corpus", str(self.case), "--term", "请求费")
        self.assertEqual(page["corpus_source_id"], "S1")
        self.assertEqual(page["content_scope"], "local_file")
        evidence = self.cli("evidence", page["ref"], "--need", "N1", "--claim", "Current request fee",
                            "--quote", "每 1,000 次请求 0.12 美元", "--relation", "supports")
        self.assertEqual(evidence["snapshot_id"], page["snapshot_id"])
        self.assertEqual(ws.status()["task"]["needs"]["N1"]["state"], "open")
        trace = json.loads((ws.root() / "corpus-trace.json").read_text())
        self.assertEqual([row["kind"] for row in trace["operations"]], ["search", "read"])
        self.assertEqual(trace["operations"][1]["snapshot_id"], evidence["snapshot_id"])

    def test_read_corpus_id_works_for_offline_helpers_after_budget_exhaustion(self):
        case = json.loads(self.case.read_text())
        case["budget"]["source_reads"] = 1
        self.case.write_text(json.dumps(case))
        page = self.cli("read", "S1", "--corpus", str(self.case))
        trace = ws.root() / "corpus-trace.json"
        before = trace.read_bytes()
        # Offline helpers must use the saved snapshot even without the input file.
        Path(page["citation_path"]).unlink()
        alias = self.cli("evidence", "S1", "--need", "N1", "--claim", "Request fee",
                         "--quote", "每 1,000 次请求 0.12 美元", "--relation", "supports")
        canonical = self.cli("evidence", page["ref"], "--version", page["snapshot_id"],
                             "--need", "N1", "--claim", "Request fee",
                             "--quote", "每 1,000 次请求 0.12 美元", "--relation", "supports")
        self.assertEqual(alias["id"], canonical["id"])
        self.assertEqual(alias["ref"], page["ref"])
        self.assertEqual(alias["snapshot_id"], page["snapshot_id"])
        opened = self.cli("open", "S1", "--count", "2")
        found = self.cli("find", "S1", "--term", "请求费")
        self.assertIn("请求费", opened["text"])
        self.assertTrue(found["windows"])
        self.assertEqual(opened["snapshot_id"], page["snapshot_id"])
        self.assertEqual(found["snapshot_id"], page["snapshot_id"])
        self.assertEqual(trace.read_bytes(), before)

    def test_unknown_and_search_only_corpus_ids_require_a_successful_read(self):
        error = self.cli_error("open", "S1")
        self.assertIn("read", error.lower())
        self.search()
        trace = ws.root() / "corpus-trace.json"
        before = trace.read_bytes()
        for args in (("find", "S1", "--term", "请求"),
                     ("evidence", "S1", "--need", "N1", "--claim", "Fee",
                      "--quote", "每 1,000 次请求 0.12 美元", "--relation", "supports"),
                     ("open", "S-UNKNOWN")):
            with self.subTest(command=args[0], source=args[1]):
                self.assertIn("successful", self.cli_error(*args))
        self.assertFalse((ws.root() / "evidence.json").exists())
        self.assertEqual(trace.read_bytes(), before)

    def test_failed_or_pending_reads_do_not_authorize_a_cached_source_alias(self):
        # Even a separately saved REF cannot turn a failed corpus Read into success.
        path = self.case.parent / json.loads(self.case.read_text())["sources"][0]["path"]
        research._ref_read(str(path), [], 650)
        with self.assertRaisesRegex(RuntimeError, "fixture failed"):
            corpus.read(ws, str(self.case), "S1", mock.Mock(side_effect=RuntimeError("fixture failed")))
        identity, _ = corpus.load_case(str(self.case))
        corpus._begin(ws, identity, "read", {"source_id": "S1"})
        before = (ws.root() / "corpus-trace.json").read_bytes()
        self.assertIn("successful", self.cli_error("open", "S1"))
        self.assertEqual((ws.root() / "corpus-trace.json").read_bytes(), before)
        page = self.cli("read", "S1", "--corpus", str(self.case))
        with self.assertRaisesRegex(RuntimeError, "later failure"):
            corpus.read(ws, str(self.case), "S1", mock.Mock(side_effect=RuntimeError("later failure")))
        corpus._begin(ws, identity, "read", {"source_id": "S1"})
        self.assertEqual(self.cli("open", "S1")["snapshot_id"], page["snapshot_id"])

    def test_corpus_alias_stays_on_read_snapshot_after_ordinary_ref_refresh(self):
        page = self.cli("read", "S1", "--corpus", str(self.case))
        Path(page["citation_path"]).write_text("Replacement text after the corpus Read.")
        refreshed = research._ref_read(page["citation_path"], [], 650, refresh=True)
        self.assertNotEqual(refreshed["snapshot_id"], page["snapshot_id"])
        self.assertEqual(self.cli("open", "S1")["snapshot_id"], page["snapshot_id"])
        self.assertEqual(self.cli("open", page["ref"])["snapshot_id"], refreshed["snapshot_id"])
        self.assertIn("successful", self.cli_error("open", "S1", "--version", refreshed["snapshot_id"]))
        self.cli_error("evidence", "S1", "--need", "N1", "--claim", "Changed",
                       "--quote", "Replacement text", "--relation", "supports")
        self.assertFalse((ws.root() / "evidence.json").exists())

    def test_corpus_alias_selects_recorded_versions_with_same_text_and_changed_scope(self):
        first = self.cli("read", "S1", "--corpus", str(self.case))
        _, text = ws.require_source(first["ref"])

        def changed_scope(path):
            metadata, _ = ws.save_source(first["ref"], {
                "url": path, "content_scope": "abstract", "truncated": True,
            }, text)
            return metadata

        second = corpus.read(ws, str(self.case), "S1", changed_scope)
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(self.cli("open", "S1")["content_scope"], "abstract")
        self.assertEqual(self.cli("open", "S1", "--version", first["snapshot_id"])["content_scope"], "local_file")
        legacy = self.cli("open", "S1", "--version", first["content_hash"])
        self.assertEqual(legacy["snapshot_id"], second["snapshot_id"])
        self.assertTrue(legacy["truncated"])
        self.assertEqual(self.cli("open", first["ref"], "--version", first["content_hash"])["snapshot_id"], first["snapshot_id"])
        (ws.root() / "sources" / first["ref"] / f"{second['snapshot_id']}.json").unlink()
        self.cli_error("open", "S1")
        self.cli_error("open", "S1", "--version", first["content_hash"])
        self.assertEqual(self.cli("open", "S1", "--version", first["snapshot_id"])["snapshot_id"], first["snapshot_id"])

    def test_corpus_hash_selection_skips_unrelated_missing_snapshot_without_fallback(self):
        first = self.cli("read", "S1", "--corpus", str(self.case))

        def changed_body(path):
            return ws.save_source(first["ref"], {"url": path, "content_scope": "local_file"},
                                  "Different saved extraction.")[0]

        second = corpus.read(ws, str(self.case), "S1", changed_body)
        latest = ws.root() / "sources" / first["ref"] / f"{second['snapshot_id']}.json"
        saved = latest.read_bytes()
        latest.unlink()
        self.assertEqual(self.cli("open", "S1", "--version", first["content_hash"])["snapshot_id"],
                         first["snapshot_id"])
        self.cli_error("open", "S1")
        self.cli_error("open", "S1", "--version", second["content_hash"])
        # Historical traces cannot identify a missing snapshot's content hash.
        trace_path = ws.root() / "corpus-trace.json"
        trace = json.loads(trace_path.read_text())
        for item in trace["operations"]:
            item.pop("content_hash", None)
        trace_path.write_text(json.dumps(trace))
        self.cli_error("open", "S1", "--version", first["content_hash"])
        self.assertEqual(self.cli("open", "S1", "--version", first["snapshot_id"])["snapshot_id"],
                         first["snapshot_id"])
        latest.write_bytes(saved)
        self.assertEqual(self.cli("open", "S1", "--version", first["content_hash"])["snapshot_id"],
                         first["snapshot_id"])

    def test_corpus_source_ids_reserve_the_canonical_ref_namespace(self):
        reserved = "REF-1234567890ABCDEF"
        case = json.loads(self.case.read_text())
        case["sources"][0]["id"] = reserved
        self.case.write_text(json.dumps(case))
        with self.assertRaisesRegex(ValueError, "reserved"):
            corpus.load_case(str(self.case))
        original = json.loads(self.original.read_text())
        original["sources"][0]["id"] = reserved
        original["sources"] = [original["sources"][0]]
        original.pop("evaluator", None)
        original["sources"][0]["path"] = str(
            (self.original.parent / original["sources"][0]["path"]).resolve())
        original_path = self.folder / "reserved-case.json"
        original_path.write_text(json.dumps(original))
        output = self.folder / "reserved-researcher"
        with self.assertRaisesRegex(ValueError, "reserved"):
            research.evaluation.pack_case(ws, str(original_path), str(output))
        self.assertFalse(output.exists())
        self.assertFalse((ws.root() / "corpus-trace.json").exists())

    def test_corpus_alias_is_run_scoped_and_distinguishes_identical_sources(self):
        case = json.loads(self.case.read_text())
        first_source, second_source = case["sources"][:2]
        (self.case.parent / second_source["path"]).write_bytes((self.case.parent / first_source["path"]).read_bytes())
        second_source["hash"] = first_source["hash"]
        self.case.write_text(json.dumps(case))
        first = self.cli("read", "S1", "--corpus", str(self.case))
        second = self.cli("read", "S2", "--corpus", str(self.case))
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertNotEqual(first["ref"], second["ref"])
        self.assertEqual(self.cli("open", "S1")["ref"], first["ref"])
        self.assertEqual(self.cli("open", "S2")["ref"], second["ref"])
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_RUN_DIR": str(self.folder / "other-run")}):
            self.assertIn("read", self.cli_error("open", "S1").lower())

    def test_corpus_alias_rejects_broken_trace_and_corrupted_saved_snapshot(self):
        page = self.cli("read", "S1", "--corpus", str(self.case))
        trace_path = ws.root() / "corpus-trace.json"
        trace = json.loads(trace_path.read_text())
        for field, value in (("ref", "../../other-run"), ("snapshot_id", "../metadata"),
                             ("content_hash", "0" * 64), ("content_hash", "invalid")):
            changed = json.loads(json.dumps(trace))
            changed["operations"][0][field] = value
            trace_path.write_text(json.dumps(changed))
            with self.subTest(field=field):
                self.cli_error("open", "S1")
        trace_path.write_text(json.dumps(trace))
        Path(page["body_path"]).write_text("corrupted cached body")
        self.cli_error("evidence", "S1", "--need", "N1", "--claim", "Fee",
                       "--quote", "每 1,000 次请求 0.12 美元", "--relation", "supports")
        self.assertFalse((ws.root() / "evidence.json").exists())

    def test_failed_reads_consume_budget_and_corpus_tamper_and_escape_are_rejected(self):
        case = json.loads(self.case.read_text())
        case["budget"]["source_reads"] = 1
        self.case.write_text(json.dumps(case))
        with self.assertRaisesRegex(RuntimeError, "read failed"):
            corpus.read(ws, str(self.case), "S1", mock.Mock(side_effect=RuntimeError("read failed")))
        with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
            corpus.read(ws, str(self.case), "S1", mock.Mock())
        (self.case.parent / case["sources"][0]["path"]).write_text("changed")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            corpus.load_case(str(self.case))
        case["sources"][0]["path"] = "../../private-evaluator.json"
        self.case.write_text(json.dumps(case))
        with self.assertRaisesRegex(ValueError, "inside the researcher"):
            corpus.load_case(str(self.case))

    def test_shared_budgets_reject_new_search_and_corpus_switch(self):
        case = json.loads(self.case.read_text())
        case["budget"]["search_calls"] = 1
        self.case.write_text(json.dumps(case))
        self.search()
        self.assertTrue(self.search()["cache_hit"])
        with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
            self.search("new query")
        case["question"] = "A different question"
        self.case.write_text(json.dumps(case))
        with self.assertRaisesRegex(ValueError, "separate run directory"):
            self.search("new question")

    def test_english_corpus_and_evaluator_inputs_are_distinct(self):
        original = SCRIPT.parent.parent / "evals" / "trial-hard-en" / "case.json"
        exported = self.folder / "english"
        research.evaluation.pack_case(ws, str(original), str(exported))
        result = corpus.search(ws, str(exported / "case.json"), "randomized intervention control", 2)
        self.assertIn("S1", [row["source_id"] for row in result["results"]])
        with self.assertRaisesRegex(ValueError, "case-pack researcher"):
            corpus.load_case(str(original))

    def test_diagnostics_distinguish_retrieval_reading_citation_and_use(self):
        comparison = {"task_rubric": [{"id": "R1", "evidence_groups": [["S1"], ["S2", "S3"]]}]}
        judgment = {"R1": {"status": "unmet", "source_ids": []}}
        trace = {"operations": []}
        cases = [([], [], [], "unmet", "retrieval_gap"),
                 (["S1", "S2"], ["S1"], ["S1"], "unmet", "reading_gap"),
                 (["S1", "S2"], ["S1", "S2"], ["S1"], "partial", "citation_gap"),
                 (["S1", "S2"], ["S1", "S2"], ["S1", "S2"], "partial", "evidence_use_gap"),
                 ([], ["S1", "S3"], ["S1", "S3"], "met", "meets_rubric"),
                 ([], [], ["S1", "S3"], "met", "review_trace_mismatch")]
        for retrieved, read, cited, status, expected in cases:
            with self.subTest(stage=expected):
                trace["operations"] = [{"kind": "search", "state": "completed", "source_ids": retrieved}] + [
                    {"kind": "read", "state": "completed", "source_id": value} for value in read]
                judgment["R1"].update(status=status, source_ids=cited)
                result = research.evaluation._diagnose(trace, comparison, judgment)
                self.assertEqual(result["criteria"]["R1"]["stage"], expected)
        self.assertFalse(research.evaluation._diagnose(None, comparison, judgment)["available"])

    def test_comparison_consumes_actual_trace_and_preserves_unknown_model_and_cost(self):
        self.search()
        corpus.read(ws, str(self.case), "S1", lambda path: research._ref_read(path, [], 650))
        report = self.folder / "fixture.md"
        report.write_text("Unit-test report fixture. [S1]")
        output = self.folder / "comparison"
        research.evaluation.prepare(ws, research.reporting, str(self.original), str(report), str(report), str(output),
                                    a_run=str(self.run), b_run=str(self.run))
        judgments = json.loads((output / "judgments.template.json").read_text())
        judgments.update(reviewer="fixture-reviewer", preferred="tie", reason="Contract test, not a quality comparison.")
        for row in judgments["reports"].values():
            row["scores"] = {key: 0 for key in research.evaluation.DIMENSIONS}
            row["reasons"] = {key: "Fixture does not answer the task." for key in research.evaluation.DIMENSIONS}
            for item in row["checklist"].values():
                item.update(status="missing", reason="Fixture omits the item.")
            for item in row["criteria"].values():
                item.update(status="unmet", reason="No answer in fixture.", report_quote="", source_ids=[])
        judgment_path = output / "review.json"
        judgment_path.write_text(json.dumps(judgments))
        result = research.evaluation.score(ws, str(output), str(judgment_path))
        self.assertTrue(result["comparability"]["same_corpus"])
        self.assertIsNone(result["comparability"]["same_model"])
        values = result["reports"]["A"]["measurements"]["values"]
        self.assertEqual(values["search_calls"], 1)
        self.assertEqual(values["source_reads"], 1)
        self.assertIsNone(values["cost_usd"])
        self.assertNotIn("corpus_trace", result["reports"]["A"])
        self.assertTrue(result["reports"]["A"]["diagnostics"]["available"])
        self.assertIsNone(result["reports"]["A"]["diagnostics"]["correctly_used_source_ids"])

    def test_correct_use_requires_claim_review_and_exact_read_snapshot(self):
        ws.init_task("Assess pricing", "Service v3")
        ws.set_need("N1", question="Export format", proof="Official interface document")
        page = corpus.read(ws, str(self.case), "S4", lambda path: research._ref_read(path, [], 650, refresh=True))
        card = ws.record_evidence(page["ref"], "N1", "Exports use JSONL", "批量导出使用 JSONL", "supports")
        report = self.folder / "reviewed.md"
        report.write_text(f"Exports use JSONL. [{card['id']}]", encoding="utf-8")
        prepared = research.claim_review.claims(ws, research.reporting, str(report))
        judgments = json.loads(Path(prepared["template_path"]).read_text())
        judgments["reviewer"] = "fixture-semantic-reviewer"
        row = judgments["claims"][0]
        row.update(kind="factual", verdict="supported", support_mode="direct", rationale="Exact format statement.")
        row["bindings"][0].update(contribution="necessary", rationale="Direct format evidence.")
        path = self.folder / "claim-judgments.json"
        path.write_text(json.dumps(judgments))
        research.claim_review.claims(ws, research.reporting, str(report), judgments_path=str(path))
        output = self.folder / "with-semantic"
        research.evaluation.prepare(ws, research.reporting, str(self.original), str(report), str(report), str(output),
                                    a_run=str(self.run), b_run=str(self.run), semantic_review=research.claim_review)
        key = json.loads((output / "key.json").read_text())
        packet = json.loads((output / "blind.json").read_text())
        empty = {item["id"]: {"status": "not_assessable", "source_ids": []} for item in packet["task_rubric"]}
        identity = key["labels"]["A"]
        result = research.evaluation._diagnose(identity["corpus_trace"], packet, empty, identity["claim_review"])
        self.assertEqual(result["correctly_used_source_ids"], ["S4"])
        self.assertEqual(result["direct_read_source_ids"], ["S4"])
        identity["corpus_trace"]["operations"][0]["snapshot_id"] = "different"
        result = research.evaluation._diagnose(identity["corpus_trace"], packet, empty, identity["claim_review"])
        self.assertEqual(result["correctly_used_source_ids"], [])
        # Evaluation can inspect another run without changing the current run environment.
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_RUN_DIR": str(self.folder / "evaluator-run")}):
            research.evaluation.prepare(ws, research.reporting, str(self.original), str(report), str(report),
                str(self.folder / "other-run-comparison"), a_run=str(self.run), b_run=str(self.run),
                semantic_review=research.claim_review)


if __name__ == "__main__":
    unittest.main()
