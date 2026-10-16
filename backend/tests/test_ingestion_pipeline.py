"""Ingestion pipeline against PostgreSQL: idempotency, duplicates, explicit failures, locking.

Uses generated PDFs in a temporary source directory and a temporary manifest, so these
tests never depend on (or modify) the real corpus.
"""
import json
import shutil

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.ingestion.config import IngestionSettings
from app.ingestion.pipeline import IngestionBusy, run_ingestion
from app.ingestion.storage import sha256_file
from app.repositories import documents as repo
from tests.pdf_factory import body_text, make_scanned_pdf, make_text_pdf

SECTIONS = [(f"Section {i}", [body_text(30, f"S{i}-"), body_text(25, f"T{i}-")]) for i in range(1, 6)]


def entry(doc_id, filename, sha=None, **extra):
    return {"document_id": doc_id, "filename": filename, "sha256": sha, "title": f"Test document {doc_id}",
            "short_title": doc_id, "publisher": "Test Publisher", "document_type": "other", **extra}


@pytest.fixture
def corpus(tmp_path, engine):
    """Temporary source dir + manifest + storage, and a clean corpus schema."""
    with engine.begin() as c:
        c.execute(text("TRUNCATE corpus.documents, corpus.ingestion_runs CASCADE"))
    src, store = tmp_path / "papers", tmp_path / "storage"
    src.mkdir()

    class Corpus:
        source, storage, manifest = src, store, tmp_path / "manifest.json"
        factory = sessionmaker(bind=engine, expire_on_commit=False)

        def write_manifest(self, docs):
            self.manifest.write_text(json.dumps({"manifest_version": 1, "documents": docs}), encoding="utf-8")

        def settings(self, **over):
            return IngestionSettings(corpus_source_dir=src, corpus_manifest=self.manifest,
                                     document_storage_dir=store, **over)

        def run(self, **kw):
            report = run_ingestion(self.factory, kw.pop("settings", None) or self.settings(), triggered_by="test", **kw)
            return report, {r.document_id or r.filename: r for r in report.results}

        def chunks(self, doc_id):
            with engine.connect() as c:
                return c.execute(text("SELECT chunk_id, content_hash, page_start, page_end, section_path "
                                      "FROM corpus.chunks WHERE document_id = :d ORDER BY ordinal"),
                                 {"d": doc_id}).all()

        def doc(self, doc_id):
            with engine.connect() as c:
                return c.execute(text("SELECT * FROM corpus.documents WHERE document_id = :d"),
                                 {"d": doc_id}).mappings().one()

    yield Corpus()
    with engine.begin() as c:
        c.execute(text("TRUNCATE corpus.documents, corpus.ingestion_runs CASCADE"))


def test_ingest_creates_traceable_chunks_and_stores_original(corpus):
    pdf = make_text_pdf(corpus.source / "doc.pdf", SECTIONS)
    corpus.write_manifest([entry("TEST-DOC", "doc.pdf", sha256_file(pdf))])
    report, res = corpus.run()
    assert report.status == "succeeded" and res["TEST-DOC"].action == "ingested"

    chunks = corpus.chunks("TEST-DOC")
    assert len(chunks) == res["TEST-DOC"].chunk_count > 1
    assert [c.chunk_id for c in chunks] == [f"TEST-DOC-c{i:04d}" for i in range(1, len(chunks) + 1)]
    assert all(c.section_path and c.section_path[0].startswith("Section") for c in chunks)
    assert all(1 <= c.page_start <= c.page_end <= 5 for c in chunks)

    d = corpus.doc("TEST-DOC")
    assert d["ingestion_status"] == "ingested" and d["file_sha256"] == sha256_file(pdf)
    assert d["page_count"] == 5 and d["outline"] and d["ingest_fingerprint"]
    stored = corpus.storage / d["storage_path"]
    assert stored.is_file() and sha256_file(stored) == sha256_file(pdf)


def test_rerun_is_idempotent(corpus, engine):
    make_text_pdf(corpus.source / "doc.pdf", SECTIONS)
    corpus.write_manifest([entry("TEST-DOC", "doc.pdf")])
    corpus.run()
    before = corpus.chunks("TEST-DOC")
    report, res = corpus.run()
    assert res["TEST-DOC"].action == "unchanged" and report.status == "succeeded"
    assert corpus.chunks("TEST-DOC") == before            # same ids, hashes, pages; no duplicates
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM corpus.ingestion_runs")).scalar_one() == 2


