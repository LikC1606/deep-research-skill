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
SPEC = importlib.util.spec_from_file_location("reporting_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)
ws, reports = research.workspace, research.reporting


class ReportingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        env = mock.patch.dict(os.environ, {"DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
                                           "DEEP_RESEARCH_SOURCE_POLICY": "local_only"})
        env.start()
        self.addCleanup(env.stop)
        ws.init_task("Compare trials", "Trials A and B")
        for need in ("N1", "N2", "N3"):
            ws.set_need(need, question=f"Size of {need}", proof="Reported sample size")

    def card(self, need="N1", relation="supports", text="12 samples", index=1, url=None):
        ref = f"REF-{index:016X}"
        ws.save_source(ref, {"url": url or str(self.folder / f"source{index}.txt"), "content_scope": "local_file"}, text)
        return ws.record_evidence(ref, need, "The trial used 12 samples", text, relation)

    def section(self):
        return reports.set_section(ws, "results", "Trial results", ["N1", "N2"], perspective="Replication")

    def test_outline_exposes_unassigned_needs_and_revisions_do_not_drop_the_checklist(self):
        self.section()
        plan = reports.outline(ws)
        self.assertEqual(plan["unassigned_needs"], ["N3"])
        self.assertEqual(plan["sections"][0]["needs_without_evidence"], ["N1", "N2"])
        before = (ws.root() / "outline.json").read_bytes()
        self.section()
        self.assertEqual((ws.root() / "outline.json").read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "requires a reason"):
            reports.set_section(ws, "results", "Narrower results", ["N1"])
        reports.set_section(ws, "results", "Narrower results", ["N1"], reason="N2 needs a separate cost chapter")
        self.assertEqual(reports.outline(ws)["unassigned_needs"], ["N2", "N3"])
        self.assertEqual(len(ws.status()["task"]["needs"]), 3)
        self.assertEqual(reports.outline(ws)["revision"], 2)
        self.assertEqual(reports.outline(ws)["sections"][0]["perspective"], "Replication")

    def test_section_rejects_unknown_needs_and_unsafe_ids(self):
        for section, needs in (("../escape", ["N1"]), ("ok", ["unknown"]), ("ok", [])):
            with self.subTest(section=section, needs=needs), self.assertRaises(ValueError):
                reports.set_section(ws, section, "Title", needs)

    def test_outline_and_packet_keep_answer_review_separate_from_synthesis_refresh(self):
        self.section()
        support = self.card()
        ws.set_need("N1", state="answered", note="12 samples reported", evidence_ids=[support["id"]])
        reports.save_synthesis(ws, "results", "Trial size", [support["id"]])
        self.card("N1", "refutes", "24 samples", 2)
        before = (ws.root() / "task.json").read_bytes()
        chapter = reports.outline(ws)["sections"][0]
        self.assertEqual(chapter["remaining_needs"], ["N1", "N2"])
        self.assertEqual(chapter["needs_requiring_review"], ["N1"])
        packet = reports.packet(ws, "results")
        self.assertTrue(packet["synthesis_stale"])
        self.assertEqual(packet["needs_requiring_review"], ["N1"])
        self.assertEqual(packet["needs_requiring_review_count"], 1)
        self.assertEqual(packet["remaining_need_count"], 2)
        saved = json.loads(Path(packet["packet_path"]).read_text())
        self.assertEqual(saved["needs_requiring_review"], ["N1"])
        self.assertEqual((ws.root() / "task.json").read_bytes(), before)
        reports.save_synthesis(ws, "results", "Scope needs review", [support["id"]])
        packet = reports.packet(ws, "results")
        self.assertFalse(packet["synthesis_stale"])
        self.assertEqual(packet["needs_requiring_review"], ["N1"])
        ws.set_need("N1", state="answered", note="Reviewed the differing evidence scopes")
        self.assertEqual(reports.outline(ws)["sections"][0]["remaining_needs"], ["N2"])
        packet = reports.packet(ws, "results")
        self.assertTrue(packet["synthesis_stale"])
        self.assertEqual(packet["needs_requiring_review"], [])
        self.assertEqual(packet["needs_requiring_review_count"], 0)
        self.assertEqual(packet["remaining_needs"], ["N2"])

    def test_synthesis_is_separate_and_packets_include_unselected_counterevidence(self):
        self.section()
        support = self.card()
        refute = self.card("N1", "refutes", "24 samples", 2)
        original = (ws.root() / "evidence.json").read_bytes()
        reports.save_synthesis(ws, "results", "Conflicting sample counts", [support["id"]],
                               inferences=["Resolve versions before comparison"], limitations=["Version unknown"])
        packet = reports.packet(ws, "results", limit=1)
        self.assertFalse(packet["synthesis_stale"])
        self.assertEqual(packet["total_evidence"], 2)
        next_page = reports.packet(ws, "results", offset=packet["next_offset"])
        self.assertEqual(next_page["evidence"][0]["id"], refute["id"])
        self.assertFalse(next_page["evidence"][0]["selected_in_synthesis"])
        self.assertEqual((ws.root() / "evidence.json").read_bytes(), original)

    def test_packet_limits_utf8_bytes_and_paginates_every_card_including_late_refutation(self):
        self.section()
        first = self.card(text="资料" * 2000)
        expected = [first["id"]]
        for index in range(5):
            card = ws.record_evidence(first["ref"], "N1", f"Observation {index}", first["quote"],
                                      "refutes" if index == 4 else "supports")
            expected.append(card["id"])
        original = (ws.root() / "evidence.json").read_bytes()
        page = reports.packet(ws, "results", limit=10)
        self.assertTrue(page["page_limited_by_bytes"])
        self.assertEqual(page["relation_counts"], {"supports": 5, "refutes": 1, "context": 0})
        self.assertFalse(any(card["relation"] == "refutes" for card in page["evidence"]))
        artifact = page["packet_path"]
        seen = []
        while True:
            self.assertLessEqual(len(json.dumps(page, ensure_ascii=False).encode()) + 1, reports.MAX_PACKET_BYTES)
            self.assertEqual(page["packet_path"], artifact)
            seen.extend(card["id"] for card in page["evidence"])
            if page["next_offset"] is None:
                break
            self.assertEqual(page["next_offset"], len(seen))
            page = reports.packet(ws, "results", offset=page["next_offset"], limit=10)
        self.assertEqual(seen, expected)
        self.assertEqual(page["evidence"][-1]["relation"], "refutes")
        saved = json.loads(Path(artifact).read_text())
        self.assertEqual([card["id"] for card in saved["evidence"]], expected)
        self.assertTrue(all(card["quote"] == first["quote"] for card in saved["evidence"]))
        self.assertEqual((ws.root() / "evidence.json").read_bytes(), original)

    def test_packet_bounds_large_need_and_synthesis_context_without_losing_saved_text(self):
        ws.set_need("long", question="问题" * 20_000, proof="标准" * 20_000)
        ws.set_need("N1", state="partial", note="缺口" * 20_000)
        reports.set_section(ws, "results", "Trial results", ["N1", "long"])
        card = self.card()
        reports.save_synthesis(ws, "results", "综合" * 6000, [card["id"]],
                               inferences=["前提" * 20_000], limitations=["限制" * 20_000])
        page = reports.packet(ws, "results")
        self.assertLessEqual(len(json.dumps(page, ensure_ascii=False).encode()) + 1, reports.MAX_PACKET_BYTES)
        self.assertTrue(page["context_preview_truncated"])
        self.assertTrue(page["preview_truncated"])
        self.assertEqual(page["needs"]["N1"]["state"], "partial")
        self.assertEqual(page["total_needs"], 2)
        self.assertEqual(page["remaining_need_count"], 2)
        self.assertEqual(page["needs_without_evidence"], ["long"])
        saved = json.loads(Path(page["packet_path"]).read_text())
        self.assertEqual(saved["needs"]["long"]["question"], "问题" * 20_000)
        self.assertEqual(saved["synthesis"]["inferences"], ["前提" * 20_000])
        self.assertEqual(saved["synthesis"]["summary"], "综合" * 6000)

    def test_packet_bounds_large_claims_and_sources_and_marks_each_preview(self):
        self.section()
        first = self.card(url="https://read.example/" + "x" * 8000)
        card = ws.record_evidence(first["ref"], "N1", "判断" * 30_000, first["quote"], "supports")
        page = reports.packet(ws, "results")
        self.assertLessEqual(len(json.dumps(page, ensure_ascii=False).encode()) + 1, reports.MAX_PACKET_BYTES)
        self.assertTrue(page["preview_truncated"])
        self.assertFalse(page["context_preview_truncated"])
        preview = next(item for item in page["evidence"] if item["id"] == card["id"])
        self.assertTrue(preview["preview_truncated"])
        self.assertEqual(preview["quote"], card["quote"])
        saved = json.loads(Path(page["packet_path"]).read_text())
        original = next(item for item in saved["evidence"] if item["id"] == card["id"])
        self.assertEqual(original["claim"], card["claim"])
        self.assertEqual(original["source"], card["source"])

    def test_packet_snapshots_survive_new_evidence_and_remain_available_after_stop(self):
        self.section()
        first = self.card()
        reports.save_synthesis(ws, "results", "Initial result", [first["id"]])
        old = reports.packet(ws, "results")
        saved = Path(old["packet_path"]).read_bytes()
        self.card("N2", index=2)
        new = reports.packet(ws, "results")
        self.assertNotEqual(new["packet_path"], old["packet_path"])
        self.assertNotEqual(new["basis_hash"], old["basis_hash"])
        self.assertEqual(Path(old["packet_path"]).read_bytes(), saved)
        self.assertTrue(new["synthesis_stale"])
        self.assertEqual(new["new_evidence_count"], 1)
        ws.stop_task("Continue later")
        self.assertEqual(reports.packet(ws, "results")["packet_path"], new["packet_path"])

    def test_empty_or_exhausted_packet_and_invalid_pagination(self):
        self.section()
        for offset in (0, 30):
            with self.subTest(offset=offset):
                page = reports.packet(ws, "results", offset=offset)
                self.assertEqual(page["evidence"], [])
                self.assertIsNone(page["next_offset"])
                self.assertEqual(page["needs_without_evidence_count"], 2)
                self.assertEqual(page["relation_counts"], {"supports": 0, "refutes": 0, "context": 0})
        for offset, limit in ((-1, 5), (0, 0), (0, 11)):
            with self.subTest(offset=offset, limit=limit), self.assertRaises(ValueError):
                reports.packet(ws, "results", offset=offset, limit=limit)

    def test_new_evidence_or_outline_changes_make_synthesis_stale(self):
        self.section()
        first = self.card()
        reports.save_synthesis(ws, "results", "Initial result", [first["id"]])
        new = self.card("N2", index=2)
        packet = reports.packet(ws, "results")
        self.assertTrue(packet["synthesis_stale"])
        self.assertEqual(packet["new_evidence_ids"], [new["id"]])
        reports.save_synthesis(ws, "results", "Updated result", [first["id"], new["id"]])
        self.assertFalse(reports.packet(ws, "results")["synthesis_stale"])
        reports.set_section(ws, "results", "Revised title", ["N1", "N2"], reason="Separate replication from cost")
        self.assertTrue(reports.packet(ws, "results")["synthesis_stale"])

    def test_synthesis_cannot_import_unrelated_evidence_or_write_after_stop(self):
        self.section()
        unrelated = self.card("N3")
        with self.assertRaisesRegex(ValueError, "assigned to this section"):
            reports.save_synthesis(ws, "results", "Wrong chapter", [unrelated["id"]])
        ws.stop_task("Continue later")
        self.assertEqual(reports.packet(ws, "results")["total_evidence"], 0)
        with self.assertRaisesRegex(ValueError, "use resume"):
            reports.save_synthesis(ws, "results", "Stopped", [unrelated["id"]])

    def audit(self, text, **kwargs):
        path = self.folder / "report.md"
        path.write_text(text, encoding="utf-8")
        return reports.audit_report(ws, str(path), **kwargs)

    def test_audit_binds_markers_after_punctuation_without_semantic_autopass(self):
        card = self.card()
        result = self.audit(f"# Results\nThe trial used 12 samples. [{card['id']}]\n")
        self.assertEqual(result["total_candidates"], 1)
        self.assertEqual(result["bound_candidates"], 1)
        self.assertEqual(result["issue_counts"], {})
        self.assertEqual(result["bindings"][0]["evidence"][0]["quote"], "12 samples")
        self.assertEqual(result["semantic_verdict"], "not_assessed")
        self.assertEqual(result["bindings"][0]["semantic_verdict"], "not_assessed")

    def test_audit_detects_dangling_unbound_and_refutation_without_hiding_them(self):
        card = self.card(relation="refutes")
        result = self.audit(f"首个结论。[{card['id']}]另一个结论。[EV-MISSING]\nNo citation here.\n```\nIgnore code.\n```\n")
        self.assertEqual(result["total_candidates"], 3)
        self.assertEqual(result["issue_counts"], {"refutes_relation": 1, "dangling_evidence": 1, "unbound_text": 1})

    def test_audit_checks_historical_locator_and_reports_corrupt_snapshot(self):
        card = self.card()
        ws.save_source(card["ref"], {"url": "new", "content_scope": "local_file"}, "24 samples")
        result = self.audit(f"12 samples [{card['id']}]")
        self.assertNotIn("invalid_snapshot", result["issue_counts"])
        metadata, _ = ws.require_source(card["ref"], card["content_hash"])
        Path(metadata["body_path"]).write_text("tampered", encoding="utf-8")
        self.assertEqual(self.audit(f"12 samples [{card['id']}]")["issue_counts"]["invalid_snapshot"], 1)

    def test_audit_checks_each_metadata_snapshot_even_when_body_hashes_match(self):
        old = self.card()
        ws.save_source(old["ref"], {"url": old["source"], "content_scope": "abstract", "truncated": True,
                                    "pages": [{"page": 7, "start_char": 0, "end_char": 10}]}, "12 samples")
        new = ws.record_evidence(old["ref"], "N1", old["claim"], old["quote"], "supports")
        self.assertEqual(old["content_hash"], new["content_hash"])
        text = f"12 samples [{old['id']}] [{new['id']}]"
        result = self.audit(text)
        self.assertNotIn("invalid_snapshot", result["issue_counts"])
        for card in (old, new):
            self.assertIn(card["snapshot_id"], json.dumps(result["bindings"]))
        metadata, _ = ws.require_source(new["ref"], new["snapshot_id"])
        path = Path(metadata["body_path"]).with_name(f"{new['snapshot_id']}.json")
        metadata["pages"][0]["page"] = 99
        path.write_text(json.dumps(metadata), encoding="utf-8")
        self.assertEqual(self.audit(text)["issue_counts"]["invalid_snapshot"], 1)
        self.assertNotIn("invalid_snapshot", self.audit(f"12 samples [{old['id']}]")["issue_counts"])

    def test_audit_accepts_legacy_cards_without_snapshot_ids(self):
        card = self.card()
        cards = ws._evidence()
        del cards[card["id"]]["snapshot_id"]
        ws._write_json(ws.root() / "evidence.json", cards)
        result = self.audit(f"12 samples [{card['id']}]")
        self.assertNotIn("invalid_snapshot", result["issue_counts"])

    def test_audit_checks_read_url_and_evidence_url_mismatch(self):
        card = self.card()
        ws._write_json(ws.root().parent / ".deep-research-citations.json", ["https://read.example/result"])
        result = self.audit(f"Result [{card['id']}] [source](https://unread.example/result) [other](https://read.example/result)")
        self.assertEqual(result["issue_counts"]["unread_url"], 1)
        self.assertEqual(result["issue_counts"]["url_evidence_mismatch"], 1)

    def test_audit_checks_naked_urls_but_ignores_inline_code_urls(self):
        result = self.audit("See https://unread.example/result and `https://code.example/ignored`.")
        self.assertEqual(result["issue_counts"], {"unbound_text": 1, "unread_url": 1})

    def test_code_markers_cannot_supply_evidence_for_prose(self):
        card = self.card()
        result = self.audit(f"An unsupported claim with example `[{card['id']}]` and ``[EV-FAKE] https://code.example/x``.")
        self.assertEqual(result["bound_candidates"], 0)
        self.assertEqual(result["issue_counts"], {"unbound_text": 1})
        self.assertEqual(result["bindings"][0]["mentioned_urls"], [])

    def test_escaped_markers_and_entities_do_not_become_live_evidence(self):
        card = self.card()
        for marker in (f"\\[{card['id']}\\]", f"&#91;{card['id']}&#93;", f"`` [{card['id']}] ``"):
            with self.subTest(marker=marker):
                result = self.audit(f"Unsupported text shows {marker} as an example.")
                self.assertEqual(result["bound_candidates"], 0)
                self.assertEqual(result["issue_counts"], {"unbound_text": 1})

    def test_nested_or_mismatched_fences_do_not_hide_real_claims(self):
        card = self.card()
        for opening, inner, closing in (("````md", "```python", "````"), ("~~~md", "```", "~~~")):
            with self.subTest(opening=opening):
                result = self.audit(f"{opening}\n{inner}\nExample [EV-FAKE]\n{closing}\n12 samples [{card['id']}].\n")
                self.assertEqual(result["total_candidates"], 1)
                self.assertEqual(result["issue_counts"], {})
                self.assertEqual((result["bindings"][0]["line"], result["bindings"][0]["end_line"]), (5, 5))

    def test_indented_code_and_unused_reference_definitions_are_not_claims(self):
        card = self.card()
        result = self.audit(f"    Code [EV-FAKE] https://code.example/x\n\n[unused]: https://unused.example/x\n\n12 samples [{card['id']}].\n")
        self.assertEqual(result["total_candidates"], 1)
        self.assertEqual(result["issue_counts"], {})

    def test_full_collapsed_and_shortcut_references_expose_unread_sources(self):
        card = self.card()
        result = self.audit(
            f"First finding [{card['id']}] [source][TrIaL Label].\n\n"
            f"Second finding [{card['id']}] [paper][].\n\n"
            f"Third finding [{card['id']}] [short].\n\n"
            "[trial label]: <https://unread.example/full> \"Trial\"\n"
            "[paper]: https://unread.example/collapsed\n"
            "[short]: https://unread.example/shortcut\n")
        self.assertEqual(result["total_candidates"], 3)
        self.assertEqual(result["bound_candidates"], 3)
        self.assertEqual(result["issue_counts"], {"unread_url": 3})
        self.assertEqual([item["mentioned_urls"][0] for item in result["bindings"]],
                         [f"https://unread.example/{kind}" for kind in ("full", "collapsed", "shortcut")])

    def test_reference_links_keep_snapshot_source_and_url_mismatch_checks(self):
        url = "https://read.example/trial"
        card = self.card(url=url)
        ws._write_json(ws.root().parent / ".deep-research-citations.json", [url, "https://other.example/trial"])
        good = self.audit(f"12 samples. [{card['id']}] [source][trial]\n\n[trial]: {url}\n")
        self.assertEqual(good["total_candidates"], 1)
        self.assertEqual(good["issue_counts"], {})
        mismatch = self.audit(f"12 samples [{card['id']}] [source][trial]\n\n[trial]: https://other.example/trial\n")
        self.assertEqual(mismatch["issue_counts"], {"url_evidence_mismatch": 1})

    def test_inline_urls_with_balanced_parentheses_and_unicode_match_read_source(self):
        for url in ("https://read.example/trial_(pilot)", "https://read.example/研究?x=1&y=2"):
            with self.subTest(url=url):
                card = self.card(url=url)
                ws._write_json(ws.root().parent / ".deep-research-citations.json", [url])
                result = self.audit(f"12 samples [{card['id']}] [source]({url}).")
                self.assertEqual(result["issue_counts"], {})
                self.assertEqual(result["bindings"][0]["mentioned_urls"], [reports.MARKDOWN.normalizeLink(url)])

    def test_wrapped_claim_and_trailing_reference_remain_one_binding(self):
        url = "https://read.example/trial"
        card = self.card(url=url)
        ws._write_json(ws.root().parent / ".deep-research-citations.json", [url])
        result = self.audit(f"The trial used\n12 samples.\n[{card['id']}] [source][trial]\n\n[trial]: {url}\n")
        self.assertEqual(result["total_candidates"], 1)
        self.assertEqual(result["issue_counts"], {})
        self.assertEqual((result["bindings"][0]["line"], result["bindings"][0]["end_line"]), (1, 3))

    def test_table_rows_keep_cells_and_citations_together_without_header_candidates(self):
        card = self.card()
        result = self.audit(f"| Group | Samples | Evidence |\n| --- | --- | --- |\n| Trial | 12 | [{card['id']}] |\n| Draft | Unknown | `[EV-FAKE]` |\n")
        self.assertEqual(result["total_candidates"], 2)
        self.assertEqual(result["bound_candidates"], 1)
        self.assertEqual(result["issue_counts"], {"unbound_text": 1})
        self.assertEqual(result["bindings"][0]["sentence"], f"Trial | 12 | [{card['id']}]")
        self.assertEqual((result["bindings"][0]["line"], result["bindings"][0]["end_line"]), (3, 3))

    def test_fenced_reference_definitions_do_not_admit_a_url(self):
        card = self.card()
        result = self.audit(f"12 samples [{card['id']}] [source][trial].\n\n```md\n[trial]: https://code.example/ignored\n```\n")
        self.assertEqual(result["total_candidates"], 1)
        self.assertEqual(result["issue_counts"], {})
        self.assertEqual(result["bindings"][0]["mentioned_urls"], [])

    def test_audit_bounds_preview_without_truncating_saved_sentences(self):
        text = "长句" * 20_000
        result = self.audit(text)
        self.assertTrue(result["bindings"][0]["preview_truncated"])
        self.assertLess(len(json.dumps(result, ensure_ascii=False).encode()), 12_000)
        saved = json.loads(Path(result["audit_path"]).read_text())
        self.assertEqual(saved["bindings"][0]["sentence"], text)

    def test_audit_persists_complete_results_while_cli_response_is_paginated(self):
        result = self.audit("First sentence. Second sentence. Third sentence.", limit=1)
        self.assertEqual(len(result["bindings"]), 1)
        self.assertEqual(result["next_offset"], 1)
        saved = json.loads(Path(result["audit_path"]).read_text())
        self.assertEqual(len(saved["bindings"]), 3)

    def test_cli_section_synthesis_packet_and_audit(self):
        def cli(*args):
            result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[:1500])
            return json.loads(result.stdout)

        cli("section", "results", "--title", "Results", "--need", "N1")
        card = self.card()
        cli("synthesis", "results", "--summary", "The size is 12.", "--evidence", card["id"])
        packet = cli("packet", "results")
        self.assertEqual(packet["total_evidence"], 1)
        self.assertTrue(Path(packet["packet_path"]).exists())
        self.assertLessEqual(len(json.dumps(packet, ensure_ascii=False).encode()) + 1, packet["response_byte_limit"])
        self.assertEqual(cli("outline")["unassigned_needs"], ["N2", "N3"])
        path = self.folder / "report.md"
        path.write_text(f"Size is\n12.\n[{card['id']}]", encoding="utf-8")
        self.assertFalse(cli("audit", str(path))["structural_issues_found"])


if __name__ == "__main__":
    unittest.main()
