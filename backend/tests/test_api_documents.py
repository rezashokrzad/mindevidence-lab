"""Documents, chunks, and admin ingestion over HTTP, using the REAL corpus in data/papers.

The traceability test re-opens each original PDF and checks that every sampled chunk's
text really appears on the PDF pages the chunk claims, so provenance is verified against
the source rather than trusted.
"""
import re
import unicodedata
import uuid

import fitz
import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.ingestion.config import REPO_ROOT, IngestionSettings
from app.ingestion.manifest import load_manifest
from app.ingestion.pipeline import run_ingestion
from tests.test_api_patients import assert_error

PAPERS = REPO_ROOT / "data" / "papers"
MANIFEST = REPO_ROOT / "data" / "corpus_manifest.json"


@pytest.fixture(scope="module")
def real_corpus(engine, tmp_path_factory):
    if not all((PAPERS / d.filename).is_file() for d in load_manifest(MANIFEST).documents if d.filename):
        pytest.skip("real corpus PDFs not present in data/papers")
    with engine.begin() as c:
        c.execute(text("TRUNCATE corpus.documents, corpus.ingestion_runs CASCADE"))
    settings = IngestionSettings(corpus_source_dir=PAPERS, corpus_manifest=MANIFEST,
                                 document_storage_dir=tmp_path_factory.mktemp("storage"))
    report = run_ingestion(sessionmaker(bind=engine), settings, triggered_by="test")
    yield settings, report


@pytest.fixture
def instructor(make_client, make_user, real_corpus):
    settings, _ = real_corpus
    return make_client(as_user=make_user(role="instructor"), ingestion_settings=settings)


@pytest.fixture
def student(make_client, real_corpus):
    settings, _ = real_corpus
    return make_client(ingestion_settings=settings)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", s).lower())


# ---------- the real corpus ingests cleanly ----------

def test_real_corpus_run_result(real_corpus):
    _, report = real_corpus
    actions = {r.document_id: r.action for r in report.results}
    assert actions == {"WHO-GL-2023": "ingested", "NIDA-PDAT": "ingested", "COCH-AUD-2025": "ingested",
                       "WHO-IG-2016": "missing_file"}
    # Manifest metadata (DOI, ISBN, year, title) agrees with what the PDFs say.
    assert all(not r.warnings for r in report.results if r.action == "ingested")


# ---------- documents ----------

def test_list_documents(student):
    body = student.get("/api/documents").json()
    by_id = {d["document_id"]: d for d in body["items"]}
    assert body["total"] == 4 and set(by_id) == {"WHO-GL-2023", "NIDA-PDAT", "COCH-AUD-2025", "WHO-IG-2016"}
    assert by_id["WHO-IG-2016"]["ingestion_status"] == "missing_file" and by_id["WHO-IG-2016"]["chunk_count"] == 0
    assert all(by_id[d]["ingestion_status"] == "ingested" and by_id[d]["chunk_count"] > 20
               for d in ("WHO-GL-2023", "NIDA-PDAT", "COCH-AUD-2025"))
    filtered = student.get("/api/documents", params={"status": "ingested"}).json()
    assert filtered["total"] == 3


def test_document_detail_metadata_and_citation(student):
    d = student.get("/api/documents/COCH-AUD-2025").json()
    assert d["doi"] == "10.1002/14651858.CD015673.pub2"
    assert d["authors"][0] == "Minozzi S" and len(d["authors"]) == 7
    assert d["citation"].startswith("Minozzi S, La Rosa GRM, Salis F, Camposeragna A, Saulle R, Leggio L, et al.")
    assert "doi:10.1002/14651858.CD015673.pub2" in d["citation"]
    assert d["extracted_metadata"]["doi"] == d["doi"]           # found in the PDF itself
    assert len(d["file_sha256"]) == 64 and d["page_count"] == 113
    assert d["outline"] and all(o["page"] >= 1 for o in d["outline"])

    who = student.get("/api/documents/WHO-GL-2023").json()
    assert who["isbn"] == "978-92-4-008427-8" and who["license"] == "CC BY-NC-SA 3.0 IGO"
    assert who["citation"] == ("World Health Organization. Mental Health Gap Action Programme (mhGAP) guideline for "
                               "mental, neurological and substance use disorders. World Health Organization; 2023. "
                               "ISBN 978-92-4-008427-8.")


def test_document_errors(student, anon_client):
    assert_error(student.get("/api/documents/NOPE-123"), 404, "document_not_found")
    assert_error(student.get("/api/documents/bad id"), 422, "validation_error")
    assert_error(anon_client.get("/api/documents"), 401, "authentication_required")


def test_list_chunks_paginated(student):
    body = student.get("/api/documents/NIDA-PDAT/chunks", params={"limit": 5, "offset": 5}).json()
    assert [c["ordinal"] for c in body["items"]] == [6, 7, 8, 9, 10]
    assert body["total"] > 20 and all(len(c["preview"]) <= 241 for c in body["items"])


# ---------- chunks: the acceptance criterion ----------

@pytest.mark.parametrize("doc_id, filename", [("NIDA-PDAT", "podat-3rdEd-508.pdf"),
                                              ("WHO-GL-2023", "9789240084278-eng.pdf"),
                                              ("COCH-AUD-2025", "CD015673.pdf")])
