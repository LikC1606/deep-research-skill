import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "research.py"
SPEC = importlib.util.spec_from_file_location("citation_graph_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)


def payload(direction="references"):
    return json.dumps({"data": [{"citedPaper" if direction == "references" else "citingPaper": {
        "title": "A follow-up trial", "paperId": "b" * 40, "year": 2026,
        "url": "https://www.semanticscholar.org/paper/" + "b" * 40,
        "externalIds": {"DOI": "10.1234/followup"}, "openAccessPdf": None}}], "next": 10}).encode()


class CitationGraphTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        env = mock.patch.dict(os.environ, {"DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
                                           "DRBENCH_MAX_SEARCH": "", "DEEP_RESEARCH_MAX_SEARCH": "1",
                                           "DEEP_RESEARCH_SOURCE_POLICY": "web_allowed"})
        env.start()
        self.addCleanup(env.stop)

    def test_identifiers_accept_paper_ids_and_reject_arbitrary_urls(self):
        normalize = research.citation_graph.paper_id
        self.assertEqual(normalize("https://doi.org/10.1234/test"), "DOI:10.1234/test")
        self.assertEqual(normalize("https://arxiv.org/pdf/2401.12345v2.pdf"), "ARXIV:2401.12345v2")
        self.assertEqual(normalize("https://www.semanticscholar.org/paper/name/" + "a" * 40), "a" * 40)
        for value in ("https://internal.example/secret", "../path", "https://doi.org/invalid", "not a paper"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize(value)

    def test_follow_registers_unread_candidates_caches_edges_and_enforces_budget(self):
        with mock.patch.object(research.v1, "_fetch_bytes", return_value=(payload(), "api", "application/json")) as fetch:
            first = research._follow_papers("10.1234/test", "references", 0, 10)
            again = research._follow_papers("10.1234/test", "references", 0, 10)
            fetch.assert_called_once()
            self.assertIn("DOI%3A10.1234%2Ftest/references", fetch.call_args.args[0])
            self.assertEqual(first["next_offset"], 10)
            self.assertTrue(again["cache_hit"])
            self.assertEqual(first["results"][0]["evidence_status"], "unread_lead")
            self.assertFalse(first["results"][0]["citable"])
            self.assertEqual(research._load_citations(), [])
            self.assertNotIn("https://doi.org/10.1234/followup", json.dumps(first))
            with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
                research._follow_papers("10.1234/test", "references", 0, 10, refresh=True)

    def test_local_only_replays_cached_graph_but_never_fetches_new_pages(self):
        with mock.patch.object(research.v1, "_fetch_bytes", return_value=(payload(), "api", "application/json")):
            research._follow_papers("10.1234/test", "references", 0, 10)
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}), \
                mock.patch.object(research.v1, "_fetch_bytes") as fetch:
            self.assertTrue(research._follow_papers("10.1234/test", "references", 0, 10)["cache_hit"])
            with self.assertRaisesRegex(RuntimeError, "local_only"):
                research._follow_papers("10.1234/test", "references", 10, 10)
            fetch.assert_not_called()

    def test_citations_direction_ref_input_and_failure_are_attributed_to_need(self):
        research.workspace.init_task("Review", "Paper")
        research.workspace.set_need("N1", question="Followups", proof="Replication results")
        ref = research._source_ref("https://doi.org/10.1234/test")
        research._save_refs({ref: "https://doi.org/10.1234/test"})
        with mock.patch.object(research.v1, "_fetch_bytes", return_value=(payload("citations"), "api", "application/json")):
            result = research._follow_papers(ref, "citations", 0, 10, need="N1")
        self.assertEqual(result["results"][0]["discovery"], "citations")
        self.assertEqual(result["need"], "N1")
        with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
            research._follow_papers(ref, "citations", 10, 10, need="N1")
        self.assertEqual(research.workspace.status()["task"]["failure_count"], 1)
        self.assertEqual(research.workspace.status()["task"]["needs"]["N1"]["state"], "open")

    def test_invalid_or_oversized_responses_are_rejected(self):
        for body in (b"{}", b"[]", b"x" * 2_000_001):
            with self.subTest(size=len(body)), self.assertRaises(ValueError):
                research.citation_graph.fetch_papers("10.1234/test", "references", 0, 10,
                                                     lambda url: (body, url, "json"))


if __name__ == "__main__":
    unittest.main()
