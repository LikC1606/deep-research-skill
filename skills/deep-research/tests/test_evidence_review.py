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
SPEC = importlib.util.spec_from_file_location("test_evidence_review_workspace", SCRIPT.with_name("workspace.py"))
workspace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workspace)


class EvidenceReviewTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="deep-research-review-tests-")
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        env = mock.patch.dict(os.environ, {
            "DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
            "DEEP_RESEARCH_SOURCE_POLICY": "local_only",
        })
        env.start()
        self.addCleanup(env.stop)

    def source(self, index=1, text="Results\n12 samples.\nOne site.", **metadata):
        ref = f"REF-{index:016X}"
        workspace.save_source(ref, {
            "url": f"https://example{index}.org/results", "content_scope": "full_text", **metadata,
        }, text)
        return ref

    def card(self, ref, *, need="N1", claim="Trial size is 12", quote="12 samples", relation="supports"):
        return workspace.record_evidence(ref, need, claim, quote, relation)

    def test_identical_text_across_urls_is_one_content_group_without_losing_cards(self):
        first = self.card(self.source(1))
        second = self.card(self.source(2, content_scope="abstract", truncated=True))
        review = workspace.review_evidence(need="N1")
        self.assertEqual(review["total"], 2)
        self.assertEqual(review["source_ref_count"], 2)
        self.assertEqual(review["distinct_content_count"], 1)
        self.assertEqual({card["id"] for card in review["evidence"]}, {first["id"], second["id"]})
        self.assertTrue(all(card["same_text_ref_count"] == 2 for card in review["evidence"]))
        partial = next(card for card in review["evidence"] if card["id"] == second["id"])
        self.assertEqual(partial["content_scope"], "abstract")
        self.assertTrue(partial["source_truncated"])
        status = workspace.status()
        self.assertEqual(status["evidence_by_need"]["N1"]["supports"], 2)
        self.assertEqual(status["evidence_content_counts_by_need"]["N1"]["supports"], 1)
        self.assertEqual(status["duplicate_text_groups"], 1)

    def test_multiple_passages_from_one_source_do_not_multiply_content_counts(self):
        ref = self.source()
        self.card(ref)
        self.card(ref, claim="Trial was at one site", quote="One site")
        self.card(ref, claim="Trial context", quote="Results", relation="context")
        status = workspace.status()
        self.assertEqual(status["evidence_count"], 3)
        self.assertEqual(status["evidence_distinct_contents"], 1)
        self.assertEqual(status["duplicate_text_groups"], 0)
        review = workspace.review_evidence()
        self.assertEqual(review["source_ref_count"], 1)
        self.assertEqual(review["relation_counts"], {"supports": 2, "refutes": 0, "context": 1})

    def test_mixed_relations_require_the_same_need_and_exact_claim(self):
        ref = self.source()
        self.card(ref)
        self.card(ref, need="N2", relation="refutes")
        self.card(ref, claim="Different claim", relation="refutes")
        self.card(ref, claim="trial size is 12", relation="refutes")
        self.assertEqual(workspace.status()["mixed_relation_claims"], 0)
        self.card(ref, relation="refutes")
        review = workspace.review_evidence(need="N1", claim="Trial size is 12", limit=1)
        self.assertEqual(review["total"], 2)
        self.assertEqual(len(review["evidence"]), 1)
        self.assertTrue(review["evidence"][0]["has_mixed_relations"])
        self.assertEqual(review["evidence"][0]["claim_relations"],
                         {"supports": 1, "refutes": 1, "context": 0})
        self.assertEqual(workspace.status()["mixed_relation_claims"], 1)
        other = workspace.review_evidence(need="N2")["evidence"][0]
        self.assertFalse(other["has_mixed_relations"])

    def test_review_uses_historical_evidence_versions_after_source_refresh(self):
        first_ref = self.source(1)
        old = self.card(first_ref)
        second_ref = self.source(2)
        self.card(second_ref)
        self.source(1, "Updated Results\n24 samples.")
        self.card(first_ref, quote="24 samples", relation="refutes")
        review = workspace.review_evidence(need="N1")
        historical = next(card for card in review["evidence"] if card["id"] == old["id"])
        self.assertEqual(historical["content_hash"], old["content_hash"])
        self.assertEqual(historical["locator"], old["locator"])
        self.assertEqual(historical["same_text_ref_count"], 2)
        self.assertEqual(review["distinct_content_count"], 2)
        self.assertNotEqual(workspace.require_source(first_ref)[0]["content_hash"], old["content_hash"])
        self.assertEqual(workspace.status()["mixed_relation_claims"], 1)

    def test_pagination_preserves_every_card_and_limits_output(self):
        ref = self.source()
        expected = [self.card(ref, claim=f"Claim {index}")["id"] for index in range(7)]
        offset, seen = 0, []
        while offset is not None:
            page = workspace.review_evidence(need="N1", limit=2, offset=offset)
            self.assertLessEqual(len(page["evidence"]), 2)
            self.assertEqual(page["total"], 7)
            seen.extend(card["id"] for card in page["evidence"])
            offset = page["next_offset"]
        self.assertEqual(seen, expected)
        self.assertEqual(len(workspace.review_evidence()["evidence"]), 5)
        self.assertEqual(workspace.review_evidence(offset=100)["evidence"], [])
        self.assertEqual(workspace.review_evidence(need="unknown")["total"], 0)
        for options in ({"offset": -1}, {"limit": 0}, {"limit": 11}, {"claim": ""}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                workspace.review_evidence(**options)

    def test_duplicate_ref_annotations_are_bounded_without_changing_group_count(self):
        for index in range(1, 13):
            self.card(self.source(index))
        review = workspace.review_evidence(limit=1)
        card = review["evidence"][0]
        self.assertEqual(card["same_text_ref_count"], 12)
        self.assertEqual(len(card["same_text_refs"]), 10)
        self.assertTrue(card["same_text_refs_truncated"])
        self.assertEqual(review["distinct_content_count"], 1)

    def test_review_is_read_only_after_stop_and_isolated_from_other_runs(self):
        workspace.init_task("Report", "The trial")
        workspace.set_need("N1", question="Sample size", proof="Reported number")
        self.card(self.source())
        workspace.stop_task("Continue later")
        task_before = (workspace.root() / "task.json").read_bytes()
        evidence_before = (workspace.root() / "evidence.json").read_bytes()
        self.assertEqual(workspace.review_evidence()["total"], 1)
        self.assertEqual((workspace.root() / "task.json").read_bytes(), task_before)
        self.assertEqual((workspace.root() / "evidence.json").read_bytes(), evidence_before)
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_RUN_DIR": str(self.folder / "other")}):
            self.assertEqual(workspace.review_evidence()["total"], 0)

    def test_cli_reviews_filtered_evidence_offline_and_rejects_invalid_limits(self):
        first = self.card(self.source(1))
        self.card(self.source(2), need="N2")
        command = [sys.executable, str(SCRIPT), "review", "--need", "N1", "--claim", "Trial size is 12"]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[:1500])
        review = json.loads(result.stdout)
        self.assertEqual(review["total"], 1)
        self.assertEqual(review["evidence"][0]["id"], first["id"])
        self.assertEqual(review["evidence"][0]["same_text_ref_count"], 2)
        self.assertFalse((self.folder / ".deep-research-citations.json").exists())
        invalid = subprocess.run(command + ["--limit", "11"], capture_output=True, text=True, timeout=30)
        self.assertEqual(invalid.returncode, 1)
        self.assertIn("between 1 and 10", json.loads(invalid.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
