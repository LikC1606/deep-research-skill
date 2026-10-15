from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest
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


if __name__ == "__main__":
    unittest.main()
