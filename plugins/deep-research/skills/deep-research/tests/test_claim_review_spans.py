import copy
import hashlib
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
SPEC = importlib.util.spec_from_file_location("claim_span_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)
ws, review = research.workspace, research.claim_review


class ClaimReviewSpanTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        env = mock.patch.dict(os.environ, {
            "DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
            "DEEP_RESEARCH_SOURCE_POLICY": "local_only",
        })
        env.start()
        self.addCleanup(env.stop)
        ws.init_task("Review cost and safety", "Service v3")
        ws.set_need("N1", question="What are the cost and safety facts?", proof="Original source passages")
        self.price = self.card(1, "Unit price is 2 dollars.")
        self.load = self.card(2, "Workload is 3 units.")
        self.report = self.folder / "report.md"
        self.report.write_text(
            f"Price is 2 dollars and no adverse events occurred. [{self.price['id']}]",
            encoding="utf-8",
        )

    def card(self, index, text):
        ref = f"REF-{index:016X}"
        ws.save_source(ref, {"url": f"/local/source{index}.txt", "content_scope": "local_file"}, text)
        return ws.record_evidence(ref, "N1", text, text, "supports")

    def prepare(self, schema_version=2):
        result = review.claims(ws, research.reporting, str(self.report))
        value = json.loads(Path(result["template_path"]).read_text(encoding="utf-8"))
        value["reviewer"] = "span-reviewer"
        if schema_version == 2:
            value["schema_version"] = 2
            value["candidate_lengths"] = {row["candidate_id"]: len(row["claim"]) for row in value["claims"]}
            for row in value["claims"]:
                row.update(span=[0, len(row["claim"])], support_origin="not_assessed", derivation=None)
        return value

    def save(self, value):
        path = self.folder / "judgments.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return review.claims(ws, research.reporting, str(self.report), judgments_path=str(path), limit=10)

    def supported(self, row, *, mode="direct", origin="source_stated"):
        row.update(kind="factual", verdict="supported", support_mode=mode,
                   support_origin=origin, derivation=None,
                   rationale="The reviewer checked the whole marked statement against the saved passages.")
        for binding in row["bindings"]:
            binding.update(contribution="necessary", rationale="The saved passage supplies a stated premise.")
        return row

    def split(self, value, position):
        first = copy.deepcopy(value["claims"][0])
        second = copy.deepcopy(first)
        sentence = first["claim"]
        first.update(span=[0, position], claim=sentence[:position])
        second.update(id="C1-rest", span=[position, len(sentence)], claim=sentence[position:])
        self.supported(first)
        second.update(kind="factual", verdict="insufficient_evidence", support_mode="not_assessed",
                      support_origin="not_assessed", derivation=None,
                      rationale="The source reports price only and contains no safety observations.",
                      gap="Need an original safety observation for the remaining statement.")
        for binding in second["bindings"]:
            binding.update(contribution="irrelevant", rationale="A price statement does not establish safety.")
        value["claims"] = [first, second]
        return value

    def derived(self, row, *, mode="joint"):
        self.supported(row, mode=mode, origin="derived")
        row["kind"] = "inference"
        row["derivation"] = {
            "premise_evidence_ids": [binding["evidence_id"] for binding in row["bindings"]],
            "formula": "2 * 3 = 6", "units": "dollars/unit * units = dollars", "assumptions": [],
        }
        return row

    def test_explicit_v2_template_covers_whole_sentence_and_leaves_v1_template_intact(self):
        original = review.claims(ws, research.reporting, str(self.report))
        old_path = Path(original["template_path"])
        old_bytes = old_path.read_bytes()
        current = review.claims(ws, research.reporting, str(self.report), schema_version=2)
        path = Path(current["template_path"])
        template = json.loads(path.read_text(encoding="utf-8"))
        row = template["claims"][0]
        self.assertEqual(template["schema_version"], 2)
        self.assertEqual(path.name, "judgments.v2.template.json")
        self.assertNotEqual(path, old_path)
        self.assertEqual(old_path.read_bytes(), old_bytes)
        self.assertEqual(row["span"], [0, len(row["claim"])])
        self.assertEqual(template["candidate_lengths"], {row["candidate_id"]: len(row["claim"])})
        self.assertEqual(row["support_origin"], "not_assessed")
        self.assertIsNone(row["derivation"])
        self.assertIsNone(current["review_hash"])
        self.assertFalse((path.parent / "latest.json").exists())

    def test_prefix_only_review_cannot_omit_unsupported_second_half(self):
        value = self.prepare()
        row = self.supported(value["claims"][0])
        prefix = "Price is 2 dollars"
        row.update(claim=prefix, span=[0, len(prefix)])
        with self.assertRaises(ValueError):
            self.save(value)
        folder = ws.root() / "claim-reviews" / value["report_hash"]
        self.assertFalse((folder / "latest.json").exists())

    def test_duplicate_correct_fragment_cannot_cover_the_same_characters_twice(self):
        value = self.prepare()
        self.split(value, len("Price is 2 dollars"))
        duplicate = copy.deepcopy(value["claims"][0])
        duplicate["id"] = "C1-repeat"
        value["claims"].append(duplicate)
        with self.assertRaises(ValueError):
            self.save(value)

    def test_identical_fragments_at_distinct_positions_are_valid(self):
        fragment = "Price is 2 dollars; "
        self.report.write_text(fragment * 2 + f"[{self.price['id']}]", encoding="utf-8")
        value = self.prepare()
        sentence = value["claims"][0]["claim"]
        template_row = value["claims"][0]
        value["claims"] = []
        for index in range(2):
            row = copy.deepcopy(template_row)
            row.update(id=f"C1-price-{index}", claim=fragment,
                       span=[index * len(fragment), (index + 1) * len(fragment)])
            self.supported(row)
            value["claims"].append(row)
        citation = copy.deepcopy(template_row)
        citation.update(id="C1-citation", claim=sentence[2 * len(fragment):],
                        span=[2 * len(fragment), len(sentence)], kind="structural",
                        rationale="The evidence marker is retained in its own structural segment.")
        value["claims"].append(citation)
        result = self.save(value)
        self.assertEqual(result["claims"][0]["claim"], result["claims"][1]["claim"])
        self.assertNotEqual(result["claims"][0]["span"], result["claims"][1]["span"])
        self.assertEqual(result["metrics"]["candidate_metrics"]["eligible_candidates"], 1)
        self.assertEqual(result["metrics"]["candidate_metrics"]["fully_supported_candidates"], 1)

    def test_external_candidate_length_cannot_hide_the_unreviewed_suffix(self):
        value = self.prepare()
        row = self.supported(value["claims"][0])
        prefix = "Price is 2 dollars"
        row.update(claim=prefix, span=[0, len(prefix)])
        value["candidate_lengths"][row["candidate_id"]] = len(prefix)
        with self.assertRaises(ValueError):
            self.save(value)

    def test_rejects_out_of_bounds_empty_and_noninteger_spans(self):
        original = self.prepare()
        self.supported(original["claims"][0])
        end = len(original["claims"][0]["claim"])
        for span in ([-1, end], [0, end + 1], [0, 0], [end, 0],
                     [False, end], [0, float(end)], [0], [0, end, end], None):
            with self.subTest(span=span):
                value = copy.deepcopy(original)
                value["claims"][0]["span"] = span
                with self.assertRaises(ValueError):
                    self.save(value)

    def test_rejects_overlapping_and_gapped_spans_even_when_each_text_matches(self):
        original = self.prepare()
        sentence = original["claims"][0]["claim"]
        position = len("Price is 2 dollars")
        for first_end, second_start in ((position + 1, position), (position - 1, position)):
            with self.subTest(first_end=first_end, second_start=second_start):
                value = self.split(copy.deepcopy(original), position)
                value["claims"][0].update(span=[0, first_end], claim=sentence[:first_end])
                value["claims"][1].update(span=[second_start, len(sentence)], claim=sentence[second_start:])
                with self.assertRaises(ValueError):
                    self.save(value)

    def test_rejects_claim_text_that_does_not_equal_its_exact_span(self):
        value = self.prepare()
        self.supported(value["claims"][0])
        value["claims"][0]["claim"] = "Price is 2 dollars"
        with self.assertRaises(ValueError):
            self.save(value)

    def test_whitespace_separator_can_be_classified_as_a_structural_span(self):
        value = self.prepare()
        template_row = copy.deepcopy(value["claims"][0])
        sentence = template_row["claim"]
        position = sentence.index(" ")
        self.split(value, position)
        value["claims"][1].update(claim=sentence[position + 1:], span=[position + 1, len(sentence)])
        whitespace = copy.deepcopy(template_row)
        whitespace.update(id="C1-space", claim=" ", span=[position, position + 1], kind="structural",
                          rationale="A separator between the adjacent words.")
        value["claims"].insert(1, whitespace)
        result = self.save(value)
        self.assertEqual(result["claims"][1]["claim"], " ")
        self.assertEqual(result["claims"][1]["kind"], "structural")
        self.assertTrue(result["metrics"]["candidate_metrics"]["coverage_verified"])
        self.assertEqual(result["metrics"]["candidate_metrics"]["fully_supported_candidates"], 0)
        whitespace.update(claim="", span=[position, position])
        with self.assertRaises(ValueError):
            self.save(value)

    def test_unicode_spans_use_character_indices_and_retain_connectors_and_citations(self):
        self.report.write_text(
            f"价格为2元🙂，且 e\u0301 无不良事件。 [{self.price['id']}]", encoding="utf-8")
        value = self.prepare()
        sentence = value["claims"][0]["claim"]
        position = sentence.index("，")
        self.split(value, position)
        result = self.save(value)
        self.assertGreater(len(sentence.encode("utf-8")), len(sentence))
        self.assertEqual([row["span"] for row in result["claims"]], [[0, position], [position, len(sentence)]])
        self.assertEqual("".join(row["claim"] for row in result["claims"]), sentence)
        self.assertIn("e\u0301", result["claims"][1]["claim"])
        self.assertEqual(result["metrics"]["candidate_metrics"]["support_coverage"], 0)
        broken = copy.deepcopy(value)
        broken["claims"][1]["span"][1] = len(sentence.encode("utf-8"))
        with self.assertRaises(ValueError):
            self.save(broken)

    def test_supported_and_unsupported_spans_do_not_make_a_fully_supported_candidate(self):
        value = self.split(self.prepare(), len("Price is 2 dollars"))
        result = self.save(value)
        metrics = result["metrics"]
        self.assertEqual(metrics["unit"], "claim_row")
        self.assertEqual(metrics["eligible_claims"], 2)
        self.assertEqual(metrics["support_coverage"], .5)
        candidates = metrics["candidate_metrics"]
        self.assertEqual(candidates["unit"], "candidate")
        self.assertTrue(candidates["coverage_verified"])
        self.assertEqual(candidates["candidate_count"], 1)
        self.assertEqual(candidates["eligible_candidates"], 1)
        self.assertEqual(candidates["assessed_candidates"], 1)
        self.assertEqual(candidates["fully_supported_candidates"], 0)
        self.assertEqual(candidates["support_coverage"], 0)
        self.assertEqual(ws.status()["task"]["needs"]["N1"]["state"], "open")

    def test_splitting_changes_row_denominator_but_preserves_candidate_denominator(self):
        value = self.prepare()
        row = self.supported(value["claims"][0])
        row.update(verdict="partial_support", gap="Need evidence for the safety half.")
        unsplit = self.save(value)["metrics"]
        split = self.save(self.split(copy.deepcopy(value), len("Price is 2 dollars")))["metrics"]
        self.assertEqual(unsplit["eligible_claims"], 1)
        self.assertEqual(split["eligible_claims"], 2)
        self.assertEqual(unsplit["support_coverage"], 0)
        self.assertEqual(split["support_coverage"], .5)
        for metrics in (unsplit, split):
            self.assertEqual(metrics["candidate_metrics"]["eligible_candidates"], 1)
            self.assertEqual(metrics["candidate_metrics"]["fully_supported_candidates"], 0)
            self.assertEqual(metrics["candidate_metrics"]["support_coverage"], 0)

    def test_unclassified_remainder_blocks_candidate_support(self):
        value = self.split(self.prepare(), len("Price is 2 dollars"))
        remainder = value["claims"][1]
        remainder.update(kind="unclassified", verdict="not_assessed", rationale="", gap="")
        for binding in remainder["bindings"]:
            binding.update(contribution="not_assessed", rationale="")
        result = self.save(value)
        self.assertEqual(result["metrics"]["support_coverage"], 1)
        candidates = result["metrics"]["candidate_metrics"]
        self.assertEqual(candidates["unclassified_candidates"], 1)
        self.assertEqual(candidates["fully_supported_candidates"], 0)
        self.assertEqual(candidates["support_coverage"], 0)

    def test_every_candidate_needs_its_own_complete_partition(self):
        self.report.write_text(self.report.read_text(encoding="utf-8") + "\n\nOverview follows.", encoding="utf-8")
        value = self.prepare()
        self.supported(value["claims"][0])
        value["claims"][1].update(kind="structural", rationale="A transition with no factual assertion.")
        result = self.save(value)
        candidates = result["metrics"]["candidate_metrics"]
        self.assertEqual(candidates["candidate_count"], 2)
        self.assertEqual(candidates["eligible_candidates"], 1)
        self.assertEqual(candidates["fully_supported_candidates"], 1)
        value["claims"].pop()
        with self.assertRaises(ValueError):
            self.save(value)

    def test_derived_origin_and_direct_mode_are_independent(self):
        self.report.write_text(f"Two units cost 4 dollars. [{self.price['id']}]", encoding="utf-8")
        value = self.prepare()
        row = self.derived(value["claims"][0], mode="direct")
        row["derivation"].update(formula="2 * 2 = 4", assumptions=["Quantity is two units."])
        result = self.save(value)
        self.assertEqual(result["claims"][0]["support_origin"], "derived")
        self.assertEqual(result["claims"][0]["support_mode"], "direct")
        self.assertEqual(result["claims"][0]["derivation"], row["derivation"])
        self.assertEqual(result["metrics"]["candidate_metrics"]["support_coverage"], 1)
        self.assertEqual(result["metrics"]["candidate_metrics"]["derived_supported_candidates"], 1)
        self.assertEqual(result["metrics"]["candidate_metrics"]["source_stated_supported_candidates"], 0)

    def test_joint_mode_does_not_imply_derived_origin(self):
        self.report.write_text(
            f"Price is 2 dollars and workload is 3 units. [{self.price['id']}][{self.load['id']}]",
            encoding="utf-8",
        )
        value = self.prepare()
        self.supported(value["claims"][0], mode="joint", origin="source_stated")
        result = self.save(value)
        self.assertEqual(result["claims"][0]["support_origin"], "source_stated")
        self.assertEqual(result["claims"][0]["support_mode"], "joint")
        self.assertIsNone(result["claims"][0]["derivation"])
        self.assertEqual(result["metrics"]["candidate_metrics"]["source_stated_supported_candidates"], 1)
        self.assertEqual(result["metrics"]["candidate_metrics"]["derived_supported_candidates"], 0)

    def test_rejects_origin_and_derivation_that_do_not_match_verdict_or_bindings(self):
        self.report.write_text(
            f"Total cost is 6 dollars. [{self.price['id']}][{self.load['id']}]", encoding="utf-8")
        original = self.prepare()
        self.derived(original["claims"][0], mode="direct")
        mutations = [
            lambda row: row.update(support_origin="not_assessed"),
            lambda row: row.update(support_origin="source_stated"),
            lambda row: row.update(derivation=None),
            lambda row: row["derivation"].update(premise_evidence_ids=[]),
            lambda row: row["derivation"].update(premise_evidence_ids=[self.price["id"], self.price["id"]]),
            lambda row: row["derivation"].update(premise_evidence_ids=["EV-missing"]),
            lambda row: row["derivation"].update(formula=" "),
            lambda row: row["derivation"].update(units=""),
            lambda row: row["derivation"].update(assumptions=[123]),
            lambda row: row["bindings"][0].update(contribution="irrelevant"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                value = copy.deepcopy(original)
                mutate(value["claims"][0])
                with self.assertRaises(ValueError):
                    self.save(value)
        value = self.split(self.prepare(), len("Total cost"))
        value["claims"][1]["support_origin"] = "source_stated"
        with self.assertRaises(ValueError):
            self.save(value)

    def test_later_snapshot_corruption_invalidates_candidate_support(self):
        self.report.write_text(f"Price is 2 dollars. [{self.price['id']}]", encoding="utf-8")
        value = self.prepare()
        self.supported(value["claims"][0])
        initial = self.save(value)
        source = ws.root() / "sources" / self.price["ref"] / f"{self.price['content_hash']}.txt"
        source.write_text("Corrupt saved passage.", encoding="utf-8")
        result = review.claims(ws, research.reporting, str(self.report))
        self.assertEqual(result["review_hash"], initial["review_hash"])
        self.assertEqual(result["claims"][0]["effective_verdict"], "read_error")
        candidates = result["metrics"]["candidate_metrics"]
        self.assertTrue(candidates["coverage_verified"])
        self.assertEqual(candidates["assessed_candidates"], 0)
        self.assertEqual(candidates["fully_supported_candidates"], 0)

    def test_v1_normalization_hash_bytes_and_old_metrics_survive_explicit_v2_review(self):
        self.report.write_text(
            f"Total cost is 6 dollars. [{self.price['id']}][{self.load['id']}]", encoding="utf-8")
        value = self.prepare(schema_version=1)
        value.pop("schema_version", None)
        row = value["claims"][0]
        row.update(kind="inference", verdict="supported", support_mode="joint",
                   rationale="The reviewer multiplies the two stated premises.")
        for binding in row["bindings"]:
            binding.update(contribution="necessary", rationale="This premise is required to compute cost.")
        original = self.save(value)
        expected_row = {key: row[key] for key in (
            "id", "candidate_id", "claim", "kind", "verdict", "rationale", "gap", "support_mode",
            "line", "end_line",
        )}
        cards = {card["id"]: card for card in (self.price, self.load)}
        expected_row["bindings"] = [{**binding, "evidence": cards[binding["evidence_id"]], "snapshot_issue": None}
                                    for binding in row["bindings"]]
        expected_record = {
            "schema_version": 1, "report_hash": value["report_hash"], "inventory_hash": value["inventory_hash"],
            "reviewer": value["reviewer"], "claims": [expected_row], "assessment": "external_reviewer",
        }
        expected_hash = hashlib.sha256(
            json.dumps(expected_record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        path = Path(original["review_path"])
        original_bytes = path.read_bytes()
        self.assertEqual(json.loads(original_bytes), expected_record)
        self.assertEqual(original["review_hash"], expected_hash)
        expected_metrics = {
            "claim_count": 1, "kind_counts": {"inference": 1}, "verdict_counts": {"supported": 1},
            "eligible_claims": 1, "assessed_claims": 1, "fully_supported_claims": 1,
            "support_coverage": 1.0, "semantic_assessment_coverage": 1.0, "unclassified_claims": 0,
            "confirmed_citation_links": 2, "assessed_citation_links": 2, "contributing_citation_links": 2,
            "citation_contribution_rate": 1.0, "invalid_citation_links": 0, "unconfirmed_implicit_proposals": 0,
            "limits": "Reviewer judgments only. Coverage uses factual/inference claims, not unknown or structural text. "
                      "Joint support does not require each source to entail the claim alone. Segmentation/classification may miss claims.",
        }
        self.assertEqual({key: original["metrics"][key] for key in expected_metrics}, expected_metrics)
        candidates = original["metrics"]["candidate_metrics"]
        self.assertFalse(candidates["coverage_verified"])
        self.assertEqual(candidates["candidate_count"], 1)
        for key in ("eligible_candidates", "assessed_candidates", "fully_supported_candidates",
                    "unclassified_candidates", "support_coverage", "semantic_assessment_coverage",
                    "source_stated_supported_candidates", "derived_supported_candidates"):
            self.assertIsNone(candidates[key])
        latest = path.parent / "latest.json"
        latest_bytes = latest.read_bytes()
        v2 = self.prepare()
        self.assertEqual(latest.read_bytes(), latest_bytes)
        self.assertEqual(path.read_bytes(), original_bytes)
        self.derived(v2["claims"][0])
        updated = self.save(v2)
        self.assertNotEqual(updated["review_hash"], original["review_hash"])
        self.assertEqual(json.loads(latest.read_text(encoding="utf-8"))["review_hash"], updated["review_hash"])
        self.assertEqual(path.read_bytes(), original_bytes)
        saved = review.load_review(ws, original["report_hash"], original["review_hash"])
        self.assertEqual({key: saved[key] for key in expected_record}, expected_record)
        old_summary, _ = review.metrics(ws, saved)
        self.assertEqual({key: old_summary[key] for key in expected_metrics}, expected_metrics)

    def test_v1_partial_substring_remains_legacy_unverified_instead_of_being_migrated(self):
        value = self.prepare(schema_version=1)
        row = value["claims"][0]
        row.update(claim="Price is 2 dollars", kind="factual", verdict="supported", support_mode="direct",
                   rationale="The old reviewer classified only the price substring.")
        row["bindings"][0].update(contribution="necessary", rationale="The price source states the amount.")
        result = self.save(value)
        self.assertEqual(result["metrics"]["support_coverage"], 1)
        candidates = result["metrics"]["candidate_metrics"]
        self.assertFalse(candidates["coverage_verified"])
        self.assertIsNone(candidates["support_coverage"])
        record = json.loads(Path(result["review_path"]).read_text(encoding="utf-8"))
        self.assertEqual(record["schema_version"], 1)
        self.assertNotIn("span", record["claims"][0])
        self.assertNotIn("support_origin", record["claims"][0])

    def test_template_version_selection_does_not_downgrade_saved_v2_judgments(self):
        value = self.prepare()
        self.supported(value["claims"][0])
        saved = self.save(value)
        folder = Path(saved["review_path"]).parent
        latest = (folder / "latest.json").read_bytes()
        automatic = review.claims(ws, research.reporting, str(self.report))
        self.assertEqual(automatic["schema_version"], 2)
        self.assertEqual(automatic["template_schema_version"], 2)
        self.assertEqual(Path(automatic["template_path"]).name, "judgments.v2.template.json")
        old_template = review.claims(ws, research.reporting, str(self.report), schema_version=1)
        self.assertEqual(old_template["schema_version"], 2)
        self.assertEqual(old_template["template_schema_version"], 1)
        self.assertEqual(old_template["review_hash"], saved["review_hash"])
        self.assertEqual(Path(old_template["template_path"]).name, "judgments.template.json")
        self.assertTrue(old_template["metrics"]["candidate_metrics"]["coverage_verified"])
        self.assertEqual((folder / "latest.json").read_bytes(), latest)

    def test_explicit_version_conflicts_and_unknown_schemas_do_not_replace_latest_review(self):
        valid = self.prepare()
        self.supported(valid["claims"][0])
        saved = self.save(valid)
        latest = Path(saved["review_path"]).parent / "latest.json"
        before = latest.read_bytes()
        path = self.folder / "judgments.json"
        with self.assertRaisesRegex(ValueError, "conflict"):
            review.claims(ws, research.reporting, str(self.report), judgments_path=str(path), schema_version=1)
        for version in (0, 3, True, "2", None):
            with self.subTest(schema_version=version):
                value = copy.deepcopy(valid)
                value["schema_version"] = version
                with self.assertRaises(ValueError):
                    self.save(value)
                self.assertEqual(latest.read_bytes(), before)

    def test_resubmission_rejects_corrupt_existing_hash_before_replacing_latest(self):
        self.report.write_text(f"Price is 2 dollars. [{self.price['id']}]", encoding="utf-8")
        original = self.prepare()
        self.supported(original["claims"][0])
        first = self.save(original)
        changed = copy.deepcopy(original)
        changed["claims"][0]["rationale"] += " An independent second review confirms the statement."
        second = self.save(changed)
        self.assertNotEqual(first["review_hash"], second["review_hash"])
        Path(second["review_path"]).write_text("{}", encoding="utf-8")
        self.save(original)
        latest = Path(first["review_path"]).parent / "latest.json"
        before = latest.read_bytes()
        with self.assertRaises(ValueError):
            self.save(changed)
        self.assertEqual(latest.read_bytes(), before)
        current = review.claims(ws, research.reporting, str(self.report))
        self.assertEqual(current["review_hash"], first["review_hash"])
        self.assertEqual(current["metrics"]["candidate_metrics"]["support_coverage"], 1)

    def test_gap_cannot_be_resolved_by_reusing_its_row_id_for_a_smaller_supported_span(self):
        value = self.prepare()
        row = self.supported(value["claims"][0])
        row.update(verdict="partial_support", gap="Need safety observations for the rest of this statement.")
        self.save(value)
        original_span = list(row["span"])
        gap = review.new_gap(ws, research.reporting, str(self.report), "C1", "N1")
        self.assertEqual(gap["candidate_id"], "C1")
        self.assertEqual(gap["span"], original_span)
        with mock.patch.object(research, "search", return_value={"results": []}):
            research._ref_search("Service v3 safety observations", 3, None, None, None, "direct", gap=gap["id"])
        newer = self.card(3, "The current v3 price remains 2 dollars per unit.")
        changed = self.split(copy.deepcopy(value), len("Price is 2 dollars"))
        changed["claims"][0]["bindings"].append({
            "evidence_id": newer["id"], "implicit": True, "confirmed": True,
            "contribution": "necessary", "rationale": "This new passage confirms the current price.",
        })
        self.save(changed)
        task_path = ws.root() / "task.json"
        before = task_path.read_bytes()
        with self.assertRaises(ValueError):
            review.gap_result(ws, gap["id"], "resolved", "The price prefix now has new evidence.", [newer["id"]])
        self.assertEqual(task_path.read_bytes(), before)
        pending = json.loads(before)["review_gaps"][gap["id"]]
        self.assertEqual(pending["state"], "awaiting_review")
        self.assertEqual(pending["span"], original_span)
        changed["claims"][0].update(verdict="partial_support", gap="Need an additional price qualifier.")
        self.save(changed)
        prefix_gap = review.new_gap(ws, research.reporting, str(self.report), "C1", "N1")
        remainder_gap = review.new_gap(ws, research.reporting, str(self.report), "C1-rest", "N1")
        self.assertNotEqual(prefix_gap["id"], gap["id"])
        self.assertNotEqual(remainder_gap["id"], gap["id"])
        self.assertEqual(prefix_gap["span"], changed["claims"][0]["span"])
        task = json.loads(task_path.read_text(encoding="utf-8"))
        self.assertEqual(task["review_gaps"][gap["id"]]["state"], "awaiting_review")
        self.assertEqual(len(task["review_gaps"][gap["id"]]["attempts"]), 1)

    def test_same_gap_span_can_resolve_after_full_supported_review_with_new_evidence(self):
        value = self.prepare()
        row = self.supported(value["claims"][0])
        row.update(verdict="partial_support", gap="Need original safety observations.")
        self.save(value)
        gap = review.new_gap(ws, research.reporting, str(self.report), "C1", "N1")
        with mock.patch.object(research, "search", return_value={"results": []}):
            research._ref_search("Service v3 safety observations", 3, None, None, None, "direct", gap=gap["id"])
        newer = self.card(3, "No adverse events occurred in the observed v3 workload.")
        self.supported(row)
        row["bindings"].append({
            "evidence_id": newer["id"], "implicit": True, "confirmed": True,
            "contribution": "necessary", "rationale": "The new passage states the previously missing safety observation.",
        })
        saved = self.save(value)
        result = review.gap_result(ws, gap["id"], "resolved", "The complete original span was reviewed again.", [newer["id"]])
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(result["span"], row["span"])
        self.assertEqual(result["candidate_id"], row["candidate_id"])
        self.assertEqual(result["resolved_review_hash"], saved["review_hash"])
        self.assertEqual(ws.status()["task"]["needs"]["N1"]["state"], "open")

    def test_long_derivation_is_bounded_in_preview_and_preserved_in_saved_record(self):
        self.report.write_text(
            f"Total cost is 6 dollars. [{self.price['id']}][{self.load['id']}]", encoding="utf-8")
        value = self.prepare()
        row = self.derived(value["claims"][0])
        row["derivation"].update(
            formula="2 * 3 = 6; " + "x" * 8000,
            units="dollars per unit times units; " * 200,
            assumptions=[f"Assumption {index}: " + "a" * 2000 for index in range(25)],
        )
        result = self.save(value)
        preview = result["claims"][0]
        self.assertTrue(preview["preview_truncated"])
        self.assertLessEqual(len(preview["derivation"]["formula"]), 1600)
        self.assertLessEqual(len(preview["derivation"]["units"]), 800)
        self.assertLessEqual(len(preview["derivation"]["assumptions"]), 10)
        self.assertTrue(all(len(item) <= 800 for item in preview["derivation"]["assumptions"]))
        self.assertLess(len(json.dumps(result, ensure_ascii=False).encode()), 40000)
        record = json.loads(Path(result["review_path"]).read_text(encoding="utf-8"))
        self.assertEqual(record["claims"][0]["derivation"], row["derivation"])
        self.assertEqual(result["metrics"]["candidate_metrics"]["derived_supported_candidates"], 1)

    def test_long_unicode_derivation_respects_the_actual_utf8_page_budget(self):
        for token in ("论", "🙂"):
            with self.subTest(token=token):
                self.report.write_text(token * 1800 + f" [{self.price['id']}]", encoding="utf-8")
                value = self.prepare()
                row = self.derived(value["claims"][0], mode="direct")
                row.update(rationale="阅" * 1200, gap="缺" * 900)
                row["derivation"].update(
                    formula="式" * 5000, units="单" * 5000,
                    assumptions=["假" * 1000 for _ in range(20)],
                )
                result = self.save(value)
                preview_bytes = len(json.dumps(result["claims"], ensure_ascii=False).encode("utf-8"))
                self.assertLessEqual(preview_bytes, 40000)
                self.assertEqual(result["next_offset"], None)
                self.assertEqual(len(result["claims"]), 1)
                self.assertTrue(result["claims"][0]["preview_truncated"])
                record = json.loads(Path(result["review_path"]).read_text(encoding="utf-8"))
                self.assertEqual(record["claims"][0]["claim"], row["claim"])
                self.assertEqual(record["claims"][0]["rationale"], row["rationale"])
                self.assertEqual(record["claims"][0]["gap"], row["gap"])
                self.assertEqual(record["claims"][0]["derivation"], row["derivation"])

    def test_v2_cli_prepare_save_and_inspect_use_the_explicit_schema(self):
        self.report.write_text(f"Price is 2 dollars. [{self.price['id']}]", encoding="utf-8")
        command = [sys.executable, "-B", str(SCRIPT), "claims", str(self.report)]
        prepared = subprocess.run(command + ["--schema-version", "2"], capture_output=True, text=True, timeout=30)
        self.assertEqual(prepared.returncode, 0, (prepared.stdout + prepared.stderr)[:1500])
        preview = json.loads(prepared.stdout)
        self.assertEqual(preview["schema_version"], 2)
        self.assertIsNone(preview["review_hash"])
        value = json.loads(Path(preview["template_path"]).read_text(encoding="utf-8"))
        value["reviewer"] = "cli-span-reviewer"
        self.supported(value["claims"][0])
        path = self.folder / "cli-judgments.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        saved = subprocess.run(command + ["--judgments", str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(saved.returncode, 0, (saved.stdout + saved.stderr)[:1500])
        result = json.loads(saved.stdout)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["metrics"]["candidate_metrics"]["support_coverage"], 1)
        inspected = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(inspected.returncode, 0, (inspected.stdout + inspected.stderr)[:1500])
        self.assertEqual(json.loads(inspected.stdout)["review_hash"], result["review_hash"])


if __name__ == "__main__":
    unittest.main()
