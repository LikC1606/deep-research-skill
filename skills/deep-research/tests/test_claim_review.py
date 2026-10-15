import copy
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
SPEC = importlib.util.spec_from_file_location("claim_review_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)
ws, review = research.workspace, research.claim_review


class ClaimReviewTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        env = mock.patch.dict(os.environ, {"DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
                                           "DEEP_RESEARCH_SOURCE_POLICY": "local_only"})
        env.start()
        self.addCleanup(env.stop)
        ws.init_task("Assess costs", "Service v3")
        ws.set_need("N1", question="What is the cost?", proof="Prices and workload")
        self.price = self.card(1, "Unit price is 2 dollars.")
        self.load = self.card(2, "Workload is 3 units.")
        self.other = self.card(3, "Exports use JSONL.")
        self.report = self.folder / "report.md"
        self.report.write_text(f"Total cost is 6 dollars. [{self.price['id']}][{self.load['id']}]", encoding="utf-8")

    def card(self, index, text):
        ref = f"REF-{index:016X}"
        ws.save_source(ref, {"url": f"/local/source{index}.txt", "content_scope": "local_file"}, text)
        return ws.record_evidence(ref, "N1", text, text, "supports")

    def prepare(self):
        result = review.claims(ws, research.reporting, str(self.report))
        return json.loads(Path(result["template_path"]).read_text())

    def supported(self):
        value = self.prepare()
        value["reviewer"] = "test-reviewer"
        row = value["claims"][0]
        row.update(kind="inference", verdict="supported", support_mode="joint",
                   rationale="Price times workload is 6; neither premise alone gives the cost.")
        for binding in row["bindings"]:
            binding.update(contribution="necessary", rationale="Removing this premise prevents computing the cost.")
        return value

    def save(self, value):
        path = self.folder / "judgments.json"
        path.write_text(json.dumps(value))
        return review.claims(ws, research.reporting, str(self.report), judgments_path=str(path))

    def test_preparation_does_not_assess_or_change_needs(self):
        before = (ws.root() / "task.json").read_bytes()
        result = review.claims(ws, research.reporting, str(self.report))
        self.assertEqual(result["claims"][0]["verdict"], "not_assessed")
        self.assertEqual(result["claims"][0]["line"], 1)
        self.assertIsNone(result["metrics"]["support_coverage"])
        self.assertIsNone(result["review_hash"])
        self.assertEqual((ws.root() / "task.json").read_bytes(), before)

    def test_joint_support_is_distinct_from_individual_citation_contribution(self):
        value = self.supported()
        first = self.save(value)
        row = value["claims"][0]
        row["bindings"].append({"evidence_id": self.other["id"], "implicit": True, "confirmed": True,
                                 "contribution": "irrelevant", "rationale": "Format does not determine cost."})
        second = self.save(value)
        self.assertEqual(first["metrics"]["support_coverage"], 1)
        self.assertEqual(second["metrics"]["support_coverage"], 1)
        self.assertEqual(second["metrics"]["citation_contribution_rate"], 2 / 3)
        self.assertNotEqual(first["review_hash"], second["review_hash"])
        self.assertTrue(Path(first["review_path"]).exists())
        saved = review.load_review(ws, first["report_hash"], first["review_hash"])
        self.assertEqual(saved["claims"][0]["bindings"][0]["evidence"]["snapshot_id"], self.price["snapshot_id"])
        self.assertEqual(ws.status()["task"]["needs"]["N1"]["state"], "open")

    def test_implicit_proposals_do_not_count_before_confirmation(self):
        self.report.write_text("Total cost is 6 dollars.")
        value = self.prepare()
        value["reviewer"] = "reviewer"
        row = value["claims"][0]
        row.update(kind="inference", rationale="Proposed price/workload bindings need confirmation.")
        row["bindings"] = [{"evidence_id": self.price["id"], "implicit": True, "confirmed": False,
                             "contribution": "not_assessed", "rationale": "Possible premise."}]
        result = self.save(value)
        self.assertEqual(result["metrics"]["unconfirmed_implicit_proposals"], 1)
        self.assertEqual(result["metrics"]["confirmed_citation_links"], 0)
        row.update(verdict="supported", support_mode="direct")
        with self.assertRaisesRegex(ValueError, "contributing citations"):
            self.save(value)

    def test_split_compound_claims_and_exclude_structural_prose(self):
        self.report.write_text(f"Price is 2 dollars and no adverse events occurred. [{self.price['id']}]\n\nOverview follows.")
        value = self.prepare()
        value["reviewer"] = "reviewer"
        row = value["claims"][0]
        second = copy.deepcopy(row)
        row.update(claim="Price is 2 dollars", kind="factual", verdict="supported", support_mode="direct",
                   rationale="Source states the unit price.")
        row["bindings"][0].update(contribution="helpful", rationale="Direct price statement.")
        second.update(id="C1-safety", claim="no adverse events occurred", kind="factual", verdict="insufficient_evidence",
                      gap="Need safety reporting, not pricing.", rationale="No safety data in the cited price source.")
        value["claims"].insert(1, second)
        value["claims"][2].update(kind="structural", rationale="Transition only.")
        result = self.save(value)
        self.assertEqual(result["metrics"]["eligible_claims"], 2)
        self.assertEqual(result["metrics"]["support_coverage"], .5)
        self.assertEqual(result["metrics"]["kind_counts"]["structural"], 1)
        value["claims"].pop()
        with self.assertRaisesRegex(ValueError, "cover every candidate"):
            self.save(value)

    def test_reject_stale_report_unknown_ids_and_invalid_support(self):
        valid = self.supported()
        mutations = [lambda x: x.update(report_hash="wrong"),
                     lambda x: x["claims"].append(copy.deepcopy(x["claims"][0])),
                     lambda x: x["claims"][0].update(claim="Invented claim"),
                     lambda x: x["claims"][0].update(kind="opinion"),
                     lambda x: x["claims"][0]["bindings"].pop(),
                     lambda x: x["claims"][0]["bindings"][0].update(confirmed=False),
                     lambda x: x["claims"][0].update(verdict="partial_support", gap="")]
        for mutate in mutations:
            value = copy.deepcopy(valid)
            mutate(value)
            with self.assertRaises(ValueError):
                self.save(value)

    def test_historical_snapshots_are_used_and_later_corruption_is_not_support(self):
        result = self.save(self.supported())
        ws.save_source(self.price["ref"], {"url": "/local/changed.txt", "content_scope": "abstract"}, "Different price.")
        current = review.claims(ws, research.reporting, str(self.report))
        self.assertEqual(current["metrics"]["fully_supported_claims"], 1)
        source = ws.root() / "sources" / self.price["ref"] / f"{self.price['content_hash']}.txt"
        source.write_text("Corrupt original text.")
        current = review.claims(ws, research.reporting, str(self.report))
        self.assertEqual(current["review_hash"], result["review_hash"])
        self.assertEqual(current["metrics"]["fully_supported_claims"], 0)
        self.assertEqual(current["claims"][0]["effective_verdict"], "read_error")

    def test_claims_cli_round_trip(self):
        value = self.supported()
        path = self.folder / "input.json"
        path.write_text(json.dumps(value))
        result = subprocess.run([sys.executable, str(SCRIPT), "claims", str(self.report), "--judgments", str(path)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[:1500])
        self.assertEqual(json.loads(result.stdout)["metrics"]["fully_supported_claims"], 1)

    def gap(self, max_attempts=3):
        value = self.supported()
        value["claims"][0].update(verdict="partial_support", gap="Need current workload confirmation.")
        self.save(value)
        return review.new_gap(ws, research.reporting, str(self.report), "C1", "N1", max_attempts=max_attempts)

    def search_gap(self, gap, query="Service v3 workload"):
        return research._ref_search(query, 3, None, None, None, "direct", gap=gap["id"])

    def test_gap_requires_review_and_blocks_repeats_pending_outcomes_and_two_empty_rounds(self):
        with self.assertRaisesRegex(ValueError, "explicit claim judgments"):
            review.new_gap(ws, research.reporting, str(self.report), "C1", "N1")
        gap = self.gap()
        with mock.patch.object(research, "search", return_value={"results": []}) as search:
            result = self.search_gap(gap)
            self.assertEqual(result["gap_state"], "awaiting_review")
            self.assertEqual(ws.status()["task"]["recent_searches"][-1]["gap_id"], gap["id"])
            with self.assertRaisesRegex(ValueError, "pending outcome"):
                self.search_gap(gap, "different workload query")
            with self.assertRaisesRegex(ValueError, "newly read evidence"):
                review.gap_result(ws, gap["id"], "progress", "New URL only.", [self.load["id"]])
            review.gap_result(ws, gap["id"], "no_progress", "No new supporting passage.")
            with self.assertRaisesRegex(ValueError, "repeats"):
                self.search_gap(gap, " SERVICE  v3 workload ")
            self.search_gap(gap, "v3 workload official specification")
            ended = review.gap_result(ws, gap["id"], "no_progress", "Same unsupported result.")
            self.assertEqual(ended["state"], "stopped")
            with self.assertRaisesRegex(ValueError, "bound"):
                self.search_gap(gap, "third query")
            self.assertEqual(search.call_count, 2)
        refreshed = review.new_gap(ws, research.reporting, str(self.report), "C1", "N1", max_attempts=6)
        self.assertEqual(refreshed["state"], "stopped")
        self.assertEqual(refreshed["max_attempts"], 3)
        self.assertNotIn("seen_evidence_ids", refreshed)
        self.assertTrue(Path(refreshed["artifact_path"]).exists())
        summary = ws.status()["task"]
        self.assertNotIn("review_gaps", summary)
        self.assertEqual(summary["recent_review_gaps"][0]["id"], gap["id"])
        self.assertEqual(ws.status()["task"]["needs"]["N1"]["state"], "open")

    def test_gap_failures_consume_bound_and_do_not_reopen_stopped_task(self):
        gap = self.gap()
        with mock.patch.object(research, "search", side_effect=RuntimeError("provider unavailable")) as search:
            for query in ("first query", "second query"):
                with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                    self.search_gap(gap, query)
            with self.assertRaisesRegex(ValueError, "bound"):
                self.search_gap(gap, "third query")
            self.assertEqual(search.call_count, 2)
        self.assertEqual(ws.status()["task"]["search_count"], 2)

    def test_gap_progress_requires_new_quote_and_resolution_needs_supported_review(self):
        gap = self.gap(max_attempts=2)
        with mock.patch.object(research, "search", return_value={"results": []}):
            self.search_gap(gap)
            duplicate = ws.record_evidence(self.load["ref"], "N1", "A differently worded claim", self.load["quote"], "supports")
            with self.assertRaisesRegex(ValueError, "repeated cards"):
                review.gap_result(ws, gap["id"], "progress", "Same source passage.", [duplicate["id"]])
            new = self.card(4, "Confirmed workload for v3: 3 units.")
            with self.assertRaisesRegex(ValueError, "supported review"):
                review.gap_result(ws, gap["id"], "resolved", "Reviewer still partial.", [new["id"]])
            updated = self.supported()
            updated["claims"][0]["bindings"].append({"evidence_id": new["id"], "implicit": True,
                "confirmed": True, "contribution": "necessary", "rationale": "Confirms the current version."})
            self.save(updated)
            result = review.gap_result(ws, gap["id"], "resolved", "Re-reviewed all current premises.", [new["id"]])
            self.assertEqual(result["state"], "resolved")
            self.assertEqual(ws.status()["task"]["needs"]["N1"]["state"], "open")

    def test_refuted_evidence_does_not_increase_support_or_contribution(self):
        value = self.supported()
        row = value["claims"][0]
        row.update(verdict="refuted", support_mode="not_assessed", gap="The cost calculation conflicts with the current rate.")
        with self.assertRaisesRegex(ValueError, "supporting contributions"):
            self.save(value)
        for binding in row["bindings"]:
            binding.update(contribution="contradicts", rationale="Fixture marks contradictory evidence explicitly.")
        result = self.save(value)
        self.assertEqual(result["metrics"]["support_coverage"], 0)
        self.assertEqual(result["metrics"]["citation_contribution_rate"], 0)


if __name__ == "__main__":
    unittest.main()
