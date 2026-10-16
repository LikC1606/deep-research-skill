from __future__ import annotations

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
SPEC = importlib.util.spec_from_file_location("test_search_workspace_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)
workspace = research.workspace


def candidates(suffix="one"):
    return {"results": [{"url": f"https://example.org/{suffix}", "title": suffix}],
            "providers_used": ["direct"]}


class SearchWorkspaceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="deep-research-search-tests-")
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        env = mock.patch.dict(os.environ, {
            "DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
            "DEEP_RESEARCH_SOURCE_POLICY": "web_allowed",
            "DEEP_RESEARCH_SEARCH_PROVIDER": "direct",
            "DRBENCH_MAX_SEARCH": "", "DEEP_RESEARCH_MAX_SEARCH": "0",
        })
        env.start()
        self.addCleanup(env.stop)

    def search(self, query="method cost", **kwargs):
        arguments = {"limit": 5, "target": None, "artifact": None, "proof": None, "provider": None}
        arguments.update(kwargs)
        return research._ref_search(query, **arguments)

    def init_needs(self):
        workspace.init_task("Compare methods", "A and B")
        for need in ("N1", "N2"):
            workspace.set_need(need, question=f"Cost of {need}", proof="Official pricing")

    def test_repeat_search_reuses_disk_cache_without_spending_budget(self):
        def retrieve(*args):
            research.base._reserve("search")
            return candidates()

        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_MAX_SEARCH": "1"}), \
                mock.patch.object(research, "search", side_effect=retrieve) as retrieve_mock:
            first = self.search()
            second = self.search()
            self.assertEqual(retrieve_mock.call_count, 1)
            self.assertFalse(first["cache_hit"])
            self.assertTrue(second["cache_hit"])
            self.assertEqual(first["searched_at"], second["searched_at"])
            with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
                self.search(refresh=True)
            self.assertTrue(self.search()["cache_hit"])
        counts = json.loads((self.folder / ".adapter-counts.json").read_text())
        self.assertEqual(counts["search"], 1)

    def test_refresh_replaces_results_and_seen_refs_are_reported(self):
        with mock.patch.object(research, "search", side_effect=[candidates(), candidates("two")]) as retrieve:
            first = self.search()
            repeated = self.search()
            refreshed = self.search(refresh=True)
            latest = self.search()
        self.assertEqual(retrieve.call_count, 2)
        self.assertEqual(first["new_ref_count"], 1)
        self.assertFalse(first["results"][0]["seen_before"])
        self.assertEqual(repeated["new_ref_count"], 0)
        self.assertTrue(repeated["results"][0]["seen_before"])
        self.assertFalse(refreshed["cache_hit"])
        self.assertEqual(refreshed["new_ref_count"], 1)
        self.assertEqual(latest["results"][0]["title"], "two")

    def test_cache_separates_routing_options_and_case_sensitive_queries(self):
        with mock.patch.object(research, "search", return_value=candidates()) as retrieve:
            baseline = self.search("ModelX")
            self.assertTrue(self.search("  ModelX  ")["cache_hit"])
            variants = [
                ("modelx", {}), ("ModelX", {"limit": 6}),
                ("ModelX", {"provider": "hybrid"}),
                ("ModelX", {"target": "A", "artifact": "official", "proof": "unit price"}),
                ("ModelX", {"target": "B", "artifact": "official", "proof": "unit price"}),
                ("ModelX", {"target": "A", "artifact": "paper", "proof": "unit price"}),
                ("ModelX", {"target": "A", "artifact": "official", "proof": "storage price"}),
            ]
            for query, options in variants:
                with self.subTest(query=query, options=options):
                    result = self.search(query, **options)
                    self.assertFalse(result["cache_hit"])
                    self.assertNotEqual(result["search_id"], baseline["search_id"])
            self.assertEqual(retrieve.call_count, len(variants) + 1)

    def test_local_only_can_replay_but_blocks_refresh_and_uncached_queries(self):
        with mock.patch.object(research, "search", return_value=candidates()):
            self.search()
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}), \
                mock.patch.object(research, "_search_anysearch") as anysearch, \
                mock.patch.object(research, "_search_direct") as direct:
            self.assertTrue(self.search()["cache_hit"])
            with self.assertRaisesRegex(RuntimeError, "local_only"):
                self.search("uncached query")
            with self.assertRaisesRegex(RuntimeError, "local_only"):
                self.search(refresh=True)
            anysearch.assert_not_called()
            direct.assert_not_called()

    def test_failure_is_logged_and_remains_retryable(self):
        self.init_needs()
        with mock.patch.object(research, "search", side_effect=[RuntimeError("Temporary failure"), candidates()]) \
                as retrieve:
            with self.assertRaisesRegex(RuntimeError, "Temporary failure"):
                self.search(need="N1")
            self.assertFalse(self.search(need="N1")["cache_hit"])
            self.assertTrue(self.search(need="N1")["cache_hit"])
            self.assertEqual(retrieve.call_count, 2)
        summary = workspace.status()["task"]
        self.assertEqual(summary["failure_count"], 1)
        self.assertEqual(summary["recent_failures"][0]["need"], "N1")
        self.assertEqual(summary["search_count"], 3)
        self.assertEqual(summary["recent_searches"][0]["error"], "Temporary failure")
        self.assertEqual(summary["needs"]["N1"]["state"], "open")

    def test_same_query_can_serve_different_needs_without_copying_attribution(self):
        self.init_needs()
        with mock.patch.object(research, "search", return_value=candidates()) as retrieve:
            first = self.search(need="N1")
            second = self.search(need="N2")
            self.assertEqual(retrieve.call_count, 1)
        self.assertEqual(first["need"], "N1")
        self.assertEqual(second["need"], "N2")
        self.assertEqual([row["need"] for row in workspace.status()["task"]["recent_searches"]],
                         ["N1", "N2"])
        self.assertEqual(workspace.status()["remaining_needs"], ["N1", "N2"])

    def test_unregistered_and_stopped_needs_are_rejected_before_retrieval(self):
        self.init_needs()
        with mock.patch.object(research, "search", return_value=candidates()) as retrieve:
            with self.assertRaisesRegex(ValueError, "register the Need"):
                self.search(need="typo")
            retrieve.assert_not_called()
            self.search(need="N1")
            workspace.stop_task("Continue later")
            with self.assertRaisesRegex(ValueError, "use resume"):
                self.search(need="N1")
            self.assertEqual(retrieve.call_count, 1)
        workspace.resume_task()
        self.assertTrue(self.search(need="N1")["cache_hit"])

    def test_search_cache_is_isolated_and_never_admits_citations(self):
        with mock.patch.object(research, "search", return_value=candidates()) as retrieve:
            first = self.search()
            with mock.patch.dict(os.environ, {"DEEP_RESEARCH_RUN_DIR": str(self.folder / "other")}):
                second = self.search()
                self.assertFalse(second["cache_hit"])
            self.assertEqual(retrieve.call_count, 2)
            cached = self.search()
        self.assertEqual(first["search_id"], cached["search_id"])
        self.assertNotIn("https://example.org", json.dumps(cached))
        self.assertFalse(cached["results"][0]["citable"])
        self.assertEqual(cached["results"][0]["evidence_status"], "unread_lead")
        self.assertEqual(research._load_citations(), [])

    def test_empty_results_are_reusable_without_claiming_a_need_is_answered(self):
        self.init_needs()
        with mock.patch.object(research, "search", return_value={"results": []}) as retrieve:
            self.search(need="N1")
            self.assertTrue(self.search(need="N1")["cache_hit"])
            self.assertEqual(retrieve.call_count, 1)
        self.assertEqual(workspace.status()["task"]["needs"]["N1"]["state"], "open")

    def test_provider_timeouts_are_not_cached_and_the_next_attempt_can_recover(self):
        self.init_needs()
        with mock.patch.dict(os.environ, {"DRBENCH_ACADEMIC_SEARCH": "0", "DRBENCH_PRACTITIONER_SEARCH": "0"}), \
                mock.patch.object(research.base, "_get", side_effect=research.base.requests.Timeout("offline")) as fetch:
            with self.assertRaises(research.SearchError) as failure:
                self.search(need="N1")
            self.assertEqual(fetch.call_count, 2)
        self.assertEqual({row["provider"] for row in failure.exception.provider_errors}, {"duckduckgo", "bing"})
        with mock.patch.object(research, "_search_direct", return_value=candidates()) as retrieve:
            self.assertFalse(self.search(need="N1")["cache_hit"])
            self.assertTrue(self.search(need="N1")["cache_hit"])
            self.assertEqual(retrieve.call_count, 1)
        self.assertEqual(workspace.status()["task"]["failure_count"], 1)

    def test_successful_empty_provider_responses_are_cached(self):
        with mock.patch.dict(os.environ, {"DRBENCH_ACADEMIC_SEARCH": "0", "DRBENCH_PRACTITIONER_SEARCH": "0"}), \
                mock.patch.object(research.base, "_get", side_effect=[('<div class="no-results">No results found</div>', ""), ("<rss><channel/></rss>", "")]) as fetch:
            result = self.search()
            self.assertEqual(result["status"], "empty")
            self.assertEqual([row["status"] for row in result["provider_results"]], ["empty", "empty"])
            self.assertTrue(self.search()["cache_hit"])
            self.assertEqual(fetch.call_count, 2)

    def test_challenge_and_unknown_responses_are_not_cached_as_empty(self):
        for page in ('<form id="challenge-form">Verify you are human</form>', "<html>unexpected shell</html>"):
            with self.subTest(page=page), \
                    mock.patch.dict(os.environ, {"DRBENCH_ACADEMIC_SEARCH": "0", "DRBENCH_PRACTITIONER_SEARCH": "0"}), \
                    mock.patch.object(research.base, "_get", side_effect=lambda url: ("<rss><channel/></rss>" if "bing.com" in url else page, "")) as fetch:
                for _ in range(2):
                    with self.assertRaises(research.SearchError):
                        self.search()
                self.assertEqual(fetch.call_count, 4)

    def test_partial_results_keep_errors_including_when_replayed(self):
        lead = {"url": "https://example.org/cost", "title": "Method cost", "snippet": "Method cost"}
        with mock.patch.dict(os.environ, {"DRBENCH_ACADEMIC_SEARCH": "0", "DRBENCH_PRACTITIONER_SEARCH": "0"}), \
                mock.patch.object(research, "_duckduckgo", return_value=[lead]), \
                mock.patch.object(research.base, "_bing", side_effect=research.base.requests.HTTPError("429")), \
                mock.patch.object(research.base, "_url_ok", return_value=True):
            result = self.search()
            replay = self.search()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["results"]), 1)
        self.assertIn("429", result["provider_errors"][0]["error"])
        self.assertTrue(replay["cache_hit"])
        self.assertEqual(replay["status"], "partial")

    def test_structured_route_can_recover_after_broad_search_failure(self):
        broad = {"results": [], "provider_errors": [{"provider": "bing", "error": "timeout"}]}
        repository = {"url": "https://github.com/team/widget", "title": "Widget implementation"}
        with mock.patch.object(research, "_search_direct", return_value=broad), \
                mock.patch.object(research, "_github_repositories", return_value=[repository]), \
                mock.patch.object(research.base, "_url_ok", return_value=True):
            result = self.search("Widget repository", target="Widget", artifact="code", proof="implementation")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["results"][0]["title"], repository["title"])
        self.assertEqual(result["provider_errors"], broad["provider_errors"])

    def test_ranking_diagnostics_survive_partial_cache_and_local_only_replay(self):
        lead = {"url": "https://sqlite.org/fts5.html", "title": "SQLite FTS5 Extension",
                "snippet": "Full-text search for SQLite.", "engine": "duckduckgo-html"}
        with mock.patch.object(research, "_duckduckgo", return_value=[lead]), \
                mock.patch.object(research.base, "_bing", side_effect=RuntimeError("temporary failure")), \
                mock.patch.object(research, "_academic_search_enabled", return_value=False), \
                mock.patch.object(research, "_practitioner_search_enabled", return_value=False), \
                mock.patch.object(research.base, "_url_ok", return_value=True):
            result = self.search("SQLite FTS5 bm25 auxiliary function")
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}), \
                mock.patch.object(research, "search", side_effect=AssertionError("cache must prevent retrieval")):
            replay = self.search("SQLite FTS5 bm25 auxiliary function")
        self.assertTrue(replay["cache_hit"])
        self.assertEqual(replay["status"], "partial")
        self.assertEqual(replay["discovery_diagnostics"], result["discovery_diagnostics"])
        self.assertEqual(replay["provider_errors"], result["provider_errors"])
        self.assertEqual(replay["discovery_diagnostics"]["direct_ranking"]["low_overlap_candidates_retained"], 1)
        self.assertFalse(replay["results"][0]["citable"])
        self.assertEqual(replay["results"][0]["evidence_status"], "unread_lead")
        self.assertNotIn(lead["url"], json.dumps(replay))
        self.assertEqual(research._load_citations(), [])

    def test_cli_uses_persistent_search_history_and_explicit_refresh(self):
        self.init_needs()
        with mock.patch.object(research, "search", return_value=candidates()):
            self.search(need="N1")
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}):
            command = [sys.executable, str(SCRIPT), "search", "method cost", "--limit", "5", "--need", "N2"]
            replay = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(replay.returncode, 0, (replay.stdout + replay.stderr)[:1500])
            self.assertEqual(json.loads(replay.stdout)["need"], "N2")
            self.assertTrue(json.loads(replay.stdout)["cache_hit"])
            refresh = subprocess.run(command + ["--refresh"], capture_output=True, text=True, timeout=30)
            self.assertEqual(refresh.returncode, 1, (refresh.stdout + refresh.stderr)[:1500])
            self.assertIn("local_only", json.loads(refresh.stdout)["error"])
        summary = workspace.status()["task"]
        self.assertEqual(summary["search_count"], 3)
        self.assertEqual(summary["recent_failures"][0]["need"], "N2")


if __name__ == "__main__":
    unittest.main()
