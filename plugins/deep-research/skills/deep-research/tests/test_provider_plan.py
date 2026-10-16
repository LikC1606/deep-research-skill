from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.parse
from unittest import mock

from requests.cookies import RequestsCookieJar


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "research.py"
SPEC = importlib.util.spec_from_file_location("test_provider_plan_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)


class Response:
    """Small HTTP response that exercises the shared metering adapters."""

    def __init__(self, content=b"", *, url, status=200):
        self.content, self.url, self.status_code = content, url, status
        self.encoding, self.headers = "utf-8", {}
        self.cookies = RequestsCookieJar()
        self.closed = False

    def json(self):
        return json.loads(self.content)

    def iter_content(self, size):
        yield self.content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise research.base.requests.HTTPError(str(self.status_code))

    def close(self):
        self.closed = True


class Transport:
    def __init__(self, *, repository_count=2, github_status=200):
        self.repository_count = repository_count
        self.github_status = github_status
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname == "api.github.com":
            items = [{
                "full_name": f"widget-dev/widget-{index}",
                "html_url": f"https://github.com/widget-dev/widget-{index}",
                "description": "Widget benchmark official repository implementation code and examples",
                "stargazers_count": 0,
            } for index in range(self.repository_count)]
            return Response(json.dumps({"items": items}).encode(), url=url, status=self.github_status)
        if parsed.hostname == "html.duckduckgo.com":
            return Response(b'<div class="no-results">No results found</div>', url=url)
        if parsed.hostname == "www.bing.com":
            return Response(b"<rss><channel/></rss>", url=url)
        if parsed.hostname == "www.kaggle.com":
            if "/topics" in parsed.path:
                value = {"topics": []}
            else:
                value = {
                    "id": 42, "competitionName": "widget-trial", "title": "Widget Trial",
                    "briefDescription": "Widget Trial prediction benchmark",
                    "evaluationAlgorithm": {"name": "RMSE"},
                }
            return Response(json.dumps(value).encode(), url=url)
        raise AssertionError(f"unexpected HTTP GET: {url}")

    def post(self, url, **kwargs):
        self.calls.append(("POST", url))
        if url != research.anysearch.ENDPOINT:
            raise AssertionError(f"unexpected HTTP POST: {url}")
        query = kwargs["json"]["params"]["arguments"]["query"]
        source_url = ("https://www.kaggle.com/competitions/widget-trial/overview"
                      if "kaggle" in query.casefold() else "https://docs.example/widget")
        text = "\n\n".join(
            f"### {index + 1}. {query} official source {index + 1}\n"
            f"- **URL**: {source_url}?source={index}\n"
            f"{query}: metric RMSE, train and test fields, target and submission format."
            for index in range(2)
        )
        payload = {"result": {"content": [{"type": "text", "text": text}]}}
        return Response(json.dumps(payload).encode(), url=url)

    def github_requests(self):
        return [url for method, url in self.calls
                if method == "GET" and urllib.parse.urlsplit(url).hostname == "api.github.com"]


class KaggleTransport(Transport):
    def __init__(self, *, lookup_status=200, topic_status=200):
        super().__init__()
        self.lookup_status, self.topic_status = lookup_status, topic_status

    def get(self, url, **kwargs):
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname != "www.kaggle.com":
            return super().get(url, **kwargs)
        self.calls.append(("GET", url))
        status = 200
        if "GetCompetition" in parsed.path:
            value, status = {}, self.lookup_status
        elif "ListCompetitions" in parsed.path:
            value = {"competitions": [{"id": 42, "competitionName": "widget-trial",
                                       "title": "Widget Trial"}]}
        elif "/topics" in parsed.path:
            topics = [{"id": 7, "title": "1st place winning solution", "votes": 3}]
            value = {"topics": topics if urllib.parse.parse_qs(parsed.query)["page"] == ["1"] else []}
            status = self.topic_status
        else:
            raise AssertionError(f"unexpected Kaggle GET: {url}")
        return Response(json.dumps(value).encode(), url=url, status=status)


class ProviderPlanTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="deep-research-provider-plan-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        environment = mock.patch.dict(os.environ, {
            "DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
            "DRBENCH_MAX_SEARCH": "", "DRBENCH_MAX_READ": "", "DRBENCH_MAX_REQUESTS": "",
            "DEEP_RESEARCH_MAX_SEARCH": "0", "DEEP_RESEARCH_MAX_READ": "0", "DEEP_RESEARCH_MAX_REQUESTS": "0",
            "DEEP_RESEARCH_SOURCE_POLICY": "web_allowed", "DRBENCH_ACADEMIC_SEARCH": "0",
            "DRBENCH_PRACTITIONER_SEARCH": "0",
        })
        environment.start()
        self.addCleanup(environment.stop)
        safe = mock.patch.object(research.base, "_url_ok", return_value=True)
        safe.start()
        self.addCleanup(safe.stop)

    @contextmanager
    def run_with(self, label, transport, **environment):
        with mock.patch.dict(os.environ, {
            "DEEP_RESEARCH_RUN_DIR": str(self.root / label), **environment,
        }), mock.patch.object(research.base.requests, "get", side_effect=transport.get), \
                mock.patch.object(research.base.requests, "post", side_effect=transport.post):
            yield

    def test_repository_or_code_gets_the_first_request_under_a_single_request_cap(self):
        for provider in ("direct", "auto", "hybrid"):
            for artifact in ("repository", "code"):
                with self.subTest(provider=provider, artifact=artifact):
                    transport = Transport()
                    with self.run_with(f"{provider}-{artifact}", transport,
                                       DEEP_RESEARCH_MAX_SEARCH="1", DEEP_RESEARCH_MAX_REQUESTS="1"):
                        first = research._ref_search("Widget", 5, "Widget", artifact,
                                                     "official author implementation", provider)
                        second = research._ref_search("Widget", 5, "Widget", artifact,
                                                      "official author implementation", provider)
                        counts = research.base.metrics.snapshot()
                    self.assertEqual(len(transport.calls), 1)
                    self.assertEqual(urllib.parse.urlsplit(transport.calls[0][1]).hostname, "api.github.com")
                    self.assertTrue(first["results"])
                    self.assertEqual(first["results"][0]["engine"], "github-repositories")
                    self.assertFalse(first["cache_hit"])
                    self.assertTrue(second["cache_hit"])
                    self.assertEqual((counts["search"], counts["requests"]), (1, 1))

    def test_explicit_anysearch_has_no_direct_http_for_any_artifact(self):
        target = "Kaggle Widget Trial"
        for artifact in research.ARTIFACT_TYPES:
            with self.subTest(artifact=artifact):
                transport = Transport()
                with self.run_with(artifact, transport):
                    if artifact != "official":
                        # Fixture for a completed official Read; do not bypass the guard.
                        research._save_scope_state({
                            "status": "closed", "target": target, "canonical_target": target,
                            "competition_name": "widget-trial", "scope_confidence": "high",
                            "closed_by": "https://www.kaggle.com/competitions/widget-trial/overview",
                            "pivot_required": False,
                        })
                    result = research.search(
                        f"{target} metric train target implementation", 5, target, artifact,
                        "official source and implementation", "anysearch",
                    )
                    counts = research.base.metrics.snapshot()
                self.assertTrue(result["results"])
                self.assertEqual(transport.calls, [("POST", research.anysearch.ENDPOINT)])
                self.assertEqual(result["providers_used"], ["anysearch"])
                self.assertEqual((counts["search"], counts["requests"]), (1, 1))

    def test_anysearch_still_obeys_an_open_kaggle_scope_gate(self):
        transport = Transport()
        target = "Kaggle Widget Trial"
        with self.run_with("open-scope", transport):
            research.search(f"{target} metric train target", 5, target, "official",
                            "official metric and data fields", "anysearch")
            sent = list(transport.calls)
            with self.assertRaisesRegex(RuntimeError, "scope gate is open"):
                research.search(f"{target} implementation", 5, target, "code",
                                "author implementation", "anysearch")
            self.assertEqual(transport.calls, sent)

    def test_normalized_github_query_is_sent_once_after_a_success(self):
        transport = Transport(repository_count=1)
        with self.run_with("github-success", transport, DRBENCH_PRACTITIONER_SEARCH="1"):
            result = research.search("Widget benchmark repository", 4, "Widget benchmark",
                                     "repository", "author implementation", "direct")
        requests = transport.github_requests()
        keys = Counter(tuple(sorted((name, tuple(values)) for name, values in
                                    urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()))
                       for url in requests)
        self.assertTrue(result["results"])
        self.assertTrue(keys)
        self.assertTrue(all(count == 1 for count in keys.values()),
                        f"same normalized GitHub search was repeated: {requests}")

    def test_normalized_github_failure_is_not_an_implicit_retry(self):
        transport = Transport(github_status=503)
        with self.run_with("github-error", transport, DRBENCH_PRACTITIONER_SEARCH="1"):
            for attempt in range(2):
                with self.assertRaises(research.SearchError):
                    research.search("Widget benchmark repository", 4, "Widget benchmark",
                                    "repository", "author implementation", "direct")
                self.assertEqual(len(transport.github_requests()), attempt + 1)
            self.assertEqual(research.base.metrics.snapshot()["search"], 2)
        requests = transport.github_requests()
        self.assertEqual(len(requests), 2, f"failed GitHub search was implicitly retried: {requests}")

    def test_normalized_empty_github_response_is_also_reused(self):
        transport = Transport(repository_count=0)
        with self.run_with("github-empty", transport, DRBENCH_PRACTITIONER_SEARCH="1"):
            result = research.search("Widget benchmark repository", 4, "Widget benchmark",
                                     "repository", "author implementation", "direct")
        self.assertEqual(result["status"], "empty")
        self.assertEqual(len(transport.github_requests()), 1)
        self.assertTrue(any(row["provider"] == "github" and row.get("reused") and row["status"] == "empty"
                            for row in result["provider_results"]))

    def test_hybrid_attempts_broad_search_after_sufficient_repository_results(self):
        transport = Transport(repository_count=2)
        with self.run_with("hybrid", transport):
            result = research.search("Widget", 5, "Widget", "repository",
                                     "official author implementation", "hybrid")
            counts = research.base.metrics.snapshot()
        self.assertEqual(urllib.parse.urlsplit(transport.calls[0][1]).hostname, "api.github.com")
        self.assertTrue(any(urllib.parse.urlsplit(url).hostname != "api.github.com"
                            for _, url in transport.calls[1:]))
        self.assertGreaterEqual(len(result["results"]), 2)
        self.assertEqual(counts["search"], 1)

    def test_sparse_repository_results_trigger_broad_search(self):
        for provider in ("direct", "auto"):
            with self.subTest(provider=provider):
                transport = Transport(repository_count=1)
                with self.run_with(f"sparse-{provider}", transport):
                    result = research.search("Widget", 5, "Widget", "repository",
                                             "official author implementation", provider)
                    counts = research.base.metrics.snapshot()
                self.assertEqual(urllib.parse.urlsplit(transport.calls[0][1]).hostname, "api.github.com")
                self.assertTrue(any(urllib.parse.urlsplit(url).hostname != "api.github.com"
                                    for _, url in transport.calls[1:]))
                self.assertTrue(result["results"])
                self.assertEqual(counts["search"], 1)

    def test_budget_failure_after_a_repository_keeps_partial_results(self):
        transport = Transport(repository_count=1)
        with self.run_with("partial", transport, DEEP_RESEARCH_MAX_REQUESTS="1"):
            result = research.search("Widget", 5, "Widget", "repository",
                                     "official author implementation", "hybrid")
            counts = research.base.metrics.snapshot()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["results"][0]["url"], "https://github.com/widget-dev/widget-0")
        self.assertTrue(any("budget exhausted" in row["error"] for row in result["provider_errors"]))
        self.assertEqual((counts["search"], counts["requests"], len(transport.calls)), (1, 1, 1))

    def test_kaggle_metadata_listing_and_pages_are_reused_within_one_hybrid_search(self):
        transport = KaggleTransport()
        target = "Kaggle Widget Trial"
        with self.run_with("kaggle-reuse", transport, DRBENCH_PRACTITIONER_SEARCH="1"):
            result = research.search(f"{target} repository", 4, target, "repository",
                                     "winning implementation", "hybrid")
        calls = Counter(url for _, url in transport.calls if "www.kaggle.com" in url)
        self.assertTrue(calls)
        self.assertTrue(all(count == 1 for count in calls.values()), calls)
        reused = {row["provider"] for row in result["provider_results"] if row.get("reused")}
        self.assertEqual(reused, {"kaggle-metadata", "kaggle-listing", "kaggle-topics", "github"})
        self.assertTrue(any(row["engine"] == "kaggle-competition-topics" for row in result["results"]))

    def test_kaggle_failed_requests_are_reused_but_a_new_search_can_retry(self):
        transport = KaggleTransport(lookup_status=503, topic_status=503)
        target = "Kaggle Widget Trial"
        with self.run_with("kaggle-errors", transport, DRBENCH_PRACTITIONER_SEARCH="1"):
            results = [research.search(f"{target} repository", 4, target, "repository",
                                       "winning implementation", "hybrid") for _ in range(2)]
            counts = research.base.metrics.snapshot()
        calls = Counter(url for _, url in transport.calls if "www.kaggle.com" in url)
        self.assertTrue(all(count == 2 for count in calls.values()), calls)
        for result in results:
            self.assertEqual(result["status"], "partial")
            self.assertEqual(Counter(row["provider"] for row in result["provider_errors"]),
                             {"kaggle-metadata": 1, "kaggle-topics": 1})
            self.assertTrue(any(row.get("reused") and row["status"] == "error"
                                for row in result["provider_results"]))
        self.assertEqual(counts["search"], 2)

    def test_confirmed_kaggle_scope_uses_its_slug_without_rediscovery(self):
        transport = KaggleTransport(lookup_status=404)
        target = "Kaggle Widget Trial"
        with self.run_with("kaggle-confirmed", transport, DRBENCH_PRACTITIONER_SEARCH="1"):
            research._save_scope_state({"status": "closed", "target": target, "canonical_target": target,
                                        "competition_name": "widget-trial", "pivot_required": False})
            result = research.search(f"{target} solution macro F1 writeup", 4, target, "writeup",
                                     "winning method", "hybrid")
        self.assertEqual(result["status"], "ok")
        kaggle_urls = [url for _, url in transport.calls if "www.kaggle.com" in url]
        self.assertEqual(len(kaggle_urls), 2)
        self.assertTrue(all("/competitions/widget-trial/topics?" in url for url in kaggle_urls))
        self.assertTrue(any(row["engine"] == "kaggle-competition-topics" for row in result["results"]))

    def test_unrelated_closed_scope_does_not_replace_the_current_target(self):
        transport = Transport()
        target = "Kaggle Widget Trial"
        with self.run_with("kaggle-unrelated", transport, DRBENCH_PRACTITIONER_SEARCH="1"):
            research._save_scope_state({"status": "closed", "target": "Kaggle Ghost Event",
                                        "canonical_target": "Kaggle Ghost Event", "competition_name": "ghost-event",
                                        "pivot_required": True, "pivots": ["another metric"]})
            result = research.search(f"{target} solution writeup", 4, target, "writeup",
                                     "winning implementation", "hybrid")
            self.assertTrue(research._load_scope_state()["pivot_required"])
        self.assertEqual(result["status"], "ok")
        kaggle_urls = [url for _, url in transport.calls if "www.kaggle.com" in url]
        self.assertTrue(any("GetCompetition?competitionName=widget-trial" in url for url in kaggle_urls))
        self.assertTrue(any("/competitions/widget-trial/topics?" in url for url in kaggle_urls))
        self.assertNotIn("ghost", " ".join(url for _, url in transport.calls).casefold())

    def test_parallel_batch_keeps_execution_contexts_and_budgets_separate(self):
        transport = Transport()
        barrier = threading.Barrier(2, timeout=5)

        def get(url, **kwargs):
            self.assertEqual(urllib.parse.urlsplit(url).hostname, "api.github.com")
            barrier.wait()
            return transport.get(url, **kwargs)

        plan = self.root / "parallel.json"
        plan.write_text(json.dumps({"searches": [
            {"query": "Widget repository", "target": "Widget", "artifact": "repository",
             "proof": "author code", "reason": "Find repository"},
            {"query": "Widget code", "target": "Widget", "artifact": "code",
             "proof": "implementation", "reason": "Find implementation"},
        ]}), encoding="utf-8")
        with self.run_with("parallel", transport, DEEP_RESEARCH_MAX_SEARCH="2", DEEP_RESEARCH_MAX_REQUESTS="2"), \
                mock.patch.object(research.base.requests, "get", side_effect=get):
            first = research._search_batch(str(plan), 4, "direct", workers=2)
            replay = research._search_batch(str(plan), 4, "direct", workers=2)
            counts = research.base.metrics.snapshot()
        self.assertEqual((first["status"], first["workers"]), ("complete", 2))
        self.assertEqual((counts["search"], counts["requests"], len(transport.calls)), (2, 2, 2))
        for row in first["results"]:
            self.assertFalse(row["result"]["cache_hit"])
            self.assertFalse(row["result"]["provider_results"][0].get("reused", False))
            self.assertTrue(all(not card["citable"] for card in row["result"]["results"]))
        self.assertTrue(all(row["result"]["cache_hit"] for row in replay["results"]))

    def test_specialized_search_records_one_final_search_event(self):
        for mode, request_cap in (("direct", "0"), ("hybrid", "1")):
            with self.subTest(provider=mode):
                transport = Transport()
                with self.run_with(f"events-{mode}", transport, DEEP_RESEARCH_MAX_REQUESTS=request_cap):
                    result = research.search("Widget", 5, "Widget", "repository",
                                             "official author implementation", mode)
                    events = [json.loads(line) for line in
                              (self.root / f"events-{mode}" / ".adapter-events.jsonl").read_text().splitlines()]
                searches = [row for row in events if row["kind"] == "search"]
                self.assertEqual(len(searches), 1)
                self.assertEqual(searches[0]["results"], result["results"])
                self.assertEqual(searches[0]["status"], result["status"])


if __name__ == "__main__":
    unittest.main()
