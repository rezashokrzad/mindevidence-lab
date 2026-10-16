"""Embedding interface, windows, retries, config, schemas, and evaluation metrics. No database."""
import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ingestion.config import REPO_ROOT
from app.retrieval import evaluate as ev
from app.retrieval.config import VECTOR_DIMENSION, RetrievalSettings
from app.retrieval.embedder import (
    EmbeddingError,
    HashEmbedder,
    SentenceTransformerEmbedder,
    build_embedder,
    model_id,
    validate_vectors,
)
from app.retrieval.indexer import _embed_with_retries
from app.retrieval.windows import split_windows
from app.schemas.retrieval import SearchRequest
from tests.pdf_factory import body_text


# ---------- windows ----------

def test_windows_cover_text_and_respect_limit():
    text = " ".join(body_text(8, f"P{i}-") for i in range(10))
    windows = split_windows(text, 60, 15)
    assert len(windows) > 3
    assert all(len(w.text.split()) <= 60 for w in windows)
    assert all(w.text == text[w.start:w.end] for w in windows)
    covered = set()
    for w in windows:
        covered.update(range(w.start, w.end))
    assert all(ch.isspace() or i in covered for i, ch in enumerate(text))


def test_windows_overlap_and_progress():
    text = " ".join(f"Sentence number {i} says something plain." for i in range(60))
    windows = split_windows(text, 40, 12)
    assert all(b.start < a.end for a, b in zip(windows, windows[1:]))          # overlap
    assert all(b.start > a.start for a, b in zip(windows, windows[1:]))        # always moves forward


def test_short_text_is_one_window_and_giant_sentence_is_cut():
    assert len(split_windows("A short sentence.", 150, 30)) == 1
    giant = "word " * 400
    windows = split_windows(giant, 100, 20)
    assert len(windows) == 4 and all(len(w.text.split()) <= 100 for w in windows)


# ---------- embedders ----------

def test_hash_embedder_is_deterministic_normalised_and_sized():
    e = HashEmbedder()
    (a,), (b,) = e.embed(["Naltrexone reduces heavy drinking."]), e.embed(["Naltrexone reduces heavy drinking."])
    assert a == b and len(a) == VECTOR_DIMENSION
    assert math.isclose(sum(x * x for x in a), 1.0, rel_tol=1e-9)
    assert e.embed(["!!!"])[0] != [0.0] * VECTOR_DIMENSION          # never a zero vector


def test_hash_embedder_similarity_follows_shared_words():
    e = HashEmbedder()
    q, close, far = e.embed(["opioid maintenance methadone", "methadone opioid maintenance treatment",
                             "gambling debt football"])
    dot = lambda x, y: sum(i * j for i, j in zip(x, y))
    assert dot(q, close) > dot(q, far)


def test_model_id_contains_name_version_dimension():
    assert model_id(HashEmbedder()) == "hash-bow@1#384"


def test_validate_vectors_rejects_bad_output():
    ok = [[0.0] * 384]
    assert validate_vectors(ok, 1, 384) == ok
    with pytest.raises(EmbeddingError, match="vectors for"):
        validate_vectors(ok, 2, 384)
    with pytest.raises(EmbeddingError, match="dimension 3"):
        validate_vectors([[0.0, 1.0, 2.0]], 1, 384)
    with pytest.raises(EmbeddingError, match="NaN"):
        validate_vectors([[float("nan")] * 384], 1, 384)


