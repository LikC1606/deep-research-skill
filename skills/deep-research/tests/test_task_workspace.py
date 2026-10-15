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
SPEC = importlib.util.spec_from_file_location("test_task_workspace", SCRIPT.with_name("workspace.py"))
workspace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workspace)


class TaskWorkspaceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="deep-research-task-tests-")
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        env = mock.patch.dict(os.environ, {
            "DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
            "DEEP_RESEARCH_SOURCE_POLICY": "local_only",
            "DRBENCH_MAX_READ": "0", "DEEP_RESEARCH_MAX_READ": "0",
        })
        env.start()
        self.addCleanup(env.stop)

    def init(self):
        return workspace.init_task("Compare the methods", "Methods A and B", ["Keep all user fields"])

    def add(self, need="N1", **kwargs):
        return workspace.set_need(need, question=f"Required field {need}", proof="Reported result", **kwargs)

    def evidence(self, need="N1", relation="supports"):
        ref = "REF-0123456789ABCDEF"
        workspace.save_source(ref, {"url": "local fixture", "content_scope": "local_file"},
                              "Results\nThe trial used 12 samples.")
        return workspace.record_evidence(ref, need, "The trial size", "12 samples", relation)

    def answer(self, need="N1"):
        card = self.evidence(need)
        return workspace.set_need(need, state="answered", note="The reported size is 12 samples.",
                                  evidence_ids=[card["id"]])

    def test_matching_init_and_need_registration_preserve_progress(self):
        self.init()
        self.add()
        self.answer()
        workspace.record_failure("N1", "Read supplementary data", "No public supplement")
        before = (workspace.root() / "task.json").read_bytes()
        self.init()
        self.assertEqual(self.add()["state"], "answered")
        self.assertEqual((workspace.root() / "task.json").read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "contract differs"):
            workspace.init_task("A different task", "Methods A and B")
        with self.assertRaisesRegex(ValueError, "contract differs"):
            workspace.set_need("N1", question="Replace a required field")
        self.assertEqual((workspace.root() / "task.json").read_bytes(), before)

    def test_answering_one_need_does_not_finish_other_checklist_items(self):
        self.init()
        self.add("N1")
        self.add("N2", priority=1)
        self.add("N3", priority=2)
        self.answer("N1")
        workspace.set_need("N2", state="blocked", note="Official result is inaccessible")
        workspace.set_need("N3", state="partial", note="Have method details; lack measured result")
        summary = workspace.status()
        self.assertEqual(summary["remaining_needs"], ["N2", "N3"])
        self.assertEqual(summary["next_need"], "N3")
        self.assertEqual(summary["task"]["state"], "active")
        self.assertFalse(summary["completion_ready"])
        before = (workspace.root() / "task.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "every registered checklist"):
            workspace.stop_task("One branch is done", complete=True)
        self.assertEqual((workspace.root() / "task.json").read_bytes(), before)

    def test_evidence_does_not_automatically_close_a_need(self):
        self.init()
        self.add()
        self.evidence()
        summary = workspace.status()
        self.assertEqual(summary["evidence_count"], 1)
        self.assertEqual(summary["task"]["needs"]["N1"]["state"], "open")
        self.assertFalse(summary["completion_ready"])

    def test_new_counterevidence_requires_explicit_review_before_completion(self):
        self.init()
        self.add()
        answered = self.answer()
        self.evidence()  # Re-recording the same immutable card is not a change.
        self.assertTrue(workspace.status()["completion_ready"])
        refute = self.evidence(relation="refutes")
        before = (workspace.root() / "task.json").read_bytes()
        summary = workspace.status()
        self.assertEqual(summary["task"]["needs"]["N1"]["state"], "answered")
        self.assertEqual(summary["remaining_needs"], ["N1"])
        self.assertEqual(summary["next_need"], "N1")
        self.assertEqual(summary["needs_requiring_review"], ["N1"])
        details = summary["need_review_details"]["N1"]
        self.assertFalse(details["review_basis_unknown"])
        self.assertEqual(details["new_evidence_count"], 1)
        self.assertEqual(details["new_evidence_ids"], [refute["id"]])
        self.assertFalse(summary["completion_ready"])
        with self.assertRaisesRegex(ValueError, "reviewing changed or untracked evidence"):
            workspace.stop_task("All fields answered", complete=True)
        self.assertEqual((workspace.root() / "task.json").read_bytes(), before)
        for update in ({"priority": 1}, {"note": "A note update alone"}, {}):
            workspace.set_need("N1", **update)
            self.assertEqual(workspace.status()["needs_requiring_review"], ["N1"])
        confirmed = workspace.set_need("N1", state="answered", note="Reviewed the new material and its scope")
        self.assertEqual(confirmed["evidence_ids"], answered["evidence_ids"])
        self.assertEqual(set(confirmed["reviewed_evidence_ids"]),
                         {*answered["evidence_ids"], refute["id"]})
        self.assertEqual(workspace.status()["needs_requiring_review"], [])
        self.assertTrue(workspace.status()["completion_ready"])
        before = (workspace.root() / "task.json").read_bytes()
        workspace.set_need("N1", state="answered", note=confirmed["note"])
        self.assertEqual((workspace.root() / "task.json").read_bytes(), before)
        self.assertEqual(workspace.stop_task("Evidence reviewed", complete=True)["state"], "complete")

    def test_review_basis_includes_unselected_cards_and_ignores_unrelated_activity(self):
        self.init()
        self.add()
        self.add("N2")
        context = self.evidence(relation="context")
        answered = self.answer()
        self.assertIn(context["id"], answered["reviewed_evidence_ids"])
        self.assertNotIn(context["id"], answered["evidence_ids"])
        self.evidence("N2")
        workspace.record_failure("N1", "Read supplement", "Unavailable")
        workspace.record_search_attempt("N1", "trial size", "fixture-key", cache_hit=True)
        ref = "REF-0123456789ABCDEF"
        workspace.save_source(ref, {"url": "local fixture", "content_scope": "local_file"},
                              "Updated source: 24 samples.")
        self.assertEqual(workspace.status()["needs_requiring_review"], [])
        for relation in ("supports", "context"):
            with self.subTest(relation=relation):
                card = workspace.record_evidence(ref, "N1", "Updated trial detail", "24 samples", relation)
                self.assertEqual(workspace.status()["need_review_details"]["N1"]["new_evidence_ids"],
                                 [card["id"]])
                workspace.set_need("N1", state="answered", note="Checked the updated evidence scope")
                self.assertEqual(workspace.status()["needs_requiring_review"], [])

    def test_legacy_answer_without_review_basis_needs_one_explicit_confirmation(self):
        self.init()
        self.add()
        self.answer()
        path = workspace.root() / "task.json"
        task = json.loads(path.read_text())
        del task["needs"]["N1"]["reviewed_evidence_ids"]
        path.write_text(json.dumps(task), encoding="utf-8")
        before = path.read_bytes()
        summary = workspace.status()
        self.assertEqual(summary["needs_requiring_review"], ["N1"])
        details = summary["need_review_details"]["N1"]
        self.assertTrue(details["review_basis_unknown"])
        self.assertIsNone(details["new_evidence_count"])
        self.assertIsNone(details["removed_evidence_count"])
        self.assertEqual(details["new_evidence_ids"], [])
        with self.assertRaisesRegex(ValueError, "untracked evidence"):
            workspace.stop_task("Legacy answer", complete=True)
        self.assertEqual(path.read_bytes(), before)
        workspace.set_need("N1", state="answered", note="Reviewed the saved legacy evidence")
        self.assertTrue(workspace.status()["completion_ready"])

    def test_review_change_previews_are_bounded_but_counts_include_removed_cards(self):
        self.init()
        self.add()
        support = self.evidence()
        ref = "REF-0123456789ABCDEF"
        removed = [workspace.record_evidence(ref, "N1", f"Old context {i}", "12 samples", "context")
                   for i in range(12)]
        workspace.set_need("N1", state="answered", note="Checked all existing cards",
                           evidence_ids=[support["id"]])
        path = workspace.root() / "evidence.json"
        cards = json.loads(path.read_text())
        for card in removed:
            del cards[card["id"]]
        path.write_text(json.dumps(cards), encoding="utf-8")
        added = [workspace.record_evidence(ref, "N1", f"New context {i}", "12 samples", "context")
                 for i in range(12)]
        details = workspace.status()["need_review_details"]["N1"]
        for label, records in (("new", added), ("removed", removed)):
            self.assertEqual(details[f"{label}_evidence_count"], 12)
            self.assertEqual(details[f"{label}_evidence_ids"], sorted(card["id"] for card in records)[:10])
            self.assertTrue(details[f"{label}_evidence_ids_truncated"])

    def test_late_evidence_after_completion_survives_reload_and_resume(self):
        self.init()
        self.add()
        self.answer()
        stopped = workspace.stop_task("Evidence reviewed", complete=True)
        self.evidence(relation="refutes")
        fresh = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(fresh)
        before = (fresh.root() / "task.json").read_bytes()
        summary = fresh.status()
        self.assertEqual(summary["task"]["state"], "complete")
        self.assertEqual(summary["needs_requiring_review"], ["N1"])
        self.assertFalse(summary["completion_ready"])
        self.assertEqual((fresh.root() / "task.json").read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "use resume"):
            fresh.set_need("N1", state="answered", note="Reviewed late material")
        self.assertEqual(fresh.resume_task()["last_stop"], stopped["last_stop"])
        self.assertEqual(fresh.status()["next_need"], "N1")
        fresh.set_need("N1", state="partial", note="The conflicting scope still needs checking")
        self.assertEqual(fresh.status()["needs_requiring_review"], [])
        self.assertEqual(fresh.status()["remaining_needs"], ["N1"])
        self.assertEqual(fresh.stop_task("Need more evidence")["state"], "incomplete")

    def test_answer_requires_conclusion_and_evidence_for_the_same_need(self):
        self.init()
        self.add("N1")
        self.add("N2")
        other = self.evidence("N2")
        context = self.evidence("N1", "context")
        cases = [
            ({"state": "answered"}, "requires a note"),
            ({"state": "answered", "note": "Done"}, "supports/refutes evidence"),
            ({"state": "answered", "note": "Done", "evidence_ids": ["missing"]}, "this Need"),
            ({"state": "answered", "note": "Done", "evidence_ids": [other["id"]]}, "this Need"),
            ({"state": "answered", "note": "Done", "evidence_ids": [context["id"]]},
             "supports/refutes evidence"),
        ]
        for arguments, error in cases:
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, error):
                workspace.set_need("N1", **arguments)
        self.assertEqual(workspace.status()["task"]["needs"]["N1"]["state"], "open")
        self.assertEqual(self.answer()["state"], "answered")

    def test_failure_history_and_gaps_survive_stop_and_module_reload(self):
        self.init()
        self.add()
        for index in range(12):
            workspace.record_failure("N1", f"query {index}", "No useful result")
        workspace.set_need("N1", state="blocked", note="No public measurement")
        workspace.stop_task("Read budget exhausted")
        fresh = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(fresh)
        summary = fresh.status()
        self.assertEqual(summary["task"]["state"], "incomplete")
        self.assertEqual(summary["task"]["last_stop"]["reason"], "Read budget exhausted")
        self.assertEqual(summary["task"]["failure_count"], 12)
        self.assertEqual(len(summary["task"]["recent_failures"]), 10)
        self.assertEqual(summary["remaining_needs"], ["N1"])
        self.assertIsNone(summary["next_need"])
        with self.assertRaisesRegex(ValueError, "use resume"):
            fresh.set_need("N1", state="open", note="Try the new source")
        resumed = fresh.resume_task()
        self.assertEqual(len(resumed["failures"]), 12)
        self.assertEqual(resumed["last_stop"], summary["task"]["last_stop"])
        fresh.set_need("N1", state="open", note="Try the new source")
        self.assertEqual(fresh.status()["next_need"], "N1")

    def test_completion_requires_nonempty_checklist_and_explicit_stop(self):
        self.init()
        self.assertFalse(workspace.status()["completion_ready"])
        with self.assertRaisesRegex(ValueError, "every registered checklist"):
            workspace.stop_task("No questions registered", complete=True)
        self.add("N1")
        self.add("N2")
        self.answer("N1")
        self.answer("N2")
        self.assertTrue(workspace.status()["completion_ready"])
        self.assertEqual(workspace.status()["task"]["state"], "active")
        self.assertEqual(workspace.stop_task("Checklist and conflicts reviewed", complete=True)["state"],
                         "complete")
        with self.assertRaisesRegex(ValueError, "use resume"):
            self.add("N3")
        workspace.resume_task()
        self.add("N3")
        self.assertFalse(workspace.status()["completion_ready"])

    def test_another_run_cannot_reuse_task_progress(self):
        self.init()
        self.add()
        self.answer()
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_RUN_DIR": str(self.folder / "other")}):
            self.assertIsNone(workspace.status()["task"])
            with self.assertRaisesRegex(ValueError, "init first"):
                workspace.resume_task()
            self.init()
            self.assertEqual(workspace.status()["task"]["needs"], {})

    def test_initialized_task_rejects_evidence_for_unregistered_need(self):
        self.init()
        with self.assertRaisesRegex(ValueError, "register the Need"):
            self.evidence("typo")
        self.assertEqual(workspace.status()["evidence_count"], 0)

    def test_init_preserves_evidence_from_uninitialized_workflow(self):
        card = self.evidence("N1")
        self.init()
        self.assertEqual(workspace.status()["unregistered_evidence_needs"], ["N1"])
        self.add()
        workspace.set_need("N1", state="answered", note="Previously read result",
                           evidence_ids=[card["id"]])
        self.assertEqual(workspace.status()["unregistered_evidence_needs"], [])

    def test_cli_resumes_only_unanswered_items_and_completes_explicitly(self):
        def cli(*arguments, success=True):
            result = subprocess.run([sys.executable, str(SCRIPT), *arguments],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0 if success else 1,
                             (result.stdout + result.stderr)[:2000])
            return json.loads(result.stdout)

        cli("init", "--deliverable", "Report", "--target", "Local fixture")
        for need in ("N1", "N2"):
            cli("need", need, "--question", f"Field {need}", "--proof", "A reported size")
        path = self.folder / "source.txt"
        path.write_text("The trial used 12 samples.", encoding="utf-8")
        source = cli("read", str(path))
        for need in ("N1", "N2"):
            if need == "N2":
                cli("failure", "--need", need, "--action", "Read missing supplement",
                    "--reason", "File unavailable")
                self.assertIn("error", cli("stop", "--complete", "--reason", "Premature", success=False))
                cli("stop", "--reason", "Continue later")
                resumed = cli("resume")
                self.assertEqual(resumed["remaining_needs"], ["N2"])
                self.assertEqual(resumed["task"]["failure_count"], 1)
            card = cli("evidence", source["ref"], "--need", need, "--claim", "Trial size",
                       "--quote", "12 samples", "--relation", "supports")
            cli("need", need, "--state", "answered", "--note", "12 samples reported",
                "--evidence", card["id"])
        late = cli("evidence", source["ref"], "--need", "N1", "--claim", "Trial size",
                   "--quote", "12 samples", "--relation", "context")
        self.assertIn("reviewing changed", cli("stop", "--complete", "--reason", "Premature",
                                              success=False)["error"])
        summary = cli("status")
        self.assertEqual(summary["remaining_needs"], ["N1"])
        self.assertEqual(summary["need_review_details"]["N1"]["new_evidence_ids"], [late["id"]])
        cli("need", "N1", "--state", "answered", "--note", "Reviewed the additional context")
        self.assertEqual(cli("stop", "--complete", "--reason", "All fields reviewed")["state"], "complete")
        self.assertEqual(cli("status")["remaining_needs"], [])


if __name__ == "__main__":
    unittest.main()
