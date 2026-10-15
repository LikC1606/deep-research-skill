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
SPEC = importlib.util.spec_from_file_location("test_search_feedback_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)
workspace = research.workspace


class SearchFeedbackTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="deep-research-feedback-tests-")
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        environment = mock.patch.dict(os.environ, {
            "DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
            "DEEP_RESEARCH_SOURCE_POLICY": "web_allowed",
            "DEEP_RESEARCH_SEARCH_PROVIDER": "direct",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.ref = "REF-1234567890ABCDEF"
        self.body = "Lost Gravity was manufactured by Mack Rides.\nMack Rides is a manufacturer."
        self.metadata, _ = workspace.save_source(self.ref, {
            "url": "https://example.org/coaster", "content_scope": "full_text", "truncated": False,
        }, self.body)

    def source(self, **changes):
        return {
            "ref": self.ref, "quote": "Lost Gravity was manufactured by Mack Rides.",
            "terms": ["Mack Rides"], "role": "bridge", "reason": "Find the manufacturer's country",
            **changes,
        }

    def feedback(self, **changes):
        return {"original_query": "Lost Gravity manufacture", "gap": "country",
                "sources": [self.source()], **changes}

    def run_plan(self, feedback, retrieve=None):
        path = self.folder / "plan.json"
        path.write_text(json.dumps({"feedback": feedback}), encoding="utf-8")
        if retrieve is None:
            retrieve = lambda query, *args: {"results": []}
        with mock.patch.object(research, "search", side_effect=retrieve) as search:
            result = research._search_batch(str(path))
        return result, search

    def test_bridge_hop_keeps_original_branch_and_exact_source_location(self):
        result, search = self.run_plan(self.feedback())
        queries = [call.args[0] for call in search.call_args_list]
        self.assertCountEqual(queries, ["Lost Gravity manufacture", "Mack Rides country"])
        feedback = result["search_feedback"]
        source = feedback["sources"][0]
        self.assertEqual(source["version"], self.metadata["snapshot_id"])
        locator = source["locator"]
        self.assertEqual(self.body[locator["start_char"]:locator["end_char"]], source["quote"])
        self.assertTrue(source["quote_verified"])
        self.assertEqual(source["relevance_assessed_by"], "caller")
        self.assertEqual(workspace.status()["evidence_count"], 0)
        record = json.loads((workspace.root() / "searches" / f"{result['batch_id']}.json").read_text())
        self.assertEqual(record["request"]["feedback"]["original_query"], feedback["original_query"])

    def test_relevant_expansion_preserves_original_and_misleading_terms_do_not_seed_queries(self):
        sources = [self.source(role="relevant"), self.source(role="misleading", terms=["Lost Gravity"])]
        result, search = self.run_plan(self.feedback(sources=sources))
        self.assertCountEqual([call.args[0] for call in search.call_args_list], [
            "Lost Gravity manufacture", "Lost Gravity manufacture Mack Rides country",
        ])
        self.assertNotIn("query", result["search_feedback"]["sources"][1])

    def test_grounded_feedback_routes_to_author_code_without_admitting_it_as_evidence(self):
        body = "CSQE extracts key sentences from retrieved passages."
        workspace.save_source(self.ref, {
            "url": "https://example.org/paper", "content_scope": "full_text", "truncated": False,
        }, body)
        source = self.source(quote=body, terms=["CSQE"], target="CSQE", artifact="code",
                             proof="Query expansion implementation")
        path = self.folder / "code-plan.json"
        path.write_text(json.dumps({"feedback": self.feedback(
            original_query="query expansion", gap="author implementation", sources=[source],
        )}), encoding="utf-8")
        code = {"url": "https://github.com/author/CSQE", "title": "CSQE author implementation"}
        with mock.patch.object(research, "_search_base", return_value={"results": []}), \
                mock.patch.object(research, "_kaggle_topic_cards", return_value=[]), \
                mock.patch.object(research, "_github_repositories", return_value=[code]) as repositories:
            result = research._search_batch(str(path))
        repositories.assert_called()
        expanded = result["results"][1]
        self.assertEqual((expanded["target"], expanded["artifact"]), ("CSQE", "code"))
        self.assertEqual(expanded["result"]["results"][0]["title"], code["title"])
        self.assertFalse(expanded["result"]["results"][0]["citable"])
        self.assertEqual(research._load_citations(), [])
        self.assertEqual(workspace.status()["evidence_count"], 0)

    def test_same_query_can_keep_different_artifact_routes(self):
        sources = [self.source(target="Mack Rides", artifact=artifact, proof="Author material")
                   for artifact in ("code", "paper")]
        result, search = self.run_plan(self.feedback(sources=sources))
        self.assertEqual(search.call_count, 3)
        self.assertEqual([row.get("artifact") for row in result["results"]], [None, "code", "paper"])

    def test_known_target_drops_obsolete_discovery_words_and_retrieves_core_material(self):
        body = "CSQE extracts key sentences from retrieved passages."
        workspace.save_source(self.ref, {
            "url": "https://example.org/paper", "content_scope": "full_text", "truncated": False,
        }, body)
        original = "query expansion chatbot marketing product overview"
        source = self.source(quote=body, terms=["CSQE", "key sentences"], role="relevant",
                             target="CSQE", artifact="code", proof="Key sentence extraction")

        def retrieve(query, *args):
            if "marketing" in query:
                return {"results": []}
            return {"results": [{"url": "https://example.org/csqe-code", "title": "CSQE implementation"}]}

        result, _ = self.run_plan(self.feedback(original_query=original, gap="implementation", sources=[source]),
                                  retrieve)
        self.assertEqual(result["results"][0]["query"], original)
        self.assertEqual(result["results"][0]["status"], "empty")
        focused = result["results"][1]
        self.assertEqual(focused["query"], "CSQE key sentences implementation")
        self.assertEqual(focused["status"], "ok")
        self.assertFalse(focused["result"]["results"][0]["citable"])

    def test_repeated_and_overlapping_phrases_do_not_overweight_the_query(self):
        body = "CSQE uses query expansion with key sentences."
        workspace.save_source(self.ref, {
            "url": "https://example.org/paper", "content_scope": "full_text", "truncated": False,
        }, body)
        source = self.source(quote=body, terms=["CSQE", "query expansion"], role="relevant")
        result, _ = self.run_plan(self.feedback(original_query="CSQE query expansion",
                                               gap="query expansion implementation", sources=[source]))
        self.assertEqual(result["results"][1]["query"], "CSQE query expansion implementation")

    def test_query_compaction_preserves_distinct_technical_names_and_phrase_spelling(self):
        body = "IRCoT supports IR; C C++ C# are distinct terms."
        workspace.save_source(self.ref, {
            "url": "https://example.org/technical", "content_scope": "full_text", "truncated": False,
        }, body)
        source = self.source(quote=body, terms=["C++", "C#", "IRCoT", "IR"], role="relevant")
        result, _ = self.run_plan(self.feedback(original_query="C", gap="IR implementation", sources=[source]))
        self.assertEqual(result["results"][1]["query"], "C C++ C# IRCoT IR implementation")

    def test_invalid_feedback_is_rejected_before_any_search(self):
        invalid = [
            self.feedback(sources=[]), self.feedback(sources=[self.source()] * 6),
            self.feedback(sources=[self.source(ref="REF-0000000000000000")]),
            self.feedback(sources=[self.source(quote="Mack Rides is located in Germany.")]),
            self.feedback(sources=[self.source(terms=["Germany"])]),
            self.feedback(sources=[self.source(), self.source(terms=["invented term"])]),
            self.feedback(sources=[self.source(role="answer")]),
            self.feedback(sources=[self.source(version=None)]),
            self.feedback(sources=[self.source(target="unexpected")]),
            self.feedback(sources=[self.source(target="Other company", artifact="code", proof="Implementation")]),
            self.feedback(sources=[self.source(target="Mack Rides", artifact="unsupported", proof="Implementation")]),
            self.feedback(original_query=""), self.feedback(gap="x" * 501),
            self.feedback(need="unregistered"),
        ]
        path = self.folder / "invalid.json"
        with mock.patch.object(research, "search") as search:
            for feedback in invalid:
                with self.subTest(feedback=feedback):
                    path.write_text(json.dumps({"feedback": feedback}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        research._search_batch(str(path))
            path.write_text(json.dumps({"feedback": self.feedback(), "searches": []}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "either searches or feedback"):
                research._search_batch(str(path))
            search.assert_not_called()

    def test_historical_snapshot_is_used_after_refresh(self):
        workspace.save_source(self.ref, {
            "url": "https://example.org/coaster", "content_scope": "full_text", "truncated": False,
        }, "The current page no longer mentions the manufacturer.")
        result, _ = self.run_plan(self.feedback(sources=[self.source(version=self.metadata["snapshot_id"])]))
        self.assertEqual(result["search_feedback"]["sources"][0]["version"], self.metadata["snapshot_id"])
        with self.assertRaisesRegex(ValueError, "not present verbatim"):
            self.run_plan(self.feedback())

    def test_mixed_policy_blocks_automatic_expansion_from_local_body_before_search(self):
        workspace.save_source(self.ref, {
            "url": str(self.folder / "private.txt"), "content_scope": "local_file", "truncated": False,
        }, self.body)
        path = self.folder / "local-feedback.json"
        path.write_text(json.dumps({"feedback": self.feedback()}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "mixed"}), \
                mock.patch.object(research, "search") as search, \
                self.assertRaisesRegex(ValueError, "local source text"):
            research._search_batch(str(path))
        search.assert_not_called()

    def test_duplicate_chinese_hops_keep_original_technical_spelling(self):
        body = "查询扩展采用 CSQE，而不是只生成同义词。"
        workspace.save_source(self.ref, {
            "url": "https://example.org/csqe", "content_scope": "full_text", "truncated": False,
        }, body)
        source = self.source(quote=body, terms=["CSQE"], reason="查作者实现")
        result, search = self.run_plan(self.feedback(original_query="查询扩展", gap="作者实现", sources=[source] * 5))
        self.assertEqual(search.call_count, 2)
        self.assertEqual(result["results"][1]["query"], "CSQE 作者实现")

    def test_misleading_only_feedback_replays_original_query_and_never_closes_need(self):
        workspace.init_task("Find country", "Lost Gravity")
        workspace.set_need("N1", question="Where was it manufactured?", proof="Country and manufacturer")
        result, search = self.run_plan(self.feedback(need="N1", sources=[self.source(role="misleading")]))
        self.assertEqual(search.call_count, 1)
        self.assertEqual(result["results"][0]["query"], "Lost Gravity manufacture")
        task = workspace.status()["task"]
        self.assertEqual(task["needs"]["N1"]["state"], "open")
        self.assertEqual(task["recent_searches"][-1]["need"], "N1")

    def test_cli_replays_feedback_searches_offline_without_admitting_new_citations(self):
        def retrieve(query, *args):
            return {"results": [{"url": "https://example.org/new", "title": "Unread lead"}]}

        result, _ = self.run_plan(self.feedback(), retrieve)
        self.assertEqual(result["completed"], 2)
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}):
            completed = subprocess.run([sys.executable, str(SCRIPT), "search-batch", str(self.folder / "plan.json")],
                                       capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, (completed.stdout + completed.stderr)[:1_500])
        replay = json.loads(completed.stdout)
        self.assertTrue(all(row["result"]["cache_hit"] for row in replay["results"]))
        self.assertFalse(replay["results"][0]["result"]["results"][0]["citable"])
        self.assertEqual(research._load_citations(), [])


if __name__ == "__main__":
    unittest.main()