def test_sentence_transformer_reports_missing_package(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name.startswith("sentence_transformers"):
            raise ImportError("blocked for test")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(EmbeddingError, match="not installed"):
        SentenceTransformerEmbedder("sentence-transformers/all-MiniLM-L6-v2").embed(["x"])


def test_build_embedder_backends():
    assert isinstance(build_embedder(RetrievalSettings(embedding_backend="hash")), HashEmbedder)
    st = build_embedder(RetrievalSettings())
    assert isinstance(st, SentenceTransformerEmbedder) and st.name == "sentence-transformers/all-MiniLM-L6-v2"


# ---------- retries ----------

class Flaky:
    name, version, dimension = "flaky", "1", 384

    def __init__(self, fail_times, error=RuntimeError("boom")):
        self.fail_times, self.error, self.calls = fail_times, error, 0

    def embed(self, texts):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error
        return HashEmbedder().embed(texts)


def test_retry_recovers_with_exponential_backoff():
    sleeps = []
    s = RetrievalSettings(embedding_backend="hash", embedding_max_retries=3, embedding_retry_backoff_seconds=1.0)
    vectors, attempts, error = _embed_with_retries(Flaky(2), ["a", "b"], s, sleep=sleeps.append)
    assert vectors is not None and attempts == 3 and error == "" and sleeps == [1.0, 2.0]


def test_retry_gives_up_and_reports_error():
    s = RetrievalSettings(embedding_backend="hash", embedding_max_retries=2, embedding_retry_backoff_seconds=0)
    f = Flaky(99)
    vectors, attempts, error = _embed_with_retries(f, ["a"], s, sleep=lambda _: None)
    assert vectors is None and attempts == 3 and f.calls == 3 and "boom" in error


def test_retry_treats_wrong_dimension_as_failure():
    class Wrong(Flaky):
        def embed(self, texts):
            return [[0.5, 0.5]] * len(texts)

    s = RetrievalSettings(embedding_backend="hash", embedding_max_retries=1, embedding_retry_backoff_seconds=0)
    vectors, _, error = _embed_with_retries(Wrong(0), ["a"], s, sleep=lambda _: None)
    assert vectors is None and "dimension 2" in error


# ---------- config and schemas ----------

def test_dimension_cannot_be_changed_without_migration():
    with pytest.raises(ValidationError, match="vector\\(384\\)"):
        RetrievalSettings(embedding_dimension=768)


@pytest.mark.parametrize("kwargs", [{"embedding_window_overlap_words": 100},
                                    {"retrieval_default_top_k": 30, "retrieval_max_top_k": 10}])
def test_invalid_retrieval_settings(kwargs):
    with pytest.raises(ValidationError):
        RetrievalSettings(**kwargs)


@pytest.mark.parametrize("body", [
    {"query": "ab"}, {"query": "x" * 501}, {"query": "valid query", "top_k": 0}, {"query": "valid query", "top_k": 51},
    {"query": "valid query", "min_similarity": 2}, {"query": "valid query", "surprise": 1},
    {"query": "valid query", "filters": {"topics": ["astrology"]}},
    {"query": "valid query", "filters": {"document_ids": ["bad id"]}},
    {"query": "valid query", "filters": {"extra": 1}},
])
def test_search_request_validation(body):
    with pytest.raises(ValidationError):
        SearchRequest(**body)


def test_search_request_strips_query():
    assert SearchRequest(query="  how long?  ").query == "how long?"


# ---------- evaluation metrics ----------

def case(**kw):
    return ev.QueryCase(kw.get("id", "Q"), "q", kw.get("docs", ["D1"]), kw.get("terms", ["alpha"]))


def hit(i, doc, text, sim=0.5):
    return {"chunk_id": f"{doc}-c{i}", "document_id": doc, "text": text, "similarity": sim}


def test_relevance_needs_document_and_all_terms():
    c = case(terms=["alpha", "beta"])
    assert ev.is_relevant(c, "D1", "Alpha and BETA")
    assert not ev.is_relevant(c, "D1", "alpha only")
    assert not ev.is_relevant(c, "D2", "alpha beta")


def test_metrics_on_hand_computed_example():
    c1, c2 = case(id="A"), case(id="B")
    chunks = [hit(1, "D1", "alpha"), hit(2, "D1", "alpha"), hit(3, "D1", "other"), hit(4, "D2", "alpha")]
    results = {
        "A": [hit(3, "D1", "other"), hit(1, "D1", "alpha"), hit(4, "D2", "alpha")],   # first relevant at rank 2
        "B": [hit(4, "D2", "alpha"), hit(3, "D1", "other"), hit(5, "D1", "nothing")],  # nothing relevant
    }
    c1.query, c2.query = "A", "B"
    outcomes, summary = ev.evaluate([c1, c2], lambda q, k: results[q][:k], chunks, max_k=3)
    assert [o.first_relevant_rank for o in outcomes] == [2, None]
    assert [o.total_relevant for o in outcomes] == [2, 2]
    assert summary["per_k"][1]["passage_hit"] == 0.0
    assert summary["per_k"][3]["passage_hit"] == 0.5
    assert summary["per_k"][3]["document_hit"] == 1.0                      # both retrieved a D1 chunk
    assert summary["per_k"][3]["passage_recall"] == pytest.approx(0.25)    # (1/min(3,2) + 0) / 2
    assert summary["mrr"] == pytest.approx(0.25)                           # (1/2 + 0) / 2
    assert "not probabilities" in summary["similarity"]["note"]


def test_report_states_that_similarity_is_not_probability():
    c = case(); c.query = "q"
    outcomes, summary = ev.evaluate([c], lambda q, k: [hit(1, "D1", "alpha", 0.9)], [hit(1, "D1", "alpha")], max_k=3)
    report = ev.format_report(outcomes, summary, "test")
    assert "NOT probabilities" in report and "MRR" in report


# ---------- the shipped evaluation set ----------

def test_eval_queries_file_is_well_formed():
    raw = json.loads((REPO_ROOT / "data" / "eval" / "retrieval_queries.json").read_text(encoding="utf-8"))
    manifest = json.loads((REPO_ROOT / "data" / "corpus_manifest.json").read_text(encoding="utf-8"))
    known = {d["document_id"] for d in manifest["documents"]}
    queries = raw["queries"]
    assert len(queries) >= 10
    assert len({q["id"] for q in queries}) == len(queries)
    for q in queries:
        assert len(q["query"]) >= 3 and q["expected_terms"]
        assert set(q["expected_documents"]) <= known
    cases = ev.load_cases(Path(REPO_ROOT / "data" / "eval" / "retrieval_queries.json"))
    assert len(cases) == len(queries)


def test_empty_env_values_mean_default(monkeypatch):
    """`.env.example` ships `RETRIEVAL_DEFAULT_MIN_SIMILARITY=` (empty = no cut-off); it must not crash startup."""
    monkeypatch.setenv("RETRIEVAL_DEFAULT_MIN_SIMILARITY", "")
    monkeypatch.setenv("EMBEDDING_MODEL_REVISION", "")
    s = RetrievalSettings()
    assert s.retrieval_default_min_similarity is None and s.embedding_model_revision == ""
