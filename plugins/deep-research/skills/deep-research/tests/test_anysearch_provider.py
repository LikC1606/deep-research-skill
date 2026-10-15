from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest import mock

import requests


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "anysearch_provider.py"
)
SPEC = importlib.util.spec_from_file_location("test_anysearch_provider_module", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
provider = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provider)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.body = json.dumps(payload).encode("utf-8")
        self.status_code = status_code
        self.closed = False

    def iter_content(self, _chunk_size: int):
        yield self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def close(self):
        self.closed = True


def rpc_payload(text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": text}]},
    }


class AnySearchProviderTests(unittest.TestCase):
    def test_parse_search_markdown_returns_compact_cards(self):
        text = """## Search Results (2 results, 20ms)

### 1. First result
- **URL**: https://example.com/one
- Useful body text.

### 2. Second result
- **URL**: [source](https://example.org/two)
- More body text.
"""
        cards = provider.parse_search_markdown(text, 5)
        self.assertEqual([card["title"] for card in cards], ["First result", "Second result"])
        self.assertEqual(cards[0]["url"], "https://example.com/one")
        self.assertEqual(cards[1]["url"], "https://example.org/two")
        self.assertEqual(cards[0]["engine"], "anysearch")

    def test_search_uses_fixed_endpoint_and_academic_route(self):
        captured = {}
        response = FakeResponse(
            rpc_payload(
                "### 1. Paper\n- **URL**: https://arxiv.org/abs/2005.11401\n- Abstract"
            )
        )

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return response

        environment = {
            "DEEP_RESEARCH_SOURCE_POLICY": "web_allowed",
            "ANYSEARCH_API_KEY": "test-key",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            cards = provider.search("RAG paper", 3, "paper", post=fake_post)

        self.assertEqual(captured["url"], provider.ENDPOINT)
        self.assertFalse(captured["allow_redirects"])
        self.assertTrue(captured["stream"])
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-key")
        arguments = captured["json"]["params"]["arguments"]
        self.assertEqual(arguments["domain"], "academic")
        self.assertEqual(arguments["sub_domain"], "academic.search")
        self.assertEqual(cards[0]["url"], "https://arxiv.org/abs/2005.11401")
        self.assertTrue(response.closed)

    def test_local_only_blocks_before_network(self):
        sender = mock.Mock()
        with mock.patch.dict(
            os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}, clear=False
        ):
            with self.assertRaisesRegex(provider.AnySearchError, "local_only"):
                provider.search("must stay local", post=sender)
        sender.assert_not_called()

    def test_redirect_is_rejected_without_leaking_key(self):
        response = FakeResponse({}, status_code=302)
        with mock.patch.dict(
            os.environ,
            {
                "DEEP_RESEARCH_SOURCE_POLICY": "web_allowed",
                "ANYSEARCH_API_KEY": "test-key",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(provider.AnySearchError, "redirect"):
                provider.search("query", post=lambda *_args, **_kwargs: response)
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
