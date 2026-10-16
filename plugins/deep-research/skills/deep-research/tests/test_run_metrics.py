from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from requests.cookies import RequestsCookieJar


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "research.py"
SPEC = importlib.util.spec_from_file_location("test_run_metrics_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)
metrics = research.base.metrics


class Response:
    def __init__(self, content=b"body", status=200, url="https://example.org/source", headers=None):
        self.content, self.status_code, self.url = content, status, url
        self.headers, self.encoding, self.closed = headers or {}, "utf-8", False
        self.cookies = RequestsCookieJar()

    @property
    def text(self):
        return self.content.decode()

    def json(self):
        return json.loads(self.content)

    def iter_content(self, size):
        yield self.content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise research.base.requests.HTTPError(str(self.status_code))

    def close(self):
        self.closed = True


class RunMetricsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="deep-research-metrics-")
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
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

    def test_unlimited_runs_still_record_logical_calls(self):
        for kind in ("search", "search", "read"):
            research.base._reserve(kind)
        counts = metrics.snapshot()
        self.assertEqual((counts["search"], counts["read"], counts["requests"]), (2, 1, 0))

    def test_timeout_and_http_errors_include_time_and_close_responses(self):
        response = Response(status=429)
        with mock.patch.object(metrics.time, "monotonic", side_effect=[1.0, 1.25, 2.0, 2.5]):
            with self.assertRaises(research.base.requests.Timeout):
                with metrics.request(mock.Mock(side_effect=research.base.requests.Timeout()), "https://example.org"):
                    self.fail("timeout must not yield a response")
            with self.assertRaises(research.base.requests.HTTPError):
                with metrics.request(mock.Mock(return_value=response), response.url) as fetched:
                    fetched.raise_for_status()
        counts = metrics.snapshot()
        self.assertEqual((counts["requests"], counts["request_errors"], counts["request_seconds"]), (2, 2, 0.75))
        self.assertTrue(response.closed)

    def test_every_http_adapter_uses_the_shared_request_counter(self):
        payload = {"result": {"content": [{"type": "text", "text": "No matches"}]}}
        with mock.patch.object(research.base.requests, "get", side_effect=lambda url, **kw: Response(b'{"value":1}', url=url)):
            research.base._get("https://example.org/page")
            research.v1._fetch_bytes("https://example.org/document")
            research._kaggle_json("https://example.org/metadata")
            research._read_github_readme("https://github.com/team/project", [], 100, 1)
        research.anysearch._rpc({}, post=lambda url, **kw: Response(json.dumps(payload).encode(), url=url))
        counts = metrics.snapshot()
        self.assertEqual((counts["requests"], counts["request_errors"], counts["read"]), (5, 0, 1))

    def test_malformed_kaggle_payload_is_a_provider_error_not_empty(self):
        response = Response(b"[]")
        outcomes = []
        with mock.patch.object(research.base.requests, "get", return_value=response):
            cards = research._call_provider("kaggle-metadata", lambda: [
                research._kaggle_json("https://example.org/metadata")], outcomes)
        with self.assertRaises(research.SearchError):
            research._search_status({"results": cards, "provider_errors": outcomes})
        self.assertEqual(outcomes[0]["status"], "error")
        self.assertIn("expected an object", outcomes[0]["error"])
        self.assertEqual(metrics.snapshot()["requests"], 1)
        self.assertTrue(response.closed)

    def test_fallback_requests_are_separate_from_searches_and_cache_costs_zero(self):
        responses = [Response(b'<div class="no-results">No results found</div>', url="https://html.duckduckgo.com/html/"),
                     Response(b"<rss><channel/></rss>", url="https://www.bing.com/search")]
        with mock.patch.object(research.base.requests, "get", side_effect=responses) as sender:
            first = research._ref_search("widget cost", 5, None, None, None, "direct")
            before = metrics.snapshot()
            second = research._ref_search("widget cost", 5, None, None, None, "direct")
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual((before["search"], before["requests"], sender.call_count), (1, 2, 2))
        self.assertEqual(before, metrics.snapshot())

    def test_redirect_hops_are_counted_and_budget_blocks_before_next_hop(self):
        first = Response(status=302, headers={"Location": "/destination"})
        sender = mock.Mock(return_value=first)
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_MAX_REQUESTS": "1"}):
            with self.assertRaisesRegex(RuntimeError, "requests budget exhausted"):
                with metrics.request(sender, first.url):
                    self.fail("redirect destination must not be sent")
        self.assertEqual(sender.call_count, 1)
        self.assertFalse(sender.call_args.kwargs["allow_redirects"])
        counts = metrics.snapshot()
        self.assertEqual((counts["requests"], counts["redirects"], counts["blocked"]), (1, 1, 1))
        self.assertTrue(first.closed)

    def test_redirect_destinations_are_checked_before_sending(self):
        first = Response(status=302, headers={"Location": "http://127.0.0.1/private"})
        sender = mock.Mock(return_value=first)
        with self.assertRaisesRegex(ValueError, "blocked"):
            with metrics.request(sender, first.url, url_ok=lambda url: "127.0.0.1" not in url):
                self.fail("blocked redirect must not be sent")
        self.assertEqual((sender.call_count, metrics.snapshot()["requests"]), (1, 1))

    def test_redirect_cookies_survive_and_obey_domain_and_path_scope(self):
        first = Response(status=302, url="https://example.org/start", headers={"Location": "/destination"})
        first.cookies.set("route", "accepted", domain="example.org", path="/")
        first.cookies.set("private", "hidden", domain="example.org", path="/admin")
        first.cookies.set("foreign", "hidden", domain="elsewhere.example", path="/")
        final = Response(url="https://example.org/destination")
        sender = mock.Mock(side_effect=[first, final])
        with metrics.request(sender, first.url) as response:
            self.assertIs(response, final)
        import requests
        prepared = requests.Request("GET", final.url, cookies=sender.call_args.kwargs["cookies"]).prepare()
        self.assertEqual(prepared.headers["Cookie"], "route=accepted")
        self.assertEqual((metrics.snapshot()["requests"], metrics.snapshot()["redirects"]), (2, 1))
        self.assertTrue(first.closed and final.closed)

    def test_redirect_cookie_deletion_reaches_the_shared_jar(self):
        import requests
        observed = []

        def send(adapter, prepared, **kwargs):
            observed.append(prepared.headers.get("Cookie"))
            response = requests.Response()
            response.request, response.url = prepared, prepared.url
            response._content, response._content_consumed = b"body", True
            message = Message()
            if prepared.url.endswith("/start"):
                response.status_code = 302
                response.headers["Location"] = "/clear"
                message["Set-Cookie"] = "route=stale; Path=/"
            elif prepared.url.endswith("/clear"):
                response.status_code = 302
                response.headers["Location"] = "/finish"
                message["Set-Cookie"] = "route=; Max-Age=0; Path=/"
            else:
                response.status_code = 403 if prepared.headers.get("Cookie") else 200
            response.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=message))
            requests.cookies.extract_cookies_to_jar(response.cookies, prepared, response.raw)
            return response

        with mock.patch.object(requests.adapters.HTTPAdapter, "send", new=send):
            with metrics.request(requests.get, "https://example.org/start") as response:
                self.assertEqual(response.status_code, 200)
        self.assertEqual(observed, [None, "route=stale", None])
        self.assertEqual((metrics.snapshot()["requests"], metrics.snapshot()["redirects"]), (3, 2))

    def test_redirect_auth_survives_same_origin_and_is_stripped_elsewhere(self):
        import requests
        for destination, retained in (("https://example.org/finish", True),
                                      ("https://other.example/finish", False),
                                      ("http://example.org/finish", False)):
            with self.subTest(destination=destination):
                observed = []

                def send(adapter, prepared, **kwargs):
                    observed.append(prepared.headers.get("Authorization"))
                    response = requests.Response()
                    response.request, response.url = prepared, prepared.url
                    response._content, response._content_consumed = b"body", True
                    response.status_code = 302 if len(observed) == 1 else 200
                    if response.status_code == 302:
                        response.headers["Location"] = destination
                    return response

                with mock.patch.dict(os.environ, {"NETRC": str(self.folder / "absent-netrc")}):
                    with mock.patch.object(requests.adapters.HTTPAdapter, "send", new=send):
                        with metrics.request(requests.get, "https://alice:secret@example.org/start"):
                            pass
                self.assertEqual(observed[0], "Basic YWxpY2U6c2VjcmV0")
                self.assertEqual(observed[1], observed[0] if retained else None)

    def test_parallel_requests_cannot_exceed_shared_limit(self):
        sender = mock.Mock(side_effect=lambda url, **kw: Response(url=url))

        def retrieve(index):
            try:
                with metrics.request(sender, f"https://example.org/{index}"):
                    return True
            except RuntimeError:
                return False

        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_MAX_REQUESTS": "3"}), ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(retrieve, range(8)))
        counts = metrics.snapshot()
        self.assertEqual((sum(results), sender.call_count, counts["requests"], counts["blocked"]), (3, 3, 3, 5))

    def test_a_limit_requires_a_run_directory(self):
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_RUN_DIR": "", "DEEP_RESEARCH_MAX_REQUESTS": "1"}):
            sender = mock.Mock()
            with self.assertRaisesRegex(ValueError, "RUN_DIR"):
                with metrics.request(sender, "https://example.org"):
                    pass
            sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
