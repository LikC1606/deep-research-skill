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


PATH = Path(__file__).resolve().parents[1] / "scripts" / "research.py"
SPEC = importlib.util.spec_from_file_location("test_source_workspace_research", PATH)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)
workspace = research.workspace


def document(url, text, **metadata):
    return {
        "url": url, "title": "Fixture", "_body": text,
        "content_scope": "full_text", "content_type": "text", **metadata,
    }


class SourceWorkspaceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="deep-research-tests-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = mock.patch.dict(os.environ, {
            "DRBENCH_RUN_DIR": "", "DEEP_RESEARCH_RUN_DIR": folder.name,
            "DEEP_RESEARCH_SOURCE_POLICY": "web_allowed",
            "DEEP_RESEARCH_MAX_READ": "0", "DRBENCH_MAX_READ": "0",
        })
        env.start()
        self.addCleanup(env.stop)

    def read_network(self, text="Heading\nThe trial used 12 samples.\nLimits apply.", **metadata):
        url = "https://example.org/article"
        with mock.patch.object(research.v1, "read_page", return_value=document(url, text, **metadata)):
            return research._ref_read(url, ["samples"], 100)

    def test_local_text_has_stable_lines_and_no_web_citation(self):
        path = self.root / "notes.md"
        path.write_text("# Results\nThe run used 12 samples.\nLimit: one site.\n")
        result = research._ref_read(str(path), ["samples"], 100)
        self.assertEqual(result["content_scope"], "local_file")
        self.assertEqual(result["citation_path"], str(path))
        self.assertNotIn("citation_url", result)
        self.assertNotIn("_body", result)
        opened = workspace.open_source(result["ref"], 2, 1)
        self.assertEqual(opened["text"], "The run used 12 samples.")
        self.assertEqual(opened["locator"]["start_line"], 2)

    def test_saved_web_source_can_be_requeried_offline(self):
        first = self.read_network()
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}), \
                mock.patch.object(research.v1, "read_page") as network:
            again = research._ref_read(first["ref"], ["Limits"], 100)
            network.assert_not_called()
        self.assertTrue(again["cache_hit"])
        self.assertEqual(again["content_hash"], first["content_hash"])
        self.assertIn("Limits apply", again["windows"][0])

    def test_local_only_blocks_uncached_reads_for_every_public_route(self):
        urls = [
            "https://example.org/article", "https://github.com/owner/project",
            "https://www.kaggle.com/competitions/example",
            "https://www.kaggle.com/competitions/example/discussion/123",
        ]
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}), \
                mock.patch.object(research.v1, "read_page") as generic, \
                mock.patch.object(research, "_kaggle_json") as kaggle, \
                mock.patch.object(research, "_read_github_readme") as github:
            for url in urls:
                with self.subTest(url=url), self.assertRaisesRegex(RuntimeError, "local_only"):
                    research.read_page(url, [])
            generic.assert_not_called()
            kaggle.assert_not_called()
            github.assert_not_called()

    def test_reader_adapter_also_enforces_local_only(self):
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}), \
                mock.patch.object(research.v1, "_fetch_bytes") as network:
            with self.assertRaisesRegex(RuntimeError, "local_only"):
                research.v1.read_page("https://example.org/article", [])
            network.assert_not_called()

    def test_local_only_allows_local_files_but_rejects_web_refresh(self):
        saved = self.read_network()
        path = self.root / "local.md"
        path.write_text("Locally available evidence.")
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}):
            local = research._ref_read(str(path), [], 100)
            self.assertEqual(local["content_scope"], "local_file")
            with self.assertRaisesRegex(RuntimeError, "local_only"):
                research._ref_read(saved["ref"], [], 100, refresh=True)

    def test_failed_read_does_not_admit_a_citation(self):
        with mock.patch.object(research.v1, "read_page", side_effect=RuntimeError("empty shell")):
            with self.assertRaisesRegex(RuntimeError, "empty shell"):
                research._ref_read("https://example.org/error", [], 100)
        self.assertEqual(research._load_citations(), [])

    def test_html_body_is_saved_once_without_navigation(self):
        url = "https://example.org/study"
        html = b"<title>Study</title><nav>Unrelated navigation</nav><main><h1>Results</h1><p>The trial used 12 samples.</p></main>"
        with mock.patch.object(research.v1, "_fetch_bytes", return_value=(html, url, "text/html")) as fetch:
            first = research._ref_read(url, ["samples"], 100)
            second = research._ref_read(first["ref"], ["Results"], 100)
        fetch.assert_called_once()
        self.assertEqual(first["content_scope"], "full_text")
        self.assertTrue(second["cache_hit"])
        opened = workspace.open_source(first["ref"])
        self.assertNotIn("Unrelated navigation", opened["text"])
        record = workspace.record_evidence(first["ref"], "N1", "Trial size", "12 samples", "supports")
        self.assertEqual(record["locator"]["start_line"], 2)

    def test_html_links_are_findable_without_reading_their_targets(self):
        url = "https://example.org/study"
        repository = "https://github.com/author/project"
        html = (
            b'<title>Study</title><nav><a href="https://example.org/menu">Menu</a></nav>'
            b'<main><h1>Implementation</h1><a href="https://github.com/author/project">'
            b'<strong>Code</strong></a></main>'
        )
        with mock.patch.object(research.v1, "_fetch_bytes", return_value=(html, url, "text/html")) as fetch:
            result = research._ref_read(url, [], 100)
            found = workspace.excerpts(*workspace.require_source(result["ref"]),
                                       ["github.com"], intro_if_missing=False)
        fetch.assert_called_once_with(url)
        self.assertIn(f"Code ({repository})", found["windows"][0])
        self.assertNotIn("https://example.org/menu", workspace.open_source(result["ref"])["text"])
        self.assertEqual(result["allowed_citation_urls"], [url])
        self.assertEqual(workspace.status()["sources_read"], 1)

    def test_html_relative_links_use_fetched_url_and_document_base(self):
        url = "https://example.org/study"
        fetched_url = "https://authors.example/projects/method/paper.html"
        cases = [
            (b"", "https://authors.example/projects/code/"),
            (b'<base href="/releases/v2/paper/">', "https://authors.example/releases/v2/code/"),
        ]
        for base_tag, expected in cases:
            with self.subTest(base=base_tag):
                html = (
                    b"<head><title>Study</title>" + base_tag + b"</head>"
                    b'<main><a href="../code/">Implementation</a>'
                    b'<a href="//github.com/author/project">GitHub</a></main>'
                )
                with mock.patch.object(research.v1, "_fetch_bytes", return_value=(
                    html, fetched_url, "text/html"
                )) as fetch:
                    result = research._ref_read(url, [], 100, refresh=True)
                fetch.assert_called_once_with(url)
                text = workspace.open_source(result["ref"])["text"]
                self.assertIn(f"Implementation ({expected})", text)
                self.assertIn("GitHub (https://github.com/author/project)", text)

    def test_html_non_web_and_invalid_links_keep_only_their_labels(self):
        url = "https://example.org/study"
        html = (
            b'<main><a href="#methods">Methods</a><a href="">Empty</a>'
            b'<a href="mailto:author@example.org">Email</a>'
            b'<a href="javascript:void(0)">Script</a><a href="file:///tmp/code">File</a>'
            b'<a href="data:text/plain,code">Data</a>'
            b'<a href="https://[broken">Broken</a></main>'
        )
        with mock.patch.object(research.v1, "_fetch_bytes", return_value=(html, url, "text/html")):
            result = research._ref_read(url, [], 100)
        self.assertEqual(workspace.open_source(result["ref"])["text"].splitlines(),
                         ["Methods", "Empty", "Email", "Script", "File", "Data", "Broken"])

    def test_local_html_preserves_absolute_links_without_guessing_relative_urls(self):
        path = self.root / "study.html"
        path.write_text(
            '<main><a href="https://github.com/author/project">Code</a>'
            '<a href="../paper.pdf">Paper</a></main>'
        )
        with mock.patch.object(research.v1, "_fetch_bytes") as fetch:
            result = research._ref_read(str(path), [], 100)
        fetch.assert_not_called()
        text = workspace.open_source(result["ref"])["text"]
        self.assertIn("Code (https://github.com/author/project)", text)
        self.assertEqual(text.splitlines()[-1], "Paper")
        self.assertEqual(result["content_scope"], "local_file")
        self.assertEqual(result["allowed_citation_urls"], [])

    def test_github_file_reads_raw_text_before_html(self):
        url = "https://github.com/example/project/blob/main/README.md"
        raw_url = "https://raw.githubusercontent.com/example/project/main/README.md"
        with mock.patch.object(research.v1, "_fetch_bytes", return_value=(
            b"# Methods\nThe code uses <tag> as a literal.", raw_url, "text/plain"
        )) as fetch:
            result = research._ref_read(url, [], 100)
        fetch.assert_called_once_with(raw_url)
        self.assertEqual(result["fallback"], "github_raw")
        self.assertIn("<tag>", workspace.open_source(result["ref"])["text"])

    def test_error_shell_never_becomes_a_saved_source(self):
        url = "https://example.org/missing"
        with mock.patch.object(research.v1, "_fetch_bytes", return_value=(
            b"<title>404 Not Found</title><main>Page not found</main>", url, "text/html"
        )):
            with self.assertRaisesRegex(RuntimeError, "unreadable content shell"):
                research._ref_read(url, [], 100)
        self.assertEqual(workspace.status()["sources_read"], 0)
        self.assertEqual(research._load_citations(), [])

    def test_abstract_recovery_is_labeled_by_reader(self):
        with mock.patch.object(research.v1, "_openalex_record", return_value=(
            "Paper abstract", "An abstract about a small trial.", "https://openalex.org/W12345"
        )), mock.patch.object(research.v1, "_fetch_bytes") as fetch:
            result = research._ref_read("https://openalex.org/W12345", [], 100)
        fetch.assert_not_called()
        self.assertEqual(result["content_scope"], "abstract")
        self.assertEqual(result["content_type"], "openalex_abstract")

    def test_oversized_download_is_rejected_and_closed(self):
        response = mock.Mock(url="https://example.org/large")
        response.iter_content.return_value = iter([b"123456", b"789"])
        response.headers = {"Content-Type": "text/plain"}
        with mock.patch.object(research.v1.base, "_url_ok", return_value=True), \
                mock.patch.object(research.v1.requests, "get", return_value=response), \
                mock.patch.object(research.v1, "MAX_DOWNLOAD_BYTES", 8):
            with self.assertRaisesRegex(ValueError, "download size limit"):
                research.v1._fetch_bytes(response.url)
        response.close.assert_called_once()

    def test_abstract_scope_is_retained_in_evidence(self):
        result = self.read_network("Abstract: a small observational trial.",
                                   content_scope="abstract", content_type="openalex_abstract")
        record = workspace.record_evidence(result["ref"], "N1", "Small trial",
                                           "small observational trial", "supports")
        self.assertEqual(record["content_scope"], "abstract")
        self.assertTrue(record["quote_verified"])
        self.assertEqual(record["relation_assessed_by"], "caller")
        self.assertNotIn("claim_verified", record)

    def test_evidence_rejects_fabricated_quote(self):
        result = self.read_network()
        with self.assertRaisesRegex(ValueError, "not present verbatim"):
            workspace.record_evidence(result["ref"], "N1", "Large trial", "1200 samples", "supports")
        self.assertEqual(workspace.status()["evidence_count"], 0)

    def test_refresh_preserves_the_snapshot_used_by_existing_evidence(self):
        first = self.read_network("Results\n12 samples.")
        record = workspace.record_evidence(first["ref"], "N1", "Trial size", "12 samples", "supports")
        with mock.patch.object(research.v1, "read_page", return_value=document(
            "https://example.org/article", "Updated results\n24 samples."
        )):
            latest = research._ref_read(first["ref"], [], 100, refresh=True)
        self.assertNotEqual(first["content_hash"], latest["content_hash"])
        old = workspace.open_source(first["ref"], version=record["content_hash"])
        self.assertIn("12 samples", old["text"])
        self.assertIn("24 samples", workspace.open_source(first["ref"])["text"])

    def test_identical_text_refresh_preserves_historical_scope_pages_and_content_alias(self):
        text = "Results\n12 samples."
        first = self.read_network(text, content_scope="abstract", truncated=True,
                                  pages=[{"page": 1, "start_char": 0, "end_char": len(text)}])
        card = workspace.record_evidence(first["ref"], "N1", "Trial size", "12 samples", "supports")
        folder = Path(first["body_path"]).parent
        before = {version: (folder / f"{version}.json").read_bytes()
                  for version in (first["snapshot_id"], first["content_hash"])}
        with mock.patch.object(research.v1, "read_page", return_value=document(
            first["url"], text, pages=[{"page": 7, "start_char": 0, "end_char": len(text)}]
        )):
            latest = research._ref_read(first["ref"], [], 100, refresh=True)
        self.assertEqual(first["content_hash"], latest["content_hash"])
        self.assertNotEqual(first["snapshot_id"], latest["snapshot_id"])
        for version, saved in before.items():
            self.assertEqual((folder / f"{version}.json").read_bytes(), saved)
            old = workspace.open_source(first["ref"], version=version)
            self.assertEqual(old["content_scope"], "abstract")
            self.assertTrue(old["truncated"])
            self.assertEqual(old["locator"]["pages"], [1])
        current = workspace.open_source(first["ref"], version=latest["snapshot_id"])
        self.assertEqual(current["content_scope"], "full_text")
        self.assertFalse(current["truncated"])
        self.assertEqual(current["locator"]["pages"], [7])
        repeated = workspace.record_evidence(first["ref"], "N1", "Trial size", "12 samples", "supports",
                                             version=card["snapshot_id"])
        self.assertEqual(repeated["id"], card["id"])
        new = workspace.record_evidence(first["ref"], "N1", "Trial size", "12 samples", "supports")
        self.assertEqual(new["snapshot_id"], latest["snapshot_id"])
        self.assertNotEqual(new["id"], card["id"])
        self.assertEqual(workspace.review_evidence()["distinct_content_count"], 1)

    def test_identical_refresh_keeps_snapshot_and_evidence_identity(self):
        text = "Results\n12 samples."
        first = self.read_network(text)
        card = workspace.record_evidence(first["ref"], "N1", "Trial size", "12 samples", "supports")
        snapshot = Path(first["body_path"]).with_name(f"{first['snapshot_id']}.json")
        before = snapshot.read_bytes()
        with mock.patch.object(research.v1, "read_page", return_value=document(first["url"], text)):
            latest = research._ref_read(first["ref"], [], 100, refresh=True)
        self.assertEqual(latest["snapshot_id"], first["snapshot_id"])
        self.assertEqual(snapshot.read_bytes(), before)
        self.assertEqual(workspace.record_evidence(first["ref"], "N1", "Trial size", "12 samples",
                                                    "supports")["id"], card["id"])

    def test_legacy_snapshots_remain_readable_and_gain_precise_versions_on_refresh(self):
        first = self.read_network()
        folder = Path(first["body_path"]).parent
        legacy = {key: value for key, value in first.items()
                  if key not in {"snapshot_id", "windows", "locators", "cache_hit", "citation_url",
                                 "allowed_citation_urls", "citation_note", "discovery"}}
        for name in ("metadata.json", f"{first['content_hash']}.json"):
            (folder / name).write_text(json.dumps(legacy), encoding="utf-8")
        (folder / f"{first['snapshot_id']}.json").unlink()
        old = workspace.open_source(first["ref"], version=first["content_hash"])
        self.assertNotIn("snapshot_id", old)
        card = workspace.record_evidence(first["ref"], "N1", "Trial size", "12 samples", "supports")
        self.assertNotIn("snapshot_id", card)
        metadata, text = workspace.require_source(first["ref"])
        refreshed, _ = workspace.save_source(first["ref"], {**metadata, "content_scope": "abstract"}, text)
        self.assertEqual(workspace.open_source(first["ref"], version=first["content_hash"])["content_scope"],
                         "full_text")
        self.assertEqual(workspace.open_source(first["ref"], version=refreshed["snapshot_id"])["content_scope"],
                         "abstract")

    def test_metadata_corruption_and_mismatched_versions_are_rejected(self):
        first = self.read_network()
        folder = Path(first["body_path"]).parent
        path = folder / f"{first['snapshot_id']}.json"
        metadata = json.loads(path.read_text())
        metadata["content_scope"] = "abstract"
        path.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "metadata changed"):
            workspace.open_source(first["ref"], version=first["snapshot_id"])
        wrong_version = "0" * 64
        (folder / f"{wrong_version}.json").write_bytes((folder / "metadata.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "does not match"):
            workspace.open_source(first["ref"], version=wrong_version)

    def test_cli_open_find_and_evidence_use_snapshot_versions_offline(self):
        text = "Results\n12 samples."
        first = self.read_network(text, content_scope="abstract", truncated=True,
                                  pages=[{"page": 1, "start_char": 0, "end_char": len(text)}])
        workspace.save_source(first["ref"], {"url": first["url"], "content_scope": "full_text",
                                             "pages": [{"page": 7, "start_char": 0, "end_char": len(text)}]}, text)

        def cli(*args):
            result = subprocess.run([sys.executable, str(PATH), *args], capture_output=True, text=True,
                                    timeout=30, env={**os.environ, "DEEP_RESEARCH_SOURCE_POLICY": "local_only"})
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[:1500])
            return json.loads(result.stdout)

        self.assertEqual(cli("open", first["ref"], "--version", first["snapshot_id"])["locator"]["pages"], [1])
        found = cli("find", first["ref"], "--version", first["snapshot_id"], "--term", "samples")
        self.assertEqual(found["content_scope"], "abstract")
        self.assertEqual(found["locators"][0]["pages"], [1])
        card = cli("evidence", first["ref"], "--version", first["snapshot_id"], "--need", "N1",
                   "--claim", "Trial size", "--quote", "12 samples", "--relation", "supports")
        self.assertEqual(card["snapshot_id"], first["snapshot_id"])
        self.assertTrue(card["source_truncated"])
        self.assertEqual(cli("open", first["ref"])["locator"]["pages"], [7])

    def test_another_run_cannot_reuse_sources_or_refs(self):
        result = self.read_network()
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_RUN_DIR": str(self.root / "other")}):
            with self.assertRaisesRegex(ValueError, "not found"):
                workspace.open_source(result["ref"])
            with self.assertRaisesRegex(ValueError, "known REF"):
                research._ref_read(result["ref"], [], 100)

    def test_more_than_64_citations_are_retained(self):
        previous = [f"https://example.org/paper/{i}" for i in range(64)]
        research._save_citations(previous)
        result = self.read_network()
        self.assertEqual(result["allowed_citation_urls"], previous + [result["citation_url"]])

    def test_search_keeps_earlier_refs_after_256_results(self):
        refs = {research._source_ref(f"https://example.org/{i}"): f"https://example.org/{i}"
                for i in range(256)}
        research._save_refs(refs)
        with mock.patch.object(research, "search", return_value={"results": [
            {"url": "https://example.org/new", "title": "New source"}
        ]}):
            research._ref_search("new", 5, None, None, None, None)
        self.assertTrue(set(refs).issubset(research._load_refs()))

    def test_pdf_quotes_retain_page_numbers(self):
        path = self.root / "paper.pdf"
        with research.v1.pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), "First page introduction.")
            pdf.new_page().insert_text((72, 72), "Second page: 12 samples.")
            pdf.save(path)
        result = research._ref_read(str(path), ["samples"], 100)
        record = workspace.record_evidence(result["ref"], "N1", "Trial size", "12 samples", "supports")
        self.assertEqual(record["locator"]["pages"], [2])
        self.assertEqual(len(result["pages"]), 2)

    def test_pdf_annotation_links_are_findable_on_their_page_without_following(self):
        url = "https://example.org/paper.pdf"
        repository = "https://github.com/author/project"
        with research.v1.pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), "First page introduction.")
            page = pdf.new_page()
            page.insert_text((72, 72), "Implementation: Code is available.")
            for uri in [repository, repository, "mailto:author@example.org", "https://[broken"]:
                page.insert_link({"kind": research.v1.pymupdf.LINK_URI,
                                  "from": research.v1.pymupdf.Rect(72, 55, 200, 75), "uri": uri})
            page.insert_link({"kind": research.v1.pymupdf.LINK_GOTO,
                              "from": research.v1.pymupdf.Rect(72, 80, 200, 100), "page": 0})
            raw = pdf.tobytes()
        with mock.patch.object(research.v1, "_fetch_bytes", return_value=(
            raw, url, "application/pdf"
        )) as fetch:
            result = research._ref_read(url, [], 100)
            found = workspace.excerpts(*workspace.require_source(result["ref"]),
                                       ["github.com"], window=30, intro_if_missing=False)
        fetch.assert_called_once_with(url)
        text = workspace.require_source(result["ref"])[1]
        self.assertIn(f"[PDF link] {repository}", text)
        self.assertEqual(text.count(repository), 1)
        self.assertNotIn("mailto:", text)
        self.assertNotIn("https://[broken", text)
        self.assertIn(repository, found["windows"][0])
        self.assertEqual(found["locators"][0]["pages"], [2])
        self.assertEqual(result["allowed_citation_urls"], [url])
        self.assertEqual(workspace.status()["sources_read"], 1)

    def test_local_pdf_annotation_links_are_available_offline(self):
        path = self.root / "paper.pdf"
        with research.v1.pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), "Project page")
            page.insert_link({"kind": research.v1.pymupdf.LINK_URI,
                              "from": research.v1.pymupdf.Rect(72, 55, 200, 75),
                              "uri": "https://author.example/project"})
            pdf.save(path)
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_SOURCE_POLICY": "local_only"}), \
                mock.patch.object(research.v1, "_fetch_bytes") as fetch:
            result = research._ref_read(str(path), [], 100)
        fetch.assert_not_called()
        self.assertIn("[PDF link] https://author.example/project",
                      workspace.open_source(result["ref"])["text"])
        self.assertEqual(result["content_scope"], "local_file")
        self.assertEqual(result["allowed_citation_urls"], [])

    def test_pdf_annotation_links_obey_the_text_limit(self):
        with research.v1.pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), "Code")
            page.insert_link({"kind": research.v1.pymupdf.LINK_URI,
                              "from": research.v1.pymupdf.Rect(72, 55, 200, 75),
                              "uri": "https://github.com/author/project"})
            raw = pdf.tobytes()
        details = {}
        with mock.patch.object(research.v1, "MAX_TEXT_CHARS", 30):
            _, text = research.v1._pdf_text(raw, details)
        self.assertEqual(len(text), 30)
        self.assertTrue(details["truncated"])
        self.assertEqual(details["pages"], [{"page": 1, "start_char": 0, "end_char": 30}])

    def test_find_has_no_false_intro_result_when_term_is_missing(self):
        result = self.read_network()
        found = workspace.excerpts(*workspace.require_source(result["ref"]),
                                   ["absent phrase"], intro_if_missing=False)
        self.assertEqual(found["windows"], [])

    def test_snapshot_integrity_is_checked(self):
        result = self.read_network()
        Path(result["body_path"]).write_text("Modified content.")
        with self.assertRaisesRegex(ValueError, "cached source changed"):
            workspace.open_source(result["ref"])

    def test_text_limit_is_disclosed(self):
        with mock.patch.object(workspace, "MAX_TEXT_CHARS", 15):
            result = self.read_network("A source that exceeds fifteen characters.")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["extracted_chars"], 15)

    def test_ordinary_run_budget_is_enforced(self):
        with mock.patch.dict(os.environ, {"DEEP_RESEARCH_MAX_READ": "1", "DRBENCH_MAX_READ": ""}):
            research.base._reserve("read")
            with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
                research.base._reserve("read")

    def test_source_and_evidence_summary_survives_reloading(self):
        result = self.read_network()
        for relation, quote in [("supports", "12 samples"), ("context", "Limits apply")]:
            workspace.record_evidence(result["ref"], "N1", "Trial size", quote, relation)
        status = workspace.status()
        self.assertEqual(status["sources_read"], 1)
        self.assertEqual(status["evidence_by_need"]["N1"], {"supports": 1, "refutes": 0, "context": 1})
        self.assertEqual(len(json.loads((workspace.root() / "evidence.json").read_text())), 2)


if __name__ == "__main__":
    unittest.main()