def test_force_reingest_is_deterministic(corpus):
    make_text_pdf(corpus.source / "doc.pdf", SECTIONS)
    corpus.write_manifest([entry("TEST-DOC", "doc.pdf")])
    corpus.run()
    before = corpus.chunks("TEST-DOC")
    _, res = corpus.run(force=True)
    assert res["TEST-DOC"].action == "reingested"
    assert corpus.chunks("TEST-DOC") == before


def test_changed_chunking_settings_trigger_reingest(corpus):
    make_text_pdf(corpus.source / "doc.pdf", SECTIONS)
    corpus.write_manifest([entry("TEST-DOC", "doc.pdf")])
    corpus.run()
    n_before = len(corpus.chunks("TEST-DOC"))
    small = corpus.settings(chunk_target_tokens=250, chunk_min_tokens=200, chunk_max_tokens=300,
                            chunk_overlap_tokens=40)
    _, res = corpus.run(settings=small)
    assert res["TEST-DOC"].action == "reingested"
    assert len(corpus.chunks("TEST-DOC")) > n_before


def test_changed_file_replaces_chunks_without_duplicates(corpus):
    make_text_pdf(corpus.source / "doc.pdf", SECTIONS)
    corpus.write_manifest([entry("TEST-DOC", "doc.pdf")])     # no pinned checksum
    corpus.run()
    make_text_pdf(corpus.source / "doc.pdf", SECTIONS[:2])
    _, res = corpus.run()
    assert res["TEST-DOC"].action == "reingested"
    chunks = corpus.chunks("TEST-DOC")
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert all(c.page_end <= 2 for c in chunks)


def test_checksum_mismatch_is_rejected_and_keeps_existing_chunks(corpus):
    pdf = make_text_pdf(corpus.source / "doc.pdf", SECTIONS)
    corpus.write_manifest([entry("TEST-DOC", "doc.pdf", sha256_file(pdf))])
    corpus.run()
    before = corpus.chunks("TEST-DOC")
    make_text_pdf(corpus.source / "doc.pdf", SECTIONS[:1])   # file swapped, manifest still pins the old one
    report, res = corpus.run()
    assert res["TEST-DOC"].action == "checksum_mismatch" and report.status == "failed"
    assert corpus.chunks("TEST-DOC") == before
    assert "does not match the manifest" in corpus.doc("TEST-DOC")["ingestion_error"]


def test_scanned_pdf_is_flagged_not_ingested(corpus):
    make_scanned_pdf(corpus.source / "scan.pdf")
    make_text_pdf(corpus.source / "ok.pdf", SECTIONS)
    corpus.write_manifest([entry("SCAN-DOC", "scan.pdf"), entry("OK-DOC", "ok.pdf")])
    report, res = corpus.run()
    assert res["SCAN-DOC"].action == "needs_ocr" and res["SCAN-DOC"].severity == "error"
    assert res["OK-DOC"].action == "ingested"            # one bad file does not stop the others
    assert report.status == "partial"
    d = corpus.doc("SCAN-DOC")
    assert d["ingestion_status"] == "needs_ocr" and d["chunk_count"] == 0 and "needs_ocr" in d["ingestion_error"]
    assert corpus.chunks("SCAN-DOC") == []


def test_corrupt_pdf_fails_explicitly(corpus):
    (corpus.source / "bad.pdf").write_bytes(b"%PDF-1.4 not a real pdf")
    corpus.write_manifest([entry("BAD-DOC", "bad.pdf")])
    report, res = corpus.run()
    assert res["BAD-DOC"].action == "failed" and report.status == "failed"
    assert corpus.doc("BAD-DOC")["ingestion_status"] == "failed"
    assert corpus.chunks("BAD-DOC") == []


def test_failure_after_success_clears_old_chunks(corpus):
    make_text_pdf(corpus.source / "doc.pdf", SECTIONS)
    corpus.write_manifest([entry("TEST-DOC", "doc.pdf")])
    corpus.run()
    make_scanned_pdf(corpus.source / "doc.pdf")             # new version is a scan
    _, res = corpus.run()
    assert res["TEST-DOC"].action == "needs_ocr"
    assert corpus.chunks("TEST-DOC") == []                   # never left with chunks of another file


