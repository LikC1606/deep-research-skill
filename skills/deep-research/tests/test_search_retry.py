from __future__ import annotations

from collections import Counter
import html
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from unittest import mock

from requests.cookies import RequestsCookieJar


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "research.py"
SPEC = importlib.util.spec_from_file_location("test_search_retry_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)


class Response:
    def __init__(self, body: bytes, url: str, status: int = 200):
        self.content, self.url, self.status_code = body, url, status
        self.encoding, self.headers = "utf-8", {}
        self.cookies = RequestsCookieJar()

    def iter_content(self, size):
        yield self.content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise research.base.requests.HTTPError(str(self.status_code))

    def json(self):
        return json.loads(self.content)

    def close(self):
        pass


class Transport:
    """Exercise the real provider parsers and shared HTTP budget, without sockets."""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.bing_ok = False
        self.bing_empty = False
        self.anysearch_ok = False
        self.github = {}
        self.topics = {}
        self.bing_barrier = None

    def remember(self, method, url, query=""):
        with self.lock:
            self.calls.append({"method": method, "url": url, "query": query})

    def get(self, url, **kwargs):
        parsed = urllib.parse.urlsplit(url)
        args = urllib.parse.parse_qs(parsed.query)
        query = args.get("q", [""])[0]
        self.remember("GET", url, query)
        if parsed.hostname == "html.duckduckgo.com":
            encoded = urllib.parse.quote(query, safe="")
            title = html.escape(query)
            body = (f'<div class="result"><a class="result__a" href="https://direct.example/{encoded}">'
                    f'{title} official direct source</a><div class="result__snippet">{title}</div></div>')
            return Response(body.encode(), url)
        if parsed.hostname == "www.bing.com":
            if self.bing_barrier is not None:
                self.bing_barrier.wait(timeout=5)
            if not self.bing_ok:
                return Response(b"", url, 429)
            if self.bing_empty:
                return Response(b"<rss><channel/></rss>", url)
            title, encoded = html.escape(query), urllib.parse.quote(query, safe="")
            body = (f'<rss><channel><item><title>{title} recovered Bing source</title>'
                    f'<link>https://bing.example/{encoded}</link><description>{title}</description>'
                    '</item></channel></rss>')
            return Response(body.encode(), url)
        if parsed.hostname == "api.github.com":
            value = self.github.get(query, [])
            return Response(json.dumps({"items": value if isinstance(value, list) else []}).encode(),
                            url, 200 if isinstance(value, list) else value)
        if parsed.hostname == "www.kaggle.com" and parsed.path.endswith("/topics"):
            value = self.topics.get(int(args["page"][0]), [])
            return Response(json.dumps({"topics": value if isinstance(value, list) else []}).encode(),
                            url, 200 if isinstance(value, list) else value)
        raise AssertionError(f"unexpected HTTP GET: {url}")

    def post(self, url, **kwargs):
        if url != research.anysearch.ENDPOINT:
            raise AssertionError(f"unexpected HTTP POST: {url}")
        query = kwargs["json"]["params"]["arguments"]["query"]
        self.remember("POST", url, query)
        if not self.anysearch_ok:
            return Response(b"", url, 503)
        encoded = urllib.parse.quote(query, safe="")
        text = "\n\n".join(
            f"### {index}. {query} recovered AnySearch source {index}\n"
            f"- **URL**: https://aggregated.example/{encoded}/{index}\n{query}"
            for index in (1, 2)
        )
        return Response(json.dumps({"result": {"content": [{"type": "text", "text": text}]}}).encode(), url)


class SearchRetryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="deep-research-search-retry-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.transport = Transport()
        environment = mock.patch.dict(os.environ, {
            "DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
            "DRBENCH_MAX_SEARCH": "", "DRBENCH_MAX_READ": "", "DRBENCH_MAX_REQUESTS": "",
            "DEEP_RESEARCH_MAX_SEARCH": "0", "DEEP_RESEARCH_MAX_READ": "0", "DEEP_RESEARCH_MAX_REQUESTS": "0",
            "DEEP_RESEARCH_SOURCE_POLICY": "web_allowed", "DEEP_RESEARCH_SEARCH_PROVIDER": "direct",
            "DRBENCH_ACADEMIC_SEARCH": "0", "DRBENCH_PRACTITIONER_SEARCH": "0",
            "DRBENCH_BLOCK_URL_SUBSTRINGS": "", "DEEP_RESEARCH_ANYSEARCH_TIMEOUT": "15",
            "ANYSEARCH_API_KEY": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        # Any accidental transport escape fails before reaching the network.
        for patch in (
            mock.patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")),
            mock.patch.object(research.base, "_url_ok", return_value=True),
            mock.patch.object(research.base.requests, "get", side_effect=self.transport.get),
            mock.patch.object(research.base.requests, "post", side_effect=self.transport.post),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def search(self, query="method cost", provider="direct", **options):
        return research._ref_search(query, 5, None, None, None, provider, **options)

    def cached_path(self, result):
        return research.workspace.root() / "searches" / f"{result['search_id']}.json"

    def counts(self):
        return research.base.metrics.snapshot()

    @staticmethod
    def hosts(calls):
        return [urllib.parse.urlsplit(call["url"]).hostname for call in calls]

    def assert_public(self, result):
        self.assertNotIn("provider_execution", json.dumps(result))
        self.assertNotIn("https://", json.dumps(result))
        self.assertEqual(research._load_citations(), [])
        for row in result["results"]:
            self.assertFalse(row["citable"])
            self.assertEqual(row["evidence_status"], "unread_lead")

    def test_only_failed_bing_is_retried_and_recovery_clears_its_error(self):
        first = self.search()
        self.assertEqual(first["status"], "partial")
        old_refs = {row["ref"] for row in first["results"]}
        self.assertEqual(self.hosts(self.transport.calls), ["html.duckduckgo.com", "www.bing.com"])
        self.transport.bing_ok = True
        before = len(self.transport.calls)
        recovered = self.search(retry_failed=True)
        self.assertEqual(self.hosts(self.transport.calls[before:]), ["www.bing.com"])
        self.assertEqual(recovered["status"], "ok")
        self.assertFalse(recovered.get("provider_errors"))
        self.assertTrue(old_refs <= {row["ref"] for row in recovered["results"]})
        self.assertEqual((self.counts()["search"], self.counts()["requests"]), (2, 3))
        before = len(self.transport.calls)
        replay = self.search()
        self.assertTrue(replay["cache_hit"])
        self.assertEqual(replay["status"], "ok")
        self.assertEqual(len(self.transport.calls), before)
        self.assert_public(recovered)

    def test_retry_failure_preserves_successful_candidates_and_remains_replayable(self):
        first = self.search()
        before = len(self.transport.calls)
        retried = self.search(retry_failed=True)
        self.assertEqual(self.hosts(self.transport.calls[before:]), ["www.bing.com"])
        self.assertEqual(retried["status"], "partial")
        self.assertEqual([row["ref"] for row in retried["results"]], [row["ref"] for row in first["results"]])
        self.assertEqual({row["provider"] for row in retried["provider_errors"]}, {"bing"})
        before = len(self.transport.calls)
        self.assertEqual(self.search()["status"], "partial")
        self.assertEqual(len(self.transport.calls), before)
        self.assertEqual((self.counts()["search"], self.counts()["requests"]), (2, 3))

    def test_successful_empty_recovery_is_ok_and_does_not_remove_old_candidates(self):
        first = self.search()
        self.transport.bing_ok, self.transport.bing_empty = True, True
        result = self.search(retry_failed=True)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result.get("provider_errors"))
        self.assertEqual([row["ref"] for row in result["results"]], [row["ref"] for row in first["results"]])

    def test_retry_respects_the_cache_key_trimming_of_query_edges(self):
        first = self.search("  method cost  ")
        self.transport.bing_ok = True
        before = len(self.transport.calls)
        result = self.search("method cost", retry_failed=True)
        self.assertEqual(self.hosts(self.transport.calls[before:]), ["www.bing.com"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["search_id"], first["search_id"])
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["recovery"]["deferred_new_requests"], 0)

    def test_auto_anysearch_recovery_keeps_prior_direct_leads_after_early_stop(self):
        self.transport.bing_ok, self.transport.bing_empty = True, True
        first = self.search(provider="auto")
        self.assertEqual(first["status"], "partial")
        prior = {row["ref"] for row in first["results"]}
        self.transport.anysearch_ok = True
        before = len(self.transport.calls)
        result = self.search(provider="auto", retry_failed=True)
        self.assertEqual(self.hosts(self.transport.calls[before:]), ["api.anysearch.com"])
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result.get("provider_errors"))
        self.assertTrue(prior <= {row["ref"] for row in result["results"]})
        self.assertTrue(any(row["engine"] == "anysearch" for row in result["results"]))
        self.assertEqual((self.counts()["search"], self.counts()["requests"]), (2, 4))
        self.assert_public(result)

    def test_every_old_failure_is_retried_even_when_recovered_auto_lane_is_sufficient(self):
        first = self.search(provider="auto")
        self.assertEqual({row["provider"] for row in first["provider_errors"]}, {"anysearch", "bing"})
        self.transport.anysearch_ok = True
        before = len(self.transport.calls)
        result = self.search(provider="auto", retry_failed=True)
        self.assertEqual(self.hosts(self.transport.calls[before:]), ["api.anysearch.com", "www.bing.com"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual({row["provider"] for row in result["provider_errors"]}, {"bing"})
        self.assertTrue({row["ref"] for row in first["results"]} <= {row["ref"] for row in result["results"]})

    def test_recovered_bing_candidates_survive_sufficient_anysearch_recovery(self):
        first = self.search(provider="auto")
        self.transport.anysearch_ok = self.transport.bing_ok = True
        before = len(self.transport.calls)
        result = self.search(provider="auto", retry_failed=True)
        self.assertEqual(self.hosts(self.transport.calls[before:]), ["api.anysearch.com", "www.bing.com"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["results"]), 4)
        self.assertEqual({row["engine"] for row in result["results"]}, {"duckduckgo-html", "bing-rss", "anysearch"})
        self.assertTrue({row["ref"] for row in first["results"]} <= {row["ref"] for row in result["results"]})

    def test_recovery_preserves_success_times_and_error_attempt_history(self):
        first = self.search()
        old = json.loads(self.cached_path(first).read_text())["result"]["provider_execution"]
        old_ddg = next(row for row in old["records"] if row["provider"] == "duckduckgo")
        old_bing = next(row for row in old["records"] if row["provider"] == "bing")
        self.transport.bing_ok = True
        result = self.search(retry_failed=True)
        new = json.loads(self.cached_path(result).read_text())["result"]["provider_execution"]
        new_ddg = next(row for row in new["records"] if row["provider"] == "duckduckgo")
        new_bing = next(row for row in new["records"] if row["provider"] == "bing")
        self.assertEqual(new_ddg, old_ddg)
        self.assertEqual(new_bing["attempts"][:-1], old_bing["attempts"])
        self.assertEqual(new_bing["attempts"][-1]["status"], "ok")
        self.assertEqual(new_bing["status"], "ok")
        self.assertNotIn("error", new_bing)
        self.assertEqual(new_bing["attempts"][0]["error"], old_bing["error"])

    def test_anysearch_auth_change_is_private_and_requires_refresh(self):
        with mock.patch.dict(os.environ, {"ANYSEARCH_API_KEY": "fixture-auth-before"}):
            first = self.search(provider="auto")
        cache = self.cached_path(first)
        old_bytes, usage, calls = cache.read_bytes(), self.counts(), len(self.transport.calls)
        self.assertNotIn(b"fixture-auth-before", old_bytes)
        with mock.patch.dict(os.environ, {"ANYSEARCH_API_KEY": "fixture-auth-after"}):
            self.assertTrue(self.search(provider="auto")["cache_hit"])
            with self.assertRaisesRegex(ValueError, "configuration changed.*refresh"):
                self.search(provider="auto", retry_failed=True)
        self.assertEqual(cache.read_bytes(), old_bytes)
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual(self.counts(), usage)

    def test_partial_execution_is_private_and_saved_for_explicit_recovery(self):
        result = self.search()
        record = json.loads(self.cached_path(result).read_text())
        self.assertIn("provider_execution", record["result"])
        self.assert_public(result)
        events = (self.root / ".adapter-events.jsonl").read_text()
        self.assertNotIn('"provider_execution"', events)

    def test_local_only_rejects_retry_before_budget_or_cache_mutation(self):
        first = self.search()
        cache = self.cached_path(first)
        old_bytes, usage, calls = cache.read_bytes(), self.counts(), len(self.transport.calls)
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}):
            self.assertTrue(self.search()["cache_hit"])
            with self.assertRaisesRegex((ValueError, RuntimeError), "local_only"):
                self.search(retry_failed=True)
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual((self.counts()["search"], self.counts()["requests"]),
                         (usage["search"], usage["requests"]))
        self.assertEqual(cache.read_bytes(), old_bytes)

    def test_exhausted_search_budget_rejects_retry_without_requests_or_cache_mutation(self):
        first = self.search()
        cache = self.cached_path(first)
        old_bytes, calls = cache.read_bytes(), len(self.transport.calls)
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_MAX_SEARCH": "1"}):
            with self.assertRaisesRegex(RuntimeError, "search budget exhausted"):
                self.search(retry_failed=True)
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual((self.counts()["search"], self.counts()["requests"]), (1, 2))
        self.assertEqual(cache.read_bytes(), old_bytes)

    def test_exhausted_http_budget_preserves_old_leads_as_partial(self):
        first = self.search()
        before = len(self.transport.calls)
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_MAX_REQUESTS": "2"}):
            result = self.search(retry_failed=True)
        self.assertEqual(len(self.transport.calls), before)
        self.assertEqual(result["status"], "partial")
        self.assertEqual([row["ref"] for row in result["results"]], [row["ref"] for row in first["results"]])
        self.assertIn("budget exhausted", json.dumps(result.get("provider_errors", [])))
        self.assertEqual((self.counts()["search"], self.counts()["requests"]), (2, 2))

    def test_retry_without_saved_search_is_explicitly_rejected(self):
        with self.assertRaisesRegex((ValueError, RuntimeError), "cache|saved|partial|refresh"):
            self.search(retry_failed=True)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual((self.counts()["search"], self.counts()["requests"]), (0, 0))
        self.assertEqual(list((research.workspace.root() / "searches").glob("*.json")), [])

    def test_legacy_cache_stays_replayable_but_cannot_claim_targeted_recovery(self):
        first = self.search()
        cache = self.cached_path(first)
        record = json.loads(cache.read_text())
        record["result"].pop("provider_execution", None)
        cache.write_text(json.dumps(record), encoding="utf-8")
        old_bytes, calls = cache.read_bytes(), len(self.transport.calls)
        self.assertTrue(self.search()["cache_hit"])
        with self.assertRaisesRegex((ValueError, RuntimeError), "refresh|legacy|execution|channel"):
            self.search(retry_failed=True)
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual(cache.read_bytes(), old_bytes)
        self.assertEqual((self.counts()["search"], self.counts()["requests"]), (1, 2))

    def test_changed_channel_configuration_requires_refresh(self):
        first = self.search()
        cache = self.cached_path(first)
        old_bytes, calls = cache.read_bytes(), len(self.transport.calls)
        with mock.patch.dict(os.environ, {"DRBENCH_ACADEMIC_SEARCH": "1"}):
            self.assertTrue(self.search()["cache_hit"])
            with self.assertRaisesRegex((ValueError, RuntimeError), "refresh|config|fingerprint"):
                self.search(retry_failed=True)
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual(cache.read_bytes(), old_bytes)
        self.assertEqual((self.counts()["search"], self.counts()["requests"]), (1, 2))

    def test_retry_and_refresh_are_mutually_exclusive_before_retrieval(self):
        with self.assertRaisesRegex((ValueError, RuntimeError), "refresh|exclusive|together"):
            self.search(refresh=True, retry_failed=True)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual((self.counts()["search"], self.counts()["requests"]), (0, 0))

    def test_complete_cache_retry_is_a_no_op(self):
        self.transport.bing_ok = True
        first = self.search()
        self.assertEqual(first["status"], "ok")
        cache = self.cached_path(first)
        old_bytes, calls = cache.read_bytes(), len(self.transport.calls)
        result = self.search(retry_failed=True)
        self.assertTrue(result["cache_hit"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual(cache.read_bytes(), old_bytes)
        self.assertEqual((self.counts()["search"], self.counts()["requests"]), (1, 2))

    def test_batch_retries_are_isolated_and_concurrent(self):
        plan = self.root / "plan.json"
        plan.write_text(json.dumps({"searches": [
            {"query": query, "reason": "Find supporting sources"}
            for query in ("method cost", "method latency")
        ]}), encoding="utf-8")
        first = research._search_batch(str(plan), provider="direct", workers=2)
        self.assertEqual(first["status"], "partial")
        old_refs = {row["query"]: {lead["ref"] for lead in row["result"]["results"]}
                    for row in first["results"]}
        self.transport.bing_ok = True
        self.transport.bing_barrier = threading.Barrier(2)
        before = len(self.transport.calls)
        recovered = research._search_batch(str(plan), provider="direct", workers=2, retry_failed=True)
        self.transport.bing_barrier = None
        calls = self.transport.calls[before:]
        self.assertEqual(self.hosts(calls), ["www.bing.com", "www.bing.com"])
        self.assertEqual(Counter(call["query"] for call in calls), {"method cost": 1, "method latency": 1})
        self.assertEqual(recovered["status"], "complete")
        for row in recovered["results"]:
            result = row["result"]
            self.assertEqual(result["status"], "ok")
            self.assertTrue(old_refs[row["query"]] <= {lead["ref"] for lead in result["results"]})
            self.assertTrue(all(row["query"] in lead["title"] for lead in result["results"]))
            self.assert_public(result)
        self.assertEqual((self.counts()["search"], self.counts()["requests"]), (4, 6))

    def test_github_retry_uses_the_failed_effective_query_and_keeps_broad_leads(self):
        def repository(name):
            return {"full_name": f"widget-dev/{name}", "html_url": f"https://github.com/widget-dev/{name}",
                    "description": "Widget latency official implementation code", "stargazers_count": 0}

        self.transport.bing_ok, self.transport.bing_empty = True, True
        self.transport.github = {"Widget latency": [repository("primary")], "Widget": 503}

        def retrieve(**options):
            return research._ref_search("Widget latency", 5, "Widget", "repository",
                                        "official latency implementation", "direct", **options)

        first = retrieve()
        self.assertEqual(first["status"], "partial")
        broad_refs = {row["ref"] for row in first["results"] if row["engine"] == "duckduckgo-html"}
        self.assertTrue(broad_refs)
        initial_github_queries = [call["query"] for call in self.transport.calls
                                  if urllib.parse.urlsplit(call["url"]).hostname == "api.github.com"]
        self.assertEqual(initial_github_queries, ["Widget latency", "Widget"])
        self.transport.github["Widget"] = [repository("recovered")]
        before = len(self.transport.calls)
        result = retrieve(retry_failed=True)
        calls = self.transport.calls[before:]
        self.assertEqual(self.hosts(calls), ["api.github.com"])
        self.assertEqual([call["query"] for call in calls], ["Widget"])
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result.get("provider_errors"))
        self.assertTrue(broad_refs <= {row["ref"] for row in result["results"]})
        self.assertEqual(sum(row["engine"] == "github-repositories" for row in result["results"]), 2)
        self.assert_public(result)

    def test_recovered_primary_github_does_not_skip_a_recovered_fallback_request(self):
        self.transport.bing_ok, self.transport.bing_empty = True, True
        self.transport.github = {"Widget latency": 503, "Widget": 503}

        def retrieve(**options):
            return research._ref_search("Widget latency", 5, "Widget", "repository",
                                        "official latency implementation", "direct", **options)

        first = retrieve()
        self.assertEqual(first["status"], "partial")
        for query, prefix in (("Widget latency", "primary"), ("Widget", "fallback")):
            self.transport.github[query] = [{
                "full_name": f"widget-dev/{prefix}-{index}",
                "html_url": f"https://github.com/widget-dev/{prefix}-{index}",
                "description": "Widget latency official implementation code", "stargazers_count": 0,
            } for index in range(2)]
        before = len(self.transport.calls)
        result = retrieve(retry_failed=True)
        self.assertEqual(self.hosts(self.transport.calls[before:]), ["api.github.com", "api.github.com"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(sum(row["engine"] == "github-repositories" for row in result["results"]), 4)
        self.assertTrue({row["ref"] for row in first["results"]} <= {row["ref"] for row in result["results"]})

    def test_cli_exposes_explicit_retry_for_search_and_batch(self):
        for command in ("search", "search-batch"):
            with self.subTest(command=command):
                process = subprocess.run([sys.executable, "-B", str(SCRIPT), command, "--help"],
                                         capture_output=True, text=True, timeout=20)
                self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                self.assertIn("--retry-failed", process.stdout)

    def test_cli_rejects_retry_plus_refresh_before_search_or_plan_loading(self):
        environment = {**os.environ, "DEEP_RESEARCH_SOURCE_POLICY": "local_only"}
        for command, argument in (("search", "method cost"), ("search-batch", str(self.root / "absent.json"))):
            with self.subTest(command=command):
                process = subprocess.run(
                    [sys.executable, "-B", str(SCRIPT), command, argument, "--refresh", "--retry-failed"],
                    capture_output=True, text=True, timeout=20, env=environment,
                )
                text = (process.stdout + process.stderr).casefold()
                self.assertNotEqual(process.returncode, 0)
                self.assertNotIn("unrecognized arguments", text)
                self.assertIn("refresh", text)
                self.assertIn("retry", text)
                self.assertRegex(text, "not allowed with|mutually exclusive|cannot.*(?:together|combined)|cannot use")
                self.assertNotIn("no such file", text)
        self.assertEqual(self.transport.calls, [])

    def kaggle_search(self, **options):
        target = "Kaggle Widget Trial"
        return research._ref_search(f"{target} winning solution", 5, target, "repository",
                                    "winning implementation", "direct", **options)

    def close_kaggle_scope(self):
        research._save_scope_state({
            "status": "closed", "target": "Kaggle Widget Trial", "canonical_target": "Kaggle Widget Trial",
            "competition_name": "widget-trial", "pivot_required": False,
        })

    def test_kaggle_page_two_retry_reuses_page_one_and_other_successes(self):
        self.close_kaggle_scope()
        self.transport.bing_ok, self.transport.bing_empty = True, True
        self.transport.topics = {1: [{"id": 7, "title": "1st place winning solution", "votes": 3}], 2: 503}
        first = self.kaggle_search()
        self.assertEqual(first["status"], "partial")
        topic_refs = {row["ref"] for row in first["results"] if row["engine"] == "kaggle-competition-topics"}
        self.assertTrue(topic_refs)
        self.transport.topics[2] = []
        before = len(self.transport.calls)
        result = self.kaggle_search(retry_failed=True)
        calls = self.transport.calls[before:]
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.hosts(calls), ["www.kaggle.com"])
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(calls[0]["url"]).query)["page"], ["2"])
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result.get("provider_errors"))
        self.assertTrue(topic_refs <= {row["ref"] for row in result["results"]})
        self.assert_public(result)

    def test_recovery_scope_and_pivot_rejection_precede_search_budget_and_http(self):
        self.close_kaggle_scope()
        first = self.kaggle_search()
        cache = self.cached_path(first)
        old_bytes, usage, calls = cache.read_bytes(), self.counts(), len(self.transport.calls)
        for state, message in (
            ({"status": "open", "target": "Kaggle Widget Trial"}, "scope gate is open"),
            ({"status": "closed", "target": "Kaggle Widget Trial", "pivot_required": True,
              "pivots": ["missing official metric"]}, "must copy"),
        ):
            with self.subTest(state=state):
                research._save_scope_state(state)
                scope_bytes = research._scope_state_path().read_bytes()
                with self.assertRaisesRegex(RuntimeError, message):
                    self.kaggle_search(retry_failed=True)
                self.assertEqual(research._scope_state_path().read_bytes(), scope_bytes)
                self.assertEqual(cache.read_bytes(), old_bytes)
                self.assertEqual(len(self.transport.calls), calls)
                self.assertEqual(self.counts(), usage)
        research._save_scope_state({"status": "closed", "target": "Kaggle Widget Trial",
                                    "pivot_required": True, "pivots": ["winning solution"]})
        scope_bytes = research._scope_state_path().read_bytes()
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_MAX_SEARCH": "1"}), \
                self.assertRaisesRegex(RuntimeError, "search budget exhausted"):
            self.kaggle_search(retry_failed=True)
        self.assertEqual(research._scope_state_path().read_bytes(), scope_bytes)
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual(cache.read_bytes(), old_bytes)

    def test_newly_reachable_kaggle_page_requires_refresh_without_fetching_it(self):
        self.close_kaggle_scope()
        self.transport.bing_ok, self.transport.bing_empty = True, True
        self.transport.topics = {1: 503}
        first = self.kaggle_search()
        self.assertEqual(first["status"], "partial")
        self.assertFalse(any("page=2" in call["url"] for call in self.transport.calls))
        self.transport.topics[1] = [{"id": 7, "title": "1st place winning solution", "votes": 3}]
        before = len(self.transport.calls)
        try:
            result = self.kaggle_search(retry_failed=True)
        except (ValueError, RuntimeError) as exc:
            self.assertIn("refresh", str(exc).casefold())
        else:
            self.assertEqual(result["status"], "partial")
            self.assertIn("refresh", json.dumps(result).casefold())
            self.assertTrue({row["ref"] for row in first["results"]} <=
                            {row["ref"] for row in result["results"]})
        calls = self.transport.calls[before:]
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.hosts(calls), ["www.kaggle.com"])
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(calls[0]["url"]).query)["page"], ["1"])


if __name__ == "__main__":
    unittest.main()
