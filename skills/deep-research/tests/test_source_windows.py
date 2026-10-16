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
SPEC = importlib.util.spec_from_file_location("test_source_windows_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)
workspace = research.workspace


class SourceWindowTests(unittest.TestCase):
    def test_dense_early_mentions_do_not_hide_late_correction(self):
        text = "latency preliminary claim.\n" * 8 + "context " * 250
        text += "\nlatency corrected measurement: 91 ms.\n"
        found = workspace.excerpts({}, text, ["latency", "throughput"], window=100,
                                   intro_if_missing=False)
        self.assertTrue(any("corrected measurement: 91 ms" in value for value in found["windows"]))
        self.assertEqual(found["match_counts"], {"latency": 9, "throughput": 0})
        self.assertEqual(found["missing_terms"], ["throughput"])
        for value, locator in zip(found["windows"], found["locators"]):
            self.assertEqual(value, text[locator["start_char"]:locator["end_char"]])

    def test_pages_reach_every_passage_without_page_size_changing_the_order(self):
        text = "".join(f"latency record {index:02d}.\n" + "." * 500 + "\n" for index in range(12))

        def collect(limit):
            offset, windows, locators = 0, [], []
            while True:
                found = workspace.excerpts({}, text, ["latency"], window=100,
                                           max_windows=limit, offset=offset, intro_if_missing=False)
                self.assertEqual(found["total_windows"], 12)
                self.assertEqual(found["match_counts"], {"latency": 12})
                self.assertLessEqual(len(found["windows"]), limit)
                windows.extend(found["windows"])
                locators.extend(found["locators"])
                if found["next_offset"] is None:
                    return windows, locators
                self.assertGreater(found["next_offset"], offset)
                offset = found["next_offset"]

        windows, locators = collect(3)
        self.assertEqual((windows, locators), collect(5))
        for index in range(12):
            self.assertTrue(any(f"latency record {index:02d}." in value for value in windows))
        self.assertEqual(len({item["start_char"] for item in locators}), 12)

    def test_multiple_terms_share_the_first_page_and_preserve_unicode_locators(self):
        text = "ß 中文🙂\n" + "latency early.\n" * 20 + "." * 600
        text += "\n中文修订 throughput: 7 requests/s.\n" + "." * 600 + "latency late."
        found = workspace.excerpts({}, text, ["latency", "THROUGHPUT"], window=100,
                                   max_windows=2, intro_if_missing=False)
        self.assertTrue(any("throughput: 7 requests/s" in value for value in found["windows"]))
        self.assertEqual(found["match_counts"], {"latency": 21, "THROUGHPUT": 1})
        for value, locator in zip(found["windows"], found["locators"]):
            self.assertEqual(value, text[locator["start_char"]:locator["end_char"]])
        self.assertIsNotNone(found["next_offset"])

    def test_no_match_and_out_of_range_pages_do_not_return_unrelated_text(self):
        text = "A readable introduction without the requested field."
        found = workspace.excerpts({}, text, ["latency"], intro_if_missing=False)
        self.assertEqual((found["windows"], found["total_windows"], found["next_offset"]), ([], 0, None))
        intro = workspace.excerpts({}, text, ["latency"])
        self.assertEqual(intro["missing_terms"], ["latency"])
        self.assertEqual(intro["windows"], [text])
        end = workspace.excerpts({}, "latency", ["latency"], offset=9, intro_if_missing=False)
        self.assertEqual((end["windows"], end["next_offset"]), ([], None))
        for options in ({"offset": -1}, {"max_windows": 0}, {"max_windows": 11}, {"window": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                workspace.excerpts({}, text, ["latency"], **options)

    def test_cli_find_pages_an_exact_historical_snapshot_offline(self):
        with tempfile.TemporaryDirectory(prefix="deep-research-window-tests-") as folder, \
                mock.patch.dict(os.environ, {"DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder,
                                            "DEEP_RESEARCH_SOURCE_POLICY": "local_only"}):
            path = Path(folder) / "report.txt"
            text = "".join(f"latency record {index:02d}.\n" + "." * 500 + "\n" for index in range(7))
            path.write_text(text, encoding="utf-8")
            saved = research._ref_read(str(path), ["latency"], 100)
            workspace.save_source(saved["ref"], {"url": str(path), "content_scope": "local_file"},
                                  "A refreshed snapshot without the old measurements.")
            command = [sys.executable, str(SCRIPT), "find", saved["ref"], "--term", "latency",
                       "--window", "100", "--limit", "3", "--version", saved["snapshot_id"]]
            first = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            page = json.loads(first.stdout)
            self.assertEqual(page["next_offset"], 3)
            last = subprocess.run(command + ["--offset", "6"], capture_output=True, text=True, timeout=20)
            self.assertEqual(last.returncode, 0, last.stdout + last.stderr)
            result = json.loads(last.stdout)
            self.assertEqual(result["snapshot_id"], saved["snapshot_id"])
            self.assertEqual((result["total_windows"], result["next_offset"]), (7, None))
            self.assertIn("latency record 06.", result["windows"][0])
            self.assertEqual(result["citation_path"], str(path))
            self.assertEqual(result["allowed_citation_urls"], [])
            self.assertEqual((result.get("run_usage") or {}).get("requests", 0), 0)
            self.assertEqual((result.get("run_usage") or {}).get("read", 0), 0)


if __name__ == "__main__":
    unittest.main()