def test_missing_file_reported(corpus):
    corpus.write_manifest([entry("GONE-DOC", "gone.pdf"), entry("NOFILE-DOC", None)])
    report, res = corpus.run()
    assert res["GONE-DOC"].action == "missing_file" and res["NOFILE-DOC"].action == "missing_file"
    assert corpus.doc("GONE-DOC")["ingestion_status"] == "missing_file"
    assert report.status == "succeeded"                      # warnings, not errors


def test_unlisted_and_duplicate_files_are_not_ingested(corpus, engine):
    pdf = make_text_pdf(corpus.source / "doc.pdf", SECTIONS)
    shutil.copy(pdf, corpus.source / "copy-of-doc.pdf")
    make_text_pdf(corpus.source / "random.pdf", SECTIONS[:1])
    corpus.write_manifest([entry("TEST-DOC", "doc.pdf")])
    _, res = corpus.run()
    assert res["copy-of-doc.pdf"].action == "duplicate_file"
    assert res["random.pdf"].action == "unlisted_file"
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM corpus.documents")).scalar_one() == 1


def test_same_file_under_two_manifest_entries(corpus):
    pdf = make_text_pdf(corpus.source / "a.pdf", SECTIONS)
    shutil.copy(pdf, corpus.source / "b.pdf")
    corpus.write_manifest([entry("DOC-A", "a.pdf"), entry("DOC-B", "b.pdf")])
    report, res = corpus.run()
    assert res["DOC-A"].action == "ingested" and res["DOC-B"].action == "duplicate_file"
    assert corpus.chunks("DOC-B") == [] and report.status == "partial"


def test_metadata_disagreements_are_reported(corpus):
    front = "Copyright © 2021 Example. doi: 10.1234/real.1. " + body_text(4)
    make_text_pdf(corpus.source / "doc.pdf", SECTIONS, front_matter=front)
    corpus.write_manifest([entry("TEST-DOC", "doc.pdf", doi="10.1234/wrong.2", year=2019)])
    _, res = corpus.run()
    warnings = " ".join(res["TEST-DOC"].warnings)
    assert "DOI in PDF" in warnings and "year 2019" in warnings
    meta = corpus.doc("TEST-DOC")["extracted_metadata"]
    assert meta["doi"] == "10.1234/real.1" and meta["manifest_comparison_warnings"]


def test_only_one_run_at_a_time(corpus):
    make_text_pdf(corpus.source / "doc.pdf", SECTIONS)
    corpus.write_manifest([entry("TEST-DOC", "doc.pdf")])
    with corpus.factory() as holder:
        assert repo.try_ingestion_lock(holder)
        with pytest.raises(IngestionBusy):
            corpus.run()
        repo.release_ingestion_lock(holder)
    assert corpus.run()[0].status == "succeeded"


def test_run_is_recorded(corpus, engine):
    make_text_pdf(corpus.source / "doc.pdf", SECTIONS)
    corpus.write_manifest([entry("TEST-DOC", "doc.pdf")])
    report, _ = corpus.run()
    with engine.connect() as c:
        row = c.execute(text("SELECT * FROM corpus.ingestion_runs WHERE run_id = :r"),
                        {"r": report.run_id}).mappings().one()
    assert row["status"] == "succeeded" and row["finished_at"] is not None and row["triggered_by"] == "test"
    assert row["chunking_config"]["target_tokens"] == 550
    assert row["results"][0]["document_id"] == "TEST-DOC"


def test_invalid_manifest_rejected(corpus):
    corpus.write_manifest([entry("DUP", "a.pdf"), entry("DUP", "b.pdf")])
    from app.ingestion.manifest import ManifestError
    with pytest.raises(ManifestError, match="duplicate document_id"):
        corpus.run()


def test_lock_is_always_released_even_after_many_runs(corpus, engine):
    """Regression: a mid-run commit once let the unlock run on another pooled connection, leaking the lock."""
    make_text_pdf(corpus.source / "doc.pdf", SECTIONS)
    corpus.write_manifest([entry("TEST-DOC", "doc.pdf")])
    for _ in range(6):
        assert corpus.run()[0].status == "succeeded"
    with engine.connect() as c:
        held = c.execute(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted")).scalar_one()
    assert held == 0
