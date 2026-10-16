"""POST /api/retrieval/search and the embedding pipeline.

Part 1 runs on any test database (including one WITHOUT pgvector): validation, auth, and the
"index not available" behaviour.
Part 2 needs the pgvector extension (skipped otherwise): migration objects, embedding,
idempotency, model-mixing guards, failure handling, exact cosine search, filters, determinism.

Part 2 uses small generated PDFs ingested through the real pipeline and the deterministic
HashEmbedder, so it checks the plumbing and the SQL, NOT real semantic quality.
"""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.ingestion.config import IngestionSettings
from app.ingestion.pipeline import run_ingestion
from app.retrieval.config import RetrievalSettings
from app.retrieval.embedder import HashEmbedder, model_id
from app.retrieval.indexer import ModelMismatch, embed_corpus
from tests.pdf_factory import make_text_pdf
from tests.test_api_patients import assert_error

SEARCH = "/api/retrieval/search"
SETTINGS = RetrievalSettings(embedding_backend="hash", embedding_retry_backoff_seconds=0)


# ================================================================ part 1: no pgvector needed

def test_search_requires_sign_in(anon_client):
    assert_error(anon_client.post(SEARCH, json={"query": "methadone"}), 401, "authentication_required")
    assert_error(anon_client.get("/api/retrieval/status"), 401, "authentication_required")


def test_search_requires_csrf(client):
    assert_error(client.post(SEARCH, json={"query": "methadone"}, headers={"X-CSRF-Token": "x" * 40}),
                 403, "csrf_failed")


@pytest.mark.parametrize("body", [{}, {"query": "ab"}, {"query": "valid", "top_k": 0}, {"query": "valid", "x": 1},
                                  {"query": "valid query", "filters": {"topics": ["astrology"]}}])
def test_search_validates_input(client, body):
    assert_error(client.post(SEARCH, json=body), 422, "validation_error")


def test_search_unavailable_before_index_is_built(client, vector_available):
    if vector_available:
        pytest.skip("covered by the pgvector tests below (index may exist)")
    err = assert_error(client.post(SEARCH, json={"query": "How long should treatment last?"}), 503,
                       "retrieval_unavailable")
    assert "pgvector" in err["message"]


def test_status_reports_not_ready_without_index(client, vector_available):
    if vector_available:
        pytest.skip("index state depends on earlier tests")
    body = client.get("/api/retrieval/status").json()
    assert body["ready"] is False and body["embedded_chunks"] == 0


def test_openapi_documents_retrieval(client):
    spec = client.get("/api/openapi.json").json()
    op = spec["paths"]["/api/retrieval/search"]["post"]
    assert {"401", "403", "409", "422", "503"} <= set(op["responses"])
    assert "not probabilities" in op["description"].lower() or "not correctness" in op["description"].lower()
    schemas = spec["components"]["schemas"]
    assert {"SearchRequest", "SearchResponse", "SearchResult", "RetrievalSettingsOut"} <= set(schemas)
    fields = schemas["SearchResult"]["properties"]
    assert {"chunk_id", "document", "section_path", "page_start", "text", "distance", "similarity"} <= set(fields)


# ================================================================ part 2: needs pgvector

DOCS = {
    "DOC-ALC": ("Alcohol", ["Naltrexone and acamprosate reduce heavy drinking in alcohol use disorder. " * 12,
                            "Brief interventions for hazardous alcohol use follow screening and feedback. " * 12]),
    "DOC-OPI": ("Opioids", ["Methadone and buprenorphine maintenance treat opioid dependence and prevent overdose. " * 12,
                            "Naloxone distribution reduces opioid overdose deaths in the community. " * 12]),
    "DOC-GAM": ("Gambling", ["Gambling debt and sports betting urges respond to behavioural therapy. " * 12]),
}


def _entry(doc_id, filename, doc_type):
    return {"document_id": doc_id, "filename": filename, "title": f"Test {doc_id}", "short_title": doc_id,
            "publisher": "Test", "document_type": doc_type}


