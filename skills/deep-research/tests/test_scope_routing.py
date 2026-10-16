from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
import urllib.parse
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "research.py"
SPEC = importlib.util.spec_from_file_location("test_scope_routing_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)

TARGET = "Kaggle Signal Classification"
OFFICIAL_URL = "https://www.kaggle.com/competitions/signal-classification"
METADATA_URL = research.KAGGLE_GET_COMPETITION + "?competitionName=signal-classification"


class ScopeRoutingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="deep-research-scope-tests-")
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        environment = mock.patch.dict(os.environ, {
            "DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
            "DEEP_RESEARCH_SOURCE_POLICY": "web_allowed",
            "DEEP_RESEARCH_SEARCH_PROVIDER": "anysearch",
            "DRBENCH_MAX_SEARCH": "", "DEEP_RESEARCH_MAX_SEARCH": "0",
            "DRBENCH_MAX_READ": "", "DEEP_RESEARCH_MAX_READ": "0",
            "DRBENCH_MAX_REQUESTS": "", "DEEP_RESEARCH_MAX_REQUESTS": "0",
        })
        environment.start()
        self.addCleanup(environment.stop)
        for method in ("get", "post"):
            patcher = mock.patch.object(research.base.requests, method,
                                        side_effect=AssertionError("network is forbidden in scope fixtures"))
            sender = patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(sender.assert_not_called)

    def official_search(self, url=OFFICIAL_URL):
        candidate = {
            "title": f"{TARGET} official Evaluation and Data",
            "url": url,
            "snippet": "Evaluation metric is root mean squared error. Train data schema includes target labels.",
            "engine": "anysearch",
        }
        with mock.patch.object(research, "_search_anysearch", return_value=[candidate]) as provider, \
                mock.patch.object(research, "_search_direct") as direct, \
                mock.patch.object(research, "_kaggle_official_card") as metadata, \
                mock.patch.object(research, "_kaggle_topic_cards") as topics, \
                mock.patch.object(research, "_github_repositories") as repositories:
            result = research.search(f"{TARGET} official metric data", 5, target=TARGET,
                                     artifact="official", proof="Official task metric and schema",
                                     provider="anysearch")
        provider.assert_called_once()
        for lane in (direct, metadata, topics, repositories):
            lane.assert_not_called()
        return result

    def test_local_only_new_search_does_not_change_scope_spend_search_or_call_providers(self):
        research._save_scope_state({"status": "open", "target": TARGET, "searches": 1,
                                    "urls": [OFFICIAL_URL]})
        scope_before = research._scope_state_path().read_bytes()
        counts_before = research.base.metrics.snapshot()
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}), \
                mock.patch.object(research, "_search_anysearch") as anysearch, \
                mock.patch.object(research, "_search_direct") as direct, \
                mock.patch.object(research, "_kaggle_official_card") as metadata, \
                mock.patch.object(research, "_kaggle_topic_cards") as topics, \
                mock.patch.object(research, "_github_repositories") as repositories:
            for mode in research.SEARCH_PROVIDERS:
                with self.subTest(provider=mode), self.assertRaisesRegex(RuntimeError, "local_only"):
                    research.search(f"{TARGET} official metric data", 5, target=TARGET,
                                    artifact="official", proof="Official task metric and schema",
                                    provider=mode)
        self.assertEqual(research._scope_state_path().read_bytes(), scope_before)
        self.assertEqual(research.base.metrics.snapshot(), counts_before)
        for lane in (anysearch, direct, metadata, topics, repositories):
            lane.assert_not_called()

    def test_anysearch_official_registers_candidates_without_bypassing_scope_read(self):
        result = self.official_search()
        state = research._load_scope_state()
        self.assertEqual(state["status"], "open")
        self.assertEqual(state["searches"], 1)
        self.assertIn(OFFICIAL_URL, state["urls"])
        self.assertNotIn("confidence", state)
        self.assertNotIn("pivots", state)
        self.assertNotIn("scope_state", result)
        self.assertEqual(research._load_citations(), [])
        counts_before = research.base.metrics.snapshot()
        with mock.patch.object(research, "_search_anysearch") as provider, \
                mock.patch.object(research, "_search_direct") as direct, \
                mock.patch.object(research, "_github_repositories") as repositories:
            with self.assertRaisesRegex(RuntimeError, "scope gate is open"):
                research.search(f"{TARGET} winning code", 5, target=TARGET, artifact="code",
                                proof="Winning implementation", provider="anysearch")
        for lane in (provider, direct, repositories):
            lane.assert_not_called()
        self.assertEqual(research._load_scope_state()["status"], "open")
        self.assertEqual(research.base.metrics.snapshot(), counts_before)

    def test_official_body_read_closes_scope_and_first_solution_requires_its_pivot(self):
        self.official_search()
        body = (f"{TARGET}\nEvaluation metric is root mean squared error.\n"
                "Training data schema includes signal_id and target labels.\n"
                "Submission data must include signal_id and target columns.")
        document = {"url": OFFICIAL_URL, "title": TARGET, "windows": [body], "_body": body,
                    "content_scope": "full_text", "content_type": "html"}
        with mock.patch.object(research, "_kaggle_official_pages_url",
                               side_effect=RuntimeError("offline HTML fallback fixture")), \
                mock.patch.object(research.v1, "read_page", return_value=document) as reader:
            saved = research._ref_read(OFFICIAL_URL, [], 1000)
        reader.assert_called_once()
        self.assertEqual(saved["content_scope"], "full_text")
        self.assertEqual(saved["scope_state"]["status"], "closed")
        self.assertEqual(saved["scope_state"]["confidence"], "high")
        self.assertTrue(saved["scope_state"]["pivot_required"])
        self.assertIn("root mean squared error", saved["scope_state"]["pivots"])
        self.assertEqual(research.workspace.open_source(saved["ref"])["text"], body)

        solution = {"title": f"{TARGET} root mean squared error implementation",
                    "url": "https://github.com/author/signal-classification", "snippet": "Author code"}
        with mock.patch.object(research, "_search_anysearch", return_value=[solution]) as provider, \
                mock.patch.object(research, "_search_direct") as direct, \
                mock.patch.object(research, "_github_repositories") as repositories:
            with self.assertRaisesRegex(RuntimeError, "first solution query must copy"):
                research.search(f"{TARGET} winning code", 5, target=TARGET, artifact="code",
                                proof="Winning implementation", provider="anysearch")
            provider.assert_not_called()
            self.assertTrue(research._load_scope_state()["pivot_required"])
            result = research.search(f"{TARGET} root mean squared error winning code", 5,
                                     target=TARGET, artifact="code", proof="Winning implementation",
                                     provider="anysearch")
        provider.assert_called_once()
        direct.assert_not_called()
        repositories.assert_not_called()
        self.assertEqual(result["results"][0]["url"], solution["url"])
        state = research._load_scope_state()
        self.assertFalse(state["pivot_required"])
        self.assertEqual(state["pivot_used"], "root mean squared error")

    def test_valid_pivot_is_not_consumed_when_logical_search_budget_is_exhausted(self):
        research._save_scope_state({"status": "closed", "target": TARGET, "pivot_required": True,
                                    "pivots": ["root mean squared error"]})
        research.base._reserve("search")
        scope_before = research._scope_state_path().read_bytes()
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_MAX_SEARCH": "1"}), \
                mock.patch.object(research, "_search_anysearch") as provider:
            with self.assertRaisesRegex(RuntimeError, "search budget exhausted"):
                research.search(f"{TARGET} root mean squared error winning code", 5,
                                target=TARGET, artifact="code", proof="Winning implementation",
                                provider="anysearch")
        provider.assert_not_called()
        self.assertEqual(research._scope_state_path().read_bytes(), scope_before)

    def test_third_unread_official_search_does_not_consume_a_search_attempt(self):
        self.official_search()
        self.official_search()
        scope_before = research._scope_state_path().read_bytes()
        counts_before = research.base.metrics.snapshot()
        with self.assertRaisesRegex(RuntimeError, "scope gate still open after two searches"):
            self.official_search()
        self.assertEqual(research._scope_state_path().read_bytes(), scope_before)
        self.assertEqual(research.base.metrics.snapshot(), counts_before)

    def test_metadata_read_keeps_partial_scope_and_unread_official_body_limitations(self):
        self.official_search(METADATA_URL)
        body = (f"{TARGET} official metadata\nEvaluation metric: RMSE.\n"
                "Submission data schema contains target labels.")
        document = {"url": METADATA_URL, "title": TARGET, "windows": [body], "_body": body,
                    "content_scope": "metadata", "content_type": "kaggle_official_metadata"}
        with mock.patch.object(research.v1, "read_page", return_value=document) as reader:
            saved = research._ref_read(METADATA_URL, [], 1000)
        reader.assert_called_once()
        self.assertEqual(saved["content_scope"], "metadata")
        scope = saved["scope_state"]
        self.assertEqual((scope["status"], scope["confidence"], scope["coverage"]),
                         ("closed", "high", "partial"))
        self.assertTrue(scope["report_required"])
        self.assertIn("Evaluation formula and data semantics remain unread", scope["unverified"])
        self.assertEqual(research.workspace.open_source(saved["ref"])["content_scope"], "metadata")

    def save_closed_competition(self, target, slug, canonical=None):
        research._save_scope_state({"status": "closed", "target": target,
                                    "canonical_target": canonical or target,
                                    "competition_name": slug, "pivot_required": False})

    def topic_api_response(self, slug, title):
        metadata = {"competitionName": slug, "title": title.removeprefix("Kaggle "), "id": 42}

        def retrieve(url, max_bytes=3_000_000):
            parsed = urllib.parse.urlparse(url)
            arguments = urllib.parse.parse_qs(parsed.query)
            if parsed.path.endswith("competitions.CompetitionService/GetCompetition"):
                return metadata if arguments.get("competitionName") == [slug] else {}
            if parsed.path.endswith("competitions.CompetitionService/ListCompetitions"):
                return {"competitions": [metadata]}
            if "/api/v1/competitions/" in parsed.path and parsed.path.endswith("/topics"):
                return {"topics": [{"id": 17, "title": "1st place solution", "votes": 10}]
                        if arguments.get("page") == ["1"] else []}
            raise AssertionError(f"unexpected fixture API request: {url}")

        return retrieve

    def assert_topic_identity_rediscovered(self, query, slug, title):
        with mock.patch.object(research, "_kaggle_json",
                               side_effect=self.topic_api_response(slug, title)) as api:
            cards = research._kaggle_topic_cards(query, 5)
        self.assertTrue(any("competitions.CompetitionService/" in call.args[0]
                            for call in api.call_args_list), "a different identity must be rediscovered")
        self.assertEqual([card["url"] for card in cards],
                         [f"https://www.kaggle.com/competitions/{slug}/discussion/17"])
        topic_urls = [call.args[0] for call in api.call_args_list if "/topics?" in call.args[0]]
        self.assertTrue(topic_urls)
        self.assertTrue(all(f"/{slug}/topics?" in url for url in topic_urls))

    def test_topic_reuse_rejects_same_competition_name_with_different_year(self):
        self.save_closed_competition(f"{TARGET} 2024", "signal-classification-2024")
        self.assert_topic_identity_rediscovered(f"{TARGET} 2025 solution",
                                               "signal-classification-2025", f"{TARGET} 2025")

    def test_topic_reuse_rejects_conflicting_explicit_slug_despite_matching_title(self):
        self.save_closed_competition(TARGET, "signal-classification")
        slug = "signal-classification-alternate"
        query = f"{TARGET} solution https://www.kaggle.com/competitions/{slug}"
        self.assert_topic_identity_rediscovered(query, slug, f"{TARGET} Alternate")

    def test_topic_reuse_rejects_shared_google_research_words_for_another_competition(self):
        self.save_closed_competition("Kaggle Google Research Football", "google-football")
        target = "Kaggle Google Research Identify Contrails"
        self.assert_topic_identity_rediscovered(f"{target} solution", "google-research-identify-contrails",
                                               target)

    def test_topic_reuse_accepts_complete_identity_and_matching_explicit_slug(self):
        target = f"{TARGET} 2024"
        canonical = f"{TARGET} Challenge 2024"
        slug = "signal-classification-2024"
        self.save_closed_competition(target, slug, canonical)
        queries = [f"{target} solution", f"{canonical} solution",
                   f"https://www.kaggle.com/competitions/{slug}?search=solution"]
        for query in queries:
            with self.subTest(query=query), \
                    mock.patch.object(research, "_kaggle_json",
                                      side_effect=self.topic_api_response(slug, canonical)) as api:
                cards = research._kaggle_topic_cards(query, 5)
                self.assertEqual([card["url"] for card in cards],
                                 [f"https://www.kaggle.com/competitions/{slug}/discussion/17"])
                self.assertTrue(all("/topics?" in call.args[0] for call in api.call_args_list),
                                "the verified competition identity should not need metadata discovery")

    def test_short_query_with_new_year_target_does_not_inherit_old_canonical_identity(self):
        self.save_closed_competition(f"{TARGET} 2024", "signal-classification-2024")
        requested_target = f"{TARGET} 2025"
        routed_target = research._scope_target(requested_target)
        self.assertEqual(routed_target, requested_target)
        self.assert_topic_identity_rediscovered(f"{routed_target} solution",
                                               "signal-classification-2025", requested_target)


if __name__ == "__main__":
    unittest.main()
