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

    def test_auto_supplements_same_title_leads_without_assuming_they_are_mirrors(self):
        title = "A Comprehensive Benchmark for Deep Research Agents"
        mirrors = [card(title, f"https://mirror{i}.example/paper", "anysearch") for i in range(3)]
        primary = card("ALCE: Citation Support Evaluation", "https://arxiv.org/abs/2305.14627", "arxiv")
        with mock.patch.object(research, "_search_anysearch", return_value=mirrors), \
                mock.patch.object(research.base, "_url_ok", return_value=True), \
                mock.patch.object(research, "_search_direct", return_value={"results": [primary]}) as direct:
            result = research._search_base("deep research citation support evaluation benchmark", 5, provider="auto")
        direct.assert_called_once()
        self.assertIn("similar_titles_require_read", result["recall_checks"]["initial_gaps"])
        self.assertEqual(sum(item["title"] == title for item in result["results"]), 3)
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
        urls = ["https://arxiv.org/abs/2305.14627v2", "https://arxiv.org/pdf/2305.14627v2",
                "https://arxiv.org/html/2305.14627v2",
                "https://doi.org/10.48550/arXiv.2305.14627v2"]
        with mock.patch.object(research.base, "_url_ok", return_value=True):
            rows = research._rank_discovery([card(f"Title {i}", url, "anysearch") for i, url in enumerate(urls)], "citation", 5)
        self.assertEqual(len(rows), 1)

    def test_search_keeps_short_official_title_with_partial_query_coverage(self):
        official = {
            "title": "SQLite FTS5 Extension",
            "url": "https://www.sqlite.org/fts5.html",
            "snippet": "FTS5 provides full-text search for SQLite.",
            "engine": "duckduckgo-html",
        }
        with mock.patch.object(research, "_duckduckgo", return_value=[official]), \
                mock.patch.object(research.base, "_bing", return_value=[]), \
                mock.patch.object(research, "_academic_search_enabled", return_value=False), \
                mock.patch.object(research, "_practitioner_search_enabled", return_value=False), \
                mock.patch.object(research.base, "_url_ok", return_value=True):
            result = research.search("SQLite FTS5 bm25 auxiliary function", provider="direct")
        self.assertIn(official["url"], [row["url"] for row in result["results"]])
        ranking = result["discovery_diagnostics"]["direct_ranking"]
        self.assertEqual(ranking["input_candidates"], 1)
        self.assertEqual(ranking["returned_candidates"], 1)
        self.assertEqual(ranking["low_overlap_candidates_retained"], 1)

    def test_search_ranks_partial_match_ahead_of_unrelated_high_authority_page(self):
        official = {
            "title": "SQLite FTS5 Extension",
            "url": "https://www.sqlite.org/fts5.html",
            "snippet": "FTS5 provides full-text search for SQLite.",
            "engine": "anysearch",
        }
        unrelated = card("Mountain climbing field guide", "https://outdoors.example.edu/guide", "anysearch")
        with mock.patch.object(research, "_search_anysearch", return_value=[unrelated, official]), \
                mock.patch.object(research.base, "_url_ok", return_value=True):
            result = research.search("SQLite FTS5 bm25 auxiliary function", provider="anysearch")
        urls = [row["url"] for row in result["results"]]
        self.assertEqual(set(urls), {official["url"], unrelated["url"]})
        self.assertLess(urls.index(official["url"]), urls.index(unrelated["url"]))

    def test_search_keeps_same_title_documents_at_different_version_urls(self):
        title = "Widget Runtime Release Notes and Compatibility Guide"
        versions = [
            dict(card(title, "https://docs.example/widget/v1/release-notes", "anysearch"),
                 snippet="Version 1 compatibility requirements."),
            dict(card(title, "https://docs.example/widget/v2/release-notes", "anysearch"),
                 snippet="Version 2 corrects the earlier compatibility requirements."),
        ]
        with mock.patch.object(research, "_search_anysearch", return_value=versions), \
                mock.patch.object(research.base, "_url_ok", return_value=True):
            result = research.search("Widget Runtime compatibility", provider="anysearch")
        self.assertEqual({row["url"] for row in result["results"]}, {row["url"] for row in versions})

    def test_search_merges_arxiv_representations_but_keeps_different_paper(self):
        title = "Citation Evaluation for Language Models"
        aliases = [
            "https://arxiv.org/abs/2305.14627v2",
            "https://arxiv.org/pdf/2305.14627v2",
            "https://arxiv.org/html/2305.14627v2",
            "https://doi.org/10.48550/arXiv.2305.14627v2",
        ]
        other_paper = "https://arxiv.org/abs/2401.00001"
        candidates = [card(title, url, "anysearch") for url in aliases + [other_paper]]
        with mock.patch.object(research, "_search_anysearch", return_value=candidates), \
                mock.patch.object(research.base, "_url_ok", return_value=True):
            result = research.search("citation evaluation", provider="anysearch")
        urls = [row["url"] for row in result["results"]]
        self.assertEqual(len(urls), 2)
        self.assertEqual(len(set(urls) & set(aliases)), 1)
        self.assertIn(other_paper, urls)

    def test_arxiv_versions_and_unrelated_urls_are_not_merged(self):
        urls = [
            "https://arxiv.org/abs/2305.14627v1",
            "https://arxiv.org/abs/2305.14627v2",
            "https://arxiv.org/abs/2305.14627",
            "https://docs.example/arxiv.org/abs/2305.14627v1",
            "https://arxiv.org.example/abs/2305.14627v1",
        ]
        with mock.patch.object(research, "_search_anysearch", return_value=[
            card("Citation Evaluation for Language Models", url, "anysearch") for url in urls
        ]), mock.patch.object(research.base, "_url_ok", return_value=True):
            result = research.search("citation evaluation", provider="anysearch")
        self.assertEqual({row["url"] for row in result["results"]}, set(urls))

    def test_direct_search_keeps_different_scholarly_documents_with_same_title(self):
        title = "Citation Evaluation for Language Models"
        papers = [card(title, "https://arxiv.org/abs/2305.14627", "arxiv"),
                  card(title, "https://openalex.org/W123456", "openalex")]
        with mock.patch.object(research, "_duckduckgo", return_value=[]), \
                mock.patch.object(research.base, "_bing", return_value=[]), \
                mock.patch.object(research, "_academic_search_enabled", return_value=True), \
                mock.patch.object(research, "_practitioner_search_enabled", return_value=False), \
                mock.patch.object(research, "_arxiv", return_value=papers[:1]), \
                mock.patch.object(research, "_openalex_exact", return_value=papers[1:]), \
                mock.patch.object(research.base, "_url_ok", return_value=True):
            result = research.search("citation evaluation", provider="direct")
        self.assertEqual({row["url"] for row in result["results"]}, {row["url"] for row in papers})
        self.assertEqual(result["discovery_diagnostics"]["direct_ranking"]["input_candidates"], 2)

    def test_ranking_diagnostics_account_for_policy_duplicates_aliases_and_page_limit(self):
        candidates = [
            card("Citation evaluation", "https://docs.example/a", "anysearch"),
            card("Citation evaluation", "https://docs.example/a#details", "anysearch"),
            card("Citation evaluation paper", "https://arxiv.org/abs/2305.14627v2", "anysearch"),
            card("Citation evaluation paper", "https://arxiv.org/pdf/2305.14627v2", "anysearch"),
            card("Citation evaluation", "https://blocked.example/a", "anysearch"),
            card("Citation evaluation", "https://docs.example/b", "anysearch"),
            card("Citation evaluation", "https://docs.example/c", "anysearch"),
        ]
        with mock.patch.object(research, "_search_anysearch", return_value=candidates), \
                mock.patch.object(research.base, "_url_ok", side_effect=lambda url: "blocked.example" not in url):
            result = research.search("citation evaluation", 2, provider="anysearch")
        ranking = result["discovery_diagnostics"]["combined_ranking"]
        self.assertEqual(ranking["input_candidates"], 7)
        self.assertEqual(ranking["url_duplicates_removed"], 1)
        self.assertEqual(ranking["url_policy_rejected"], 1)
        self.assertEqual(ranking["document_aliases_removed"], 1)
        self.assertEqual(ranking["page_limit_omitted"], 2)
        self.assertEqual(ranking["returned_candidates"], len(result["results"]))

    def test_combined_search_keeps_stronger_same_url_summary_regardless_of_provider_order(self):
        weak = {"title": "SQLite FTS5 Extension", "url": "https://sqlite.org/fts5.html",
                "snippet": "SQLite documentation index.", "engine": "anysearch"}
        rich = dict(weak, snippet="SQLite FTS5 bm25 auxiliary function reference.", engine="duckduckgo-html")
        for first, second in ((weak, rich), (rich, weak)):
            with self.subTest(first=first["snippet"]), \
                    mock.patch.object(research, "_search_anysearch", return_value=[first]), \
                    mock.patch.object(research, "_duckduckgo", return_value=[second]), \
                    mock.patch.object(research.base, "_bing", return_value=[]), \
                    mock.patch.object(research, "_academic_search_enabled", return_value=False), \
                    mock.patch.object(research, "_practitioner_search_enabled", return_value=False), \
                    mock.patch.object(research.base, "_url_ok", return_value=True):
                result = research.search("SQLite FTS5 bm25 auxiliary function", provider="auto")
            self.assertEqual(len(result["results"]), 1)
            self.assertEqual(result["results"][0]["snippet"], rich["snippet"])
            self.assertNotIn("need_terms_missing_from_results", result["recall_checks"]["remaining_gaps"])

    def test_structured_search_reports_recall_gaps_for_final_artifact_candidates(self):
        broad = {"results": [card("Widget documentation", "https://docs.example/widget", "anysearch")],
                 "recall_checks": {"initial_gaps": [], "remaining_gaps": ["too_few_distinct_documents"]}}
        repositories = [card("Widget core", "https://github.com/team/widget-core", "github"),
                        card("Widget examples", "https://github.com/team/widget-examples", "github")]
        with mock.patch.object(research, "_search_base", return_value=broad), \
                mock.patch.object(research, "_kaggle_topic_cards", return_value=[]), \
                mock.patch.object(research, "_github_repositories", return_value=repositories):
            result = research.search("Widget repository", target="Widget", artifact="repository",
                                     proof="Author implementation", provider="hybrid")
        self.assertEqual(len(result["results"]), 3)
        self.assertNotIn("too_few_distinct_documents", result["recall_checks"]["remaining_gaps"])

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

    def test_named_chinese_repository_candidates_survive_artifact_routing(self):
        for target in ("轻量检索", "机器翻译", "轻量检索 2.0", "轻量检索 2026"):
            repositories = [
                {"full_name": "author/reference", "html_url": "https://github.com/author/reference",
                 "description": f"{target}的官方参考实现", "stargazers_count": 0},
                {"full_name": "author/examples", "html_url": "https://github.com/author/examples",
                 "description": f"{target}的使用示例", "stargazers_count": 0},
                {"full_name": "other/unrelated", "html_url": "https://github.com/other/unrelated",
                 "description": "轻量分类、检索索引与机器学习示例", "stargazers_count": 0},
            ]
            with self.subTest(target=target), \
                    mock.patch.object(research.base, "_get", return_value=(
                        json.dumps({"items": repositories}), "application/json"
                    )), \
                    mock.patch.object(research, "_search_base", return_value={"results": []}):
                result = research.search(f"{target} 仓库", target=target, artifact="repository",
                                         proof="作者实现", provider="hybrid")
            self.assertEqual({row["url"] for row in result["results"]},
                             {row["html_url"] for row in repositories[:2]})
            self.assertNotIn("no_target_match", result["recall_checks"]["remaining_gaps"])

    def test_chinese_target_requires_the_complete_name_and_version(self):
        cases = [
            ("轻量检索", "轻量分类与检索索引"),
            ("机器翻译", "机器学习与自动翻译"),
            ("轻量检索 2.0", "轻量检索 2.1"),
            ("轻量检索 2026", "机器翻译 2026"),
            ("轻量检索 2026", "轻量检索 2025"),
        ]
        for target, title in cases:
            with self.subTest(target=target, title=title):
                unrelated = card(title, "https://github.com/other/project", "github")
                self.assertFalse(research._target_matched(unrelated, target))

    def test_latin_target_matching_preserves_abbreviations_versions_and_slugs(self):
        for target, title, matched in (
            ("ALCE", "Official ALCE implementation", True),
            ("ALCE", "An unrelated citation implementation", False),
            ("BrowseComp-Plus", "BrowseComp Plus reference", True),
            ("Widget 2026", "Widget 2026 reference", True),
            ("Widget 2026", "Widget 2025 reference", False),
        ):
            with self.subTest(target=target, title=title):
                candidate = card(title, "https://github.com/author/project", "github")
                self.assertEqual(research._target_matched(candidate, target), matched)

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
