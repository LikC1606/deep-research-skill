from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import unittest
import urllib.parse
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "research.py"
SPEC = importlib.util.spec_from_file_location("test_deep_research_module", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)


def card(title: str, url: str, engine: str) -> dict:
    return {"title": title, "url": url, "snippet": title, "engine": engine}


class ProviderRoutingTests(unittest.TestCase):
    def setUp(self):
        self.environment = mock.patch.dict(
            os.environ,
            {
                "DEEP_RESEARCH_SOURCE_POLICY": "web_allowed",
                "DRBENCH_RUN_DIR": "",
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_auto_uses_anysearch_when_it_has_enough_candidates(self):
        anysearch_cards = [
            card("First target", "https://example.com/one", "anysearch"),
            card("Second target", "https://example.org/two", "anysearch"),
        ]
        with (
            mock.patch.object(research.anysearch, "search", return_value=anysearch_cards),
            mock.patch.object(research.base, "_url_ok", return_value=True),
            mock.patch.object(research, "_search_direct") as direct,
        ):
            result = research._search_base("target", 5, provider="auto")

        self.assertEqual(result["providers_used"], ["anysearch"])
        self.assertEqual(len(result["results"]), 2)
        direct.assert_not_called()

    def test_auto_falls_back_when_anysearch_has_too_few_candidates(self):
        anysearch_cards = [
            card("First target", "https://example.com/one", "anysearch")
        ]
        direct_result = {
            "query": "target",
            "results": [card("Direct target", "https://example.net/two", "direct")],
        }
        with (
            mock.patch.object(research.anysearch, "search", return_value=anysearch_cards),
            mock.patch.object(research.base, "_url_ok", return_value=True),
            mock.patch.object(research, "_search_direct", return_value=direct_result),
        ):
            result = research._search_base("target", 5, provider="auto")

        self.assertEqual(result["providers_used"], ["anysearch", "direct"])
        self.assertEqual(len(result["results"]), 2)

    def test_auto_falls_back_when_anysearch_is_unavailable(self):
        direct_result = {
            "query": "target",
            "results": [card("Direct target", "https://example.net/two", "direct")],
        }
        with (
            mock.patch.object(
                research.anysearch,
                "search",
                side_effect=research.anysearch.AnySearchError("service unavailable"),
            ),
            mock.patch.object(research.base, "_url_ok", return_value=True),
            mock.patch.object(research, "_search_direct", return_value=direct_result),
        ):
            result = research._search_base("target", 5, provider="auto")

        self.assertEqual(result["providers_used"], ["direct"])
        self.assertEqual(
            result["provider_errors"],
            [{"provider": "anysearch", "error": "service unavailable"}],
        )
        self.assertEqual(len(result["results"]), 1)

    def test_local_only_blocks_all_web_providers(self):
        with mock.patch.dict(
            os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}, clear=False
        ):
            with self.assertRaisesRegex(RuntimeError, "local files only"):
                research._search_base("query", provider="direct")

    def test_auto_supplements_mirrors_instead_of_counting_them_as_enough_results(self):
        title = "A Comprehensive Benchmark for Deep Research Agents"
        mirrors = [card(title, f"https://mirror{i}.example/paper", "anysearch") for i in range(3)]
        primary = card("ALCE: Citation Support Evaluation", "https://arxiv.org/abs/2305.14627", "arxiv")
        with mock.patch.object(research, "_search_anysearch", return_value=mirrors), \
                mock.patch.object(research.base, "_url_ok", return_value=True), \
                mock.patch.object(research, "_search_direct", return_value={"results": [primary]}) as direct:
            result = research._search_base("deep research citation support evaluation benchmark", 5, provider="auto")
        direct.assert_called_once()
        self.assertIn("repeated_document_results", result["recall_checks"]["initial_gaps"])
        self.assertEqual(sum(item["title"] == title for item in result["results"]), 1)
        self.assertIn(primary["url"], [item["url"] for item in result["results"]])

    def test_auto_supplements_off_target_results_and_missing_need_terms(self):
        generic = [card("Generic research overview", "https://one.example/a", "anysearch"),
                   card("Another research overview", "https://two.example/b", "anysearch")]
        with mock.patch.object(research, "_search_anysearch", return_value=generic), \
                mock.patch.object(research.base, "_url_ok", return_value=True), \
                mock.patch.object(research, "_search_direct", return_value={"results": []}) as direct:
            result = research._search_base("ALCE citation entailment", 5, provider="auto", target="ALCE")
        direct.assert_called_once()
        self.assertIn("no_target_match", result["recall_checks"]["initial_gaps"])
        self.assertIn("need_terms_missing_from_results", result["recall_checks"]["initial_gaps"])

    def test_explicit_anysearch_reports_recall_gaps_without_changing_provider(self):
        with mock.patch.object(research, "_search_anysearch", return_value=[]), \
                mock.patch.object(research, "_search_direct") as direct:
            result = research._search_base("target", provider="anysearch")
        direct.assert_not_called()
        self.assertIn("too_few_distinct_documents", result["recall_checks"]["remaining_gaps"])

    def test_arxiv_abs_pdf_doi_are_one_discovery_document(self):
        urls = ["https://arxiv.org/abs/2305.14627v2", "https://arxiv.org/pdf/2305.14627",
                "https://arxiv.org/html/2305.14627v2",
                "https://doi.org/10.48550/arXiv.2305.14627"]
        with mock.patch.object(research.base, "_url_ok", return_value=True):
            rows = research._rank_discovery([card(f"Title {i}", url, "anysearch") for i, url in enumerate(urls)], "citation", 5)
        self.assertEqual(len(rows), 1)

    def test_named_research_repositories_do_not_require_solution_or_stars(self):
        for target, full_name in (("researchrubrics", "scaleapi/researchrubrics"),
                                  ("BrowseComp-Plus", "texttron/BrowseComp-Plus")):
            payload = {"items": [{"full_name": full_name, "html_url": f"https://github.com/{full_name}",
                                   "description": "Official research resources", "stargazers_count": 0}]}
            with self.subTest(target=target), mock.patch.object(research.base, "_get", return_value=(json.dumps(payload), "application/json")) as get:
                rows = research._github_repositories(f"{target} repository", 3)
                query = urllib.parse.parse_qs(urllib.parse.urlparse(get.call_args[0][0]).query)
                self.assertEqual(query["q"], [target])
                self.assertNotIn("sort", query)
                self.assertEqual(rows[0]["url"], f"https://github.com/{full_name}")
        self.assertIn("solution", research._github_query("Kaggle titanic winning code"))

    def test_generic_writeup_does_not_add_competition_participant_terms(self):
        self.assertEqual(research._route_query("hybrid retrieval query decomposition", "writeup"),
                         "hybrid retrieval query decomposition writeup")

    def test_missing_problem_subject_is_not_hidden_by_matching_metric_words(self):
        generic = [card("Automatic evaluation precision recall entailment", "https://one.example/a", "anysearch")]
        gaps = research._recall_gaps(generic, "automatic citation evaluation precision recall entailment", 1)
        self.assertIn("need_terms_missing_from_results", gaps)
        separate = [card("Citation metrics", "https://one.example/a", "anysearch"),
                    card("Evaluation using entailment", "https://two.example/b", "anysearch")]
        gaps = research._recall_gaps(separate, '"citation evaluation" entailment', 2)
        self.assertIn("quoted_phrase_missing_from_results", gaps)

    def test_direct_search_supplements_many_off_target_web_results(self):
        unrelated = [card(f"Travel guide {i}", f"https://travel.example/{i}", "duckduckgo-html")
                     for i in range(3)]
        primary = card("Enabling Language Models to Generate Citations", "https://github.com/princeton-nlp/ALCE", "bing-rss")
        with mock.patch.object(research, "_duckduckgo", return_value=unrelated), \
                mock.patch.object(research.base, "_bing", return_value=[primary]) as bing, \
                mock.patch.object(research, "_academic_search_enabled", return_value=False), \
                mock.patch.object(research.base, "_url_ok", return_value=True):
            result = research._search_direct("ALCE citation support evaluation", 3, target="ALCE")
        bing.assert_called_once()
        self.assertIn(primary["url"], [row["url"] for row in result["results"]])

    def test_direct_search_keeps_target_in_url_without_requiring_three_query_words(self):
        primary = card("Official implementation", "https://github.com/princeton-nlp/ALCE", "duckduckgo-html")
        with mock.patch.object(research, "_duckduckgo", return_value=[primary]), \
                mock.patch.object(research.base, "_bing", return_value=[]), \
                mock.patch.object(research, "_academic_search_enabled", return_value=False), \
                mock.patch.object(research.base, "_url_ok", return_value=True):
            result = research._search_direct("ALCE citation support evaluation", 3, target="ALCE")
        self.assertEqual([row["url"] for row in result["results"]], [primary["url"]])

    def test_explicit_target_survives_generic_keyword_matches_before_page_limit(self):
        generic = [card(f"Citation support evaluation methods {i}", f"https://example.org/{i}", "anysearch")
                   for i in range(3)]
        primary = card("Enabling Language Models to Generate Citations", "https://github.com/princeton-nlp/ALCE", "anysearch")
        with mock.patch.object(research, "_search_anysearch", return_value=generic + [primary]), \
                mock.patch.object(research.base, "_url_ok", return_value=True), \
                mock.patch.object(research, "_search_direct") as direct:
            result = research._search_base("ALCE citation support evaluation", 3, provider="anysearch", target="ALCE")
        direct.assert_not_called()
        self.assertEqual(result["results"][0]["url"], primary["url"])

    def test_auto_passes_target_to_direct_search(self):
        with mock.patch.object(research, "_search_anysearch", return_value=[]), \
                mock.patch.object(research, "_search_direct", return_value={"results": []}) as direct:
            research._search_base("ALCE citation support evaluation", 3, provider="auto", target="ALCE")
        direct.assert_called_once_with("ALCE citation support evaluation", 3, target="ALCE")


if __name__ == "__main__":
    unittest.main()