def test_chunk_text_is_found_on_its_claimed_pages(student, doc_id, filename):
    pdf = fitz.open(PAPERS / filename)
    total = student.get(f"/api/documents/{doc_id}/chunks").json()["total"]
    checked = 0
    for ordinal in range(1, total + 1, 6):
        c = student.get(f"/api/chunks/{doc_id}-c{ordinal:04d}").json()
        source = norm("".join(pdf[p - 1].get_text() for p in range(c["page_start"], c["page_end"] + 1)))
        longest = max(c["text"].split("\n\n"), key=len)
        n = norm(longest)
        window = n[len(n) // 2 - 30: len(n) // 2 + 30] if len(n) > 60 else n
        assert window in source, f"{c['chunk_id']} text not found on PDF pages {c['page_start']}-{c['page_end']}"
        checked += 1
    assert checked >= 7


def test_chunk_provenance_fields(student):
    first = student.get("/api/documents/NIDA-PDAT/chunks", params={"limit": 100}).json()["items"]
    principles = next(c for c in first if c["section_path"][:1] == ["Principles of Effective Treatment"])
    c = student.get(f"/api/chunks/{principles['chunk_id']}").json()
    assert c["document"]["document_id"] == "NIDA-PDAT"
    assert c["document"]["citation"].startswith("National Institute on Drug Abuse. Principles of Drug Addiction")
    assert c["section_path"][0] == "Principles of Effective Treatment"
    assert "Principles of Effective Treatment" in c["location"] and "PDF p" in c["location"]
    import hashlib
    assert c["content_hash"] == hashlib.sha256(c["text"].encode()).hexdigest()
    assert c["source_kind"] == "research_document"
    # neighbours link up
    if c["next_chunk_id"]:
        nxt = student.get(f"/api/chunks/{c['next_chunk_id']}").json()
        assert nxt["previous_chunk_id"] == c["chunk_id"] and nxt["ordinal"] == c["ordinal"] + 1


def test_printed_page_labels_when_defined(student):
    items = student.get("/api/documents/WHO-GL-2023/chunks", params={"limit": 100, "offset": 60}).json()["items"]
    assert any(c["page_label_start"] and c["page_label_start"] != str(c["page_start"]) for c in items)


def test_chunk_errors(student, anon_client):
    assert_error(student.get("/api/chunks/NIDA-PDAT-c9999"), 404, "chunk_not_found")
    assert_error(student.get("/api/chunks/NIDA-PDAT-7"), 422, "validation_error")
    assert_error(anon_client.get("/api/chunks/NIDA-PDAT-c0001"), 401, "authentication_required")


def test_research_text_is_not_patient_data(engine, real_corpus):
    """Research chunks live in `corpus`; nothing links them to the synthetic cases."""
    with engine.connect() as c:
        links = c.execute(text("""
            SELECT count(*) FROM pg_constraint
            WHERE contype = 'f' AND conrelid IN ('corpus.chunks'::regclass, 'corpus.documents'::regclass)
              AND confrelid::regclass::text LIKE 'cases.%'""")).scalar_one()
        leaked = c.execute(text("SELECT count(*) FROM corpus.chunks WHERE text ~ 'P-0(0[1-9]|10)\\M'")).scalar_one()
    assert links == 0 and leaked == 0


# ---------- admin ingestion ----------

def test_students_cannot_use_admin_endpoints(student):
    assert_error(student.post("/api/admin/ingestion/runs", json={}), 403, "forbidden")
    assert_error(student.get("/api/admin/ingestion/runs"), 403, "forbidden")


def test_admin_requires_sign_in(anon_client):
    assert_error(anon_client.post("/api/admin/ingestion/runs", json={}), 401, "authentication_required")
    assert_error(anon_client.get("/api/admin/ingestion/runs"), 401, "authentication_required")


def test_admin_run_requires_csrf(instructor):
    r = instructor.post("/api/admin/ingestion/runs", json={}, headers={"X-CSRF-Token": "wrong" * 10})
    assert_error(r, 403, "csrf_failed")


def test_admin_rerun_is_idempotent(instructor, engine):
    with engine.connect() as c:
        before = c.execute(text("SELECT count(*), md5(string_agg(chunk_id || content_hash, ',' ORDER BY chunk_id)) "
                                "FROM corpus.chunks")).one()
    r = instructor.post("/api/admin/ingestion/runs", json={})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "succeeded" and run["triggered_by"].startswith("user:")
    assert {x["action"] for x in run["results"] if x["document_id"] != "WHO-IG-2016"} == {"unchanged"}
    with engine.connect() as c:
        after = c.execute(text("SELECT count(*), md5(string_agg(chunk_id || content_hash, ',' ORDER BY chunk_id)) "
                               "FROM corpus.chunks")).one()
    assert after == before
    listed = instructor.get("/api/admin/ingestion/runs").json()
    assert listed["items"][0]["run_id"] == run["run_id"]
    assert instructor.get(f"/api/admin/ingestion/runs/{run['run_id']}").json()["status"] == "succeeded"


def test_admin_run_validation(instructor):
    assert_error(instructor.post("/api/admin/ingestion/runs", json={"document_ids": ["NOT-LISTED"]}),
                 422, "unknown_document")
    assert_error(instructor.post("/api/admin/ingestion/runs", json={"surprise": True}), 422, "validation_error")
    assert_error(instructor.get(f"/api/admin/ingestion/runs/{uuid.uuid4()}"), 404, "ingestion_run_not_found")


def test_admin_run_conflict_when_locked(instructor, engine):
    from app.repositories import documents as repo
    with sessionmaker(bind=engine)() as holder:
        assert repo.try_ingestion_lock(holder)
        try:
            assert_error(instructor.post("/api/admin/ingestion/runs", json={}), 409, "ingestion_in_progress")
        finally:
            repo.release_ingestion_lock(holder)
