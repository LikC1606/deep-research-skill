from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "research.py"
SPEC = importlib.util.spec_from_file_location("test_search_batch_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)
workspace = research.workspace


def candidates(query):
    return {"results": [{"url": f"https://example.org/{query}", "title": query}],
            "providers_used": ["direct"]}


class SearchBatchTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="deep-research-batch-tests-")
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        environment = mock.patch.dict(os.environ, {
            "DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
            "DEEP_RESEARCH_SOURCE_POLICY": "web_allowed",
            "DEEP_RESEARCH_SEARCH_PROVIDER": "direct",
            "DRBENCH_MAX_SEARCH": "", "DEEP_RESEARCH_MAX_SEARCH": "0",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def plan(self, rows=None):
        path = self.folder / "plan.json"
        rows = rows if rows is not None else [
            {"query": "implementation", "reason": "Find implementation details"},
            {"query": "evaluation", "reason": "Find independent evaluation"},
        ]
        path.write_text(json.dumps({"searches": rows}), encoding="utf-8")
        return str(path)

    def test_independent_searches_overlap_and_keep_attribution_refs_and_cache(self):
        barrier = threading.Barrier(2, timeout=5)

        def retrieve(query, *args):
            research.base._reserve("search")
            barrier.wait()
            return candidates(query)

        plan = self.plan()
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_MAX_SEARCH": "2"}), \
                mock.patch.object(research, "search", side_effect=retrieve) as search:
            first = research._search_batch(plan, workers=2)
            replay = research._search_batch(plan, workers=2)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(first["status"], "complete")
        self.assertEqual(first["completed"], 2)
        self.assertEqual([row["query"] for row in first["results"]], ["implementation", "evaluation"])
        for row in first["results"]:
            self.assertEqual(row["result"]["results"][0]["title"], row["query"])
            self.assertFalse(row["result"]["results"][0]["citable"])
            self.assertTrue(row["reason"])
        self.assertTrue(all(row["result"]["cache_hit"] for row in replay["results"]))
        self.assertNotIn("https://example.org", json.dumps(first))
        self.assertEqual(research._load_citations(), [])
        record = json.loads((workspace.root() / "searches" / f"{first['batch_id']}.json").read_text())
        self.assertEqual(record["request"]["searches"][1]["reason"], "Find independent evaluation")
        self.assertEqual(len(research._load_refs()), 2)

    def test_parallel_searches_respect_the_shared_budget(self):
        def retrieve(query, *args):
            research.base._reserve("search")
            return candidates(query)

        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_MAX_SEARCH": "1"}), \
                mock.patch.object(research, "search", side_effect=retrieve):
            result = research._search_batch(self.plan(), workers=2)
        self.assertEqual((result["completed"], result["failed"], result["status"]), (1, 1, "partial"))
        self.assertIn("budget exhausted", next(row["error"] for row in result["results"] if row["status"] == "error"))
        counts = json.loads((self.folder / ".adapter-counts.json").read_text())
        self.assertEqual((counts["search"], counts["blocked"]), (1, 1))

    def test_empty_and_failed_queries_do_not_hide_successful_results(self):
        rows = [{"query": query, "reason": query} for query in ("valid", "empty", "unavailable")]

        def retrieve(query, *args):
            if query == "unavailable":
                raise RuntimeError("Provider unavailable")
            return {"results": []} if query == "empty" else candidates(query)

        with mock.patch.object(research, "search", side_effect=retrieve):
            result = research._search_batch(self.plan(rows))
        self.assertEqual([row["status"] for row in result["results"]], ["ok", "empty", "error"])
        self.assertEqual((result["completed"], result["empty"], result["failed"]), (2, 1, 1))
        self.assertEqual(result["status"], "partial")

    def test_incomplete_provider_success_keeps_batch_partial_and_errors_structured(self):
        errors = [{"provider": "bing", "error": "Timeout: offline"}]

        def retrieve(query, *args):
            if query == "evaluation":
                raise research.SearchError(errors)
            return {**candidates(query), "status": "partial", "provider_errors": errors}

        with mock.patch.object(research, "search", side_effect=retrieve):
            result = research._search_batch(self.plan())
        self.assertEqual(result["status"], "partial")
        self.assertEqual((result["partial"], result["failed"]), (1, 1))
        self.assertEqual(result["results"][0]["result"]["provider_errors"], errors)
        self.assertEqual(result["results"][1]["provider_errors"], errors)

    def test_known_artifact_routes_reach_search_and_use_separate_caches(self):
        rows = [{"query": "CSQE", "reason": "Find author material", "target": "CSQE",
                 "artifact": artifact, "proof": "Author method details"} for artifact in ("paper", "code")]
        plan = self.plan(rows)
        with mock.patch.object(research, "search", side_effect=lambda query, *args: candidates(query)) as search:
            result = research._search_batch(plan)
            replay = research._search_batch(plan)
        self.assertEqual(search.call_count, 2)
        self.assertCountEqual([(call.args[2], call.args[3], call.args[4]) for call in search.call_args_list], [
            ("CSQE", "paper", "Author method details"), ("CSQE", "code", "Author method details"),
        ])
        self.assertNotEqual(result["results"][0]["result"]["search_id"], result["results"][1]["result"]["search_id"])
        self.assertTrue(all(row["result"]["cache_hit"] for row in replay["results"]))

    def test_invalid_plans_are_rejected_before_any_retrieval(self):
        valid = {"query": "implementation", "reason": "Find source"}
        invalid = [[], [valid, {"query": "missing purpose"}], [valid, valid],
                   [{"query": "x", "reason": "y", "target": "premature"}],
                   [{"query": "x", "reason": "y", "target": "Known", "artifact": "unsupported", "proof": "Body"}],
                   [{"query": str(i), "reason": "purpose"} for i in range(7)]]
        with mock.patch.object(research, "search") as search:
            for rows in invalid:
                with self.subTest(rows=rows), self.assertRaises(ValueError):
                    research._search_batch(self.plan(rows))
            oversized = self.folder / "oversized.json"
            oversized.write_bytes(b" " * 65_537)
            with self.assertRaisesRegex(ValueError, "65536 bytes"):
                research._search_batch(str(oversized))
            search.assert_not_called()

    def test_registered_needs_keep_their_own_history_and_remain_open(self):
        workspace.init_task("Compare implementations", "Methods")
        rows = []
        for need in ("N1", "N2"):
            workspace.set_need(need, question=need, proof="Read source")
            rows.append({"query": need, "reason": "Find source", "need": need})
        with mock.patch.object(research, "search", side_effect=lambda query, *args: candidates(query)):
            research._search_batch(self.plan(rows))
        task = workspace.status()["task"]
        self.assertCountEqual([row["need"] for row in task["recent_searches"]], ["N1", "N2"])
        self.assertEqual([task["needs"][need]["state"] for need in ("N1", "N2")], ["open", "open"])
        rows[1]["need"] = "unregistered"
        with mock.patch.object(research, "search") as search, \
                self.assertRaisesRegex(ValueError, "register the Need"):
            research._search_batch(self.plan(rows))
        search.assert_not_called()

    def test_cli_replays_local_cache_and_reports_all_uncached_queries_as_failed(self):
        plan = self.plan()
        with mock.patch.object(research, "search", side_effect=lambda query, *args: candidates(query)):
            research._search_batch(plan)
        command = [sys.executable, str(SCRIPT), "search-batch", plan, "--workers", "2"]
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}):
            replay = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(replay.returncode, 0, (replay.stdout + replay.stderr)[:1500])
            self.assertTrue(all(row["result"]["cache_hit"] for row in json.loads(replay.stdout)["results"]))
            self.plan([{"query": "uncached", "reason": "Find missing source"}])
            failed = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(failed.returncode, 1, (failed.stdout + failed.stderr)[:1500])
        result = json.loads(failed.stdout)
        self.assertEqual(result["status"], "failed")
        self.assertIn("local_only", result["results"][0]["error"])

    def test_competition_queries_use_shared_scope_sequentially(self):
        rows = [{"query": "kaggle titanic metric", "reason": "Find official metric"},
                {"query": "kaggle titanic winning code", "reason": "Find implementation"}]
        with mock.patch.object(research, "search", return_value=candidates("competition")):
            result = research._search_batch(self.plan(rows), workers=3)
        self.assertEqual(result["workers"], 1)

    def test_competition_target_uses_shared_scope_even_when_query_omits_its_name(self):
        rows = [{"query": "official evaluation", "reason": "Find metric", "target": "Kaggle Titanic",
                 "artifact": "official", "proof": "Evaluation metric"},
                {"query": "author implementation", "reason": "Find code", "target": "Kaggle Titanic",
                 "artifact": "code", "proof": "Training implementation"}]
        with mock.patch.object(research, "search", return_value=candidates("competition")):
            result = research._search_batch(self.plan(rows), workers=3)
        self.assertEqual(result["workers"], 1)


if __name__ == "__main__":
    unittest.main()