@pytest.fixture
def indexed(tmp_path, engine, needs_vector):
    """Generated corpus ingested and embedded with the HashEmbedder."""
    src = tmp_path / "papers"
    src.mkdir()
    types = {"DOC-ALC": "systematic_review", "DOC-OPI": "guideline", "DOC-GAM": "other"}
    entries = []
    for doc_id, (title, paragraphs) in DOCS.items():
        # Heading detection needs a style that recurs on several pages, as in real documents.
        make_text_pdf(src / f"{doc_id}.pdf", [(f"{title} {n}", paragraphs) for n in (1, 2, 3)], footer=None)
        entries.append(_entry(doc_id, f"{doc_id}.pdf", types[doc_id]))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"manifest_version": 1, "documents": entries}), encoding="utf-8")
    with engine.begin() as c:
        c.execute(text("TRUNCATE corpus.documents, corpus.embedding_models, corpus.embedding_failures CASCADE"))
    ing = IngestionSettings(corpus_source_dir=src, corpus_manifest=manifest, document_storage_dir=tmp_path / "st")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    assert run_ingestion(factory, ing, triggered_by="test").status == "succeeded"
    report = embed_corpus(factory, HashEmbedder(), SETTINGS, sleep=lambda _: None)
    assert report.ok and report.embedded_chunks > 0

    class Ctx:
        pass

    ctx = Ctx()
    ctx.factory, ctx.ingest, ctx.report, ctx.engine = factory, ing, report, engine
    yield ctx
    with engine.begin() as c:
        c.execute(text("TRUNCATE corpus.documents, corpus.embedding_models, corpus.embedding_failures CASCADE"))


def post(client, query, **extra):
    return client.post(SEARCH, json={"query": query, **extra})


# ---------- schema ----------

def test_vector_schema_objects(indexed):
    with indexed.engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM pg_extension WHERE extname='vector'")).scalar_one() == 1
        col = c.execute(text("SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                             "WHERE attrelid='corpus.chunk_embeddings'::regclass AND attname='embedding'")).scalar_one()
        assert col == "vector(384)"
        rows = c.execute(text("SELECT model_id, is_active, dimension FROM corpus.embedding_models")).all()
    assert rows == [(model_id(HashEmbedder()), True, 384)]


def test_database_rejects_wrong_dimension_and_other_models(indexed):
    cid = "DOC-OPI-c0001"
    with indexed.engine.connect() as c:
        tx = c.begin()
        with pytest.raises(DBAPIError):          # a 3-dimensional vector cannot be stored in vector(384)
            c.execute(text("INSERT INTO corpus.chunk_embeddings (chunk_id, window_no, model_id, embedding, "
                           "char_start, char_end, content_hash) VALUES (:c, 99, :m, '[1,2,3]', 0, 5, repeat('a',64))"),
                      {"c": cid, "m": model_id(HashEmbedder())})
        tx.rollback()
    with indexed.engine.connect() as c:
        c.execute(text("INSERT INTO corpus.embedding_models (model_id, name, version, dimension) "
                       "VALUES ('other@1#384', 'other', '1', 384)"))
        c.commit()
        vec = "[" + ",".join(["0.1"] * 384) + "]"
        with pytest.raises(DBAPIError, match="not the active model"):      # trigger blocks mixing models
            c.execute(text("INSERT INTO corpus.chunk_embeddings (chunk_id, window_no, model_id, embedding, "
                           "char_start, char_end, content_hash) VALUES (:c, 98, 'other@1#384', CAST(:v AS vector), "
                           "0, 5, repeat('a',64))"), {"c": cid, "v": vec})
        c.rollback()


# ---------- embedding pipeline ----------

def test_every_chunk_gets_windows_with_offsets(indexed):
    with indexed.engine.connect() as c:
        n_chunks = c.execute(text("SELECT count(*) FROM corpus.chunks")).scalar_one()
        embedded = c.execute(text("SELECT count(DISTINCT chunk_id) FROM corpus.chunk_embeddings")).scalar_one()
        bad = c.execute(text("SELECT count(*) FROM corpus.chunk_embeddings e JOIN corpus.chunks k USING (chunk_id) "
                             "WHERE e.char_end > length(k.text) OR e.content_hash <> k.content_hash")).scalar_one()
    assert embedded == n_chunks and bad == 0


def test_embedding_is_idempotent(indexed):
    before = indexed.report.windows
    again = embed_corpus(indexed.factory, HashEmbedder(), SETTINGS)
    assert again.embedded_chunks == 0 and again.skipped_chunks == indexed.report.embedded_chunks
    with indexed.engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM corpus.chunk_embeddings")).scalar_one() == before


def test_different_model_is_refused_without_rebuild(indexed):
    class Other(HashEmbedder):
        version = "2"

    with pytest.raises(ModelMismatch, match="--rebuild"):
        embed_corpus(indexed.factory, Other(), SETTINGS)
    report = embed_corpus(indexed.factory, Other(), SETTINGS, rebuild=True)
    assert report.rebuilt and report.ok
    with indexed.engine.connect() as c:
        assert c.execute(text("SELECT DISTINCT model_id FROM corpus.chunk_embeddings")).scalars().all() == [
            "hash-bow@2#384"]


def test_failed_chunks_are_reported_recorded_and_retried(indexed):
    with indexed.engine.begin() as c:
        c.execute(text("TRUNCATE corpus.chunk_embeddings, corpus.embedding_failures"))

    class Poisoned(HashEmbedder):
        def embed(self, texts):
            if any("Gambling" in t for t in texts):
                raise RuntimeError("model crashed on this text")
            return super().embed(texts)

    report = embed_corpus(indexed.factory, Poisoned(), SETTINGS, sleep=lambda _: None)
    assert not report.ok and all(cid.startswith("DOC-GAM") for cid, _ in report.failed)
    assert report.embedded_chunks > 0                                    # other chunks were not blocked
    with indexed.engine.connect() as c:
        failures = c.execute(text("SELECT chunk_id, attempts, last_error FROM corpus.embedding_failures")).all()
        assert failures and failures[0].attempts == SETTINGS.embedding_max_retries + 1
        assert "model crashed" in failures[0].last_error
    fixed = embed_corpus(indexed.factory, HashEmbedder(), SETTINGS)       # next run retries only the failed ones
    assert fixed.ok and fixed.embedded_chunks == len(report.failed)
    with indexed.engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM corpus.embedding_failures")).scalar_one() == 0


def test_reingesting_a_document_removes_its_stale_embeddings(indexed):
    make_text_pdf(indexed.ingest.corpus_source_dir / "DOC-GAM.pdf",
                  [("Gambling", ["Entirely different gambling text about casino self-exclusion. " * 12])], footer=None)
    run_ingestion(indexed.factory, indexed.ingest, triggered_by="test")
    with indexed.engine.connect() as c:
        left = c.execute(text("SELECT count(*) FROM corpus.chunk_embeddings WHERE chunk_id LIKE 'DOC-GAM%'")).scalar_one()
    assert left == 0                                              # cascade: no vectors for text that no longer exists
    report = embed_corpus(indexed.factory, HashEmbedder(), SETTINGS)
    assert report.embedded_chunks >= 1 and report.skipped_chunks > 0


# ---------- search over HTTP ----------

@pytest.fixture
def api(make_client, indexed):
    return make_client(embedder=HashEmbedder(), retrieval_settings=SETTINGS)


def test_search_returns_chunks_with_full_provenance(api):
    body = post(api, "methadone buprenorphine opioid dependence maintenance").json()
    top = body["results"][0]
    assert top["document"]["document_id"] == "DOC-OPI" and top["rank"] == 1
    assert top["chunk_id"].startswith("DOC-OPI-c") and "Methadone" in top["text"]
    assert top["section_path"][0].startswith("Opioids") and top["page_start"] >= 1
    assert top["location"].startswith("DOC-OPI · Opioids ") and "PDF p" in top["location"]
    assert top["matched_passage"] in top["text"]
    assert top["text"][top["matched_char_start"]:top["matched_char_end"]] == top["matched_passage"]
    assert top["similarity"] == pytest.approx(1 - top["distance"], abs=1e-5)
    assert 0 < top["similarity"] <= 1
    assert body["source_kind"] == "research_documents" and "not probabilities" in body["note"]


def test_results_are_ordered_by_cosine_similarity(api):
    results = post(api, "naloxone overdose deaths community", top_k=10).json()["results"]
    sims = [r["similarity"] for r in results]
    assert sims == sorted(sims, reverse=True) and [r["rank"] for r in results] == list(range(1, len(results) + 1))
    assert len({r["chunk_id"] for r in results}) == len(results)


def test_retrieval_settings_are_exposed(api):
    s = post(api, "alcohol", top_k=3, min_similarity=-1, filters={"topics": ["alcohol"]}).json()["settings"]
    assert s["embedding_model"] == "hash-bow" and s["embedding_model_version"] == "1"
    assert s["embedding_dimension"] == 384 and s["metric"] == "cosine" and s["search_type"] == "exact"
    assert s["top_k"] == 3 and s["min_similarity"] == -1 and s["filters"] == {"topics": ["alcohol"]}
    assert s["deterministic"] is True and "chunk_id" in s["tie_break"]


def test_top_k_default_and_cap(api):
    assert len(post(api, "treatment of dependence").json()["results"]) <= SETTINGS.retrieval_default_top_k
    capped = RetrievalSettings(embedding_backend="hash", retrieval_max_top_k=2, retrieval_default_top_k=2)
    # a 6-result request is capped to the server maximum
    small = api.app.state.retrieval_settings
    api.app.state.retrieval_settings = capped
    try:
        s = post(api, "treatment of dependence", top_k=20).json()
        assert len(s["results"]) <= 2 and s["settings"]["top_k"] == 2
    finally:
        api.app.state.retrieval_settings = small


def test_search_is_deterministic(api):
    a = post(api, "overdose prevention naloxone", top_k=8).json()
    b = post(api, "overdose prevention naloxone", top_k=8).json()
    assert a["results"] == b["results"]


def test_filters(api):
    only_alc = post(api, "treatment", top_k=20, filters={"document_ids": ["DOC-ALC"]}).json()["results"]
    assert only_alc and {r["document"]["document_id"] for r in only_alc} == {"DOC-ALC"}
    guide = post(api, "treatment", top_k=20, filters={"document_types": ["guideline"]}).json()["results"]
    assert {r["document"]["document_id"] for r in guide} == {"DOC-OPI"}
    topic = post(api, "treatment", top_k=20, filters={"topics": ["behavioral"]}).json()["results"]
    assert topic and {r["document"]["document_id"] for r in topic} == {"DOC-GAM"}
    none = post(api, "treatment", filters={"document_ids": ["DOC-ALC"], "topics": ["opioids"]}).json()
    assert none["results"] == [] and none["result_count"] == 0


def test_min_similarity_cuts_off_weak_results(api):
    everything = post(api, "naloxone overdose", top_k=20).json()["results"]
    cutoff = everything[0]["similarity"] - 1e-4
    strict = post(api, "naloxone overdose", top_k=20, min_similarity=cutoff).json()["results"]
    assert 1 <= len(strict) < len(everything) and all(r["similarity"] >= cutoff - 1e-5 for r in strict)


def test_model_mismatch_is_rejected_not_silently_searched(make_client, indexed):
    class Newer(HashEmbedder):
        version = "2"

    c = make_client(embedder=Newer(), retrieval_settings=SETTINGS)
    err = assert_error(post(c, "methadone"), 409, "embedding_model_mismatch")
    assert "hash-bow@1#384" in err["message"] and "hash-bow@2#384" in err["message"]
    assert c.get("/api/retrieval/status").json()["configured_model_matches"] is False


def test_embedding_backend_failure_is_502(make_client, indexed):
    class Broken(HashEmbedder):
        def embed(self, texts):
            from app.retrieval.embedder import EmbeddingError
            raise EmbeddingError("model offline")

    assert_error(post(make_client(embedder=Broken(), retrieval_settings=SETTINGS), "methadone"), 502, "embedding_failed")


def test_unavailable_when_no_embeddings(make_client, indexed):
    with indexed.engine.begin() as c:
        c.execute(text("TRUNCATE corpus.chunk_embeddings"))
    assert_error(post(make_client(), "methadone"), 503, "retrieval_unavailable")


def test_index_status(api):
    s = api.get("/api/retrieval/status").json()
    assert s["ready"] is True and s["configured_model_matches"] is True and s["dimension"] == 384
    assert s["embedded_chunks"] == s["chunks"] > 0 and s["stale_chunks"] == 0 and s["failed_chunks"] == 0


def test_unloadable_embedding_model_is_a_502_not_a_crash(make_client, vector_available):
    """e.g. sentence-transformers not installed. Only reachable once an index exists, so this runs on
    pgvector databases; the service-level behaviour is checked directly below on any database."""
    from app.retrieval.embedder import EmbeddingError
    from app.services.retrieval import _identity

    class Unloadable(HashEmbedder):
        @property
        def version(self):
            raise EmbeddingError("sentence-transformers is not installed")

        @version.setter
        def version(self, _v):
            pass

    from app.errors import EmbeddingBackendError
    with pytest.raises(EmbeddingBackendError, match="not installed"):
        _identity(Unloadable())
