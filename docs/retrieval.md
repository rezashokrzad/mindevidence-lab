# Semantic Retrieval (Batch 06)

Retrieval finds the passages of the research documents that are closest in meaning to a question. It uses PostgreSQL with the **pgvector** extension. **No LLM is involved**: students see the retrieved text and its source before any answer exists.

## Status: what was tested

Everything below ran on PostgreSQL 16 with pgvector (Docker image `pgvector/pgvector:pg16`) and the real model
`sentence-transformers/all-MiniLM-L6-v2` (revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, sentence-transformers 5.7.0, CPU).

| Part | Result |
| --- | --- |
| Migration `0005`, vector storage, exact cosine SQL, filters, determinism, model-mixing guards, failure handling | **Tested**: 18 database tests pass |
| Embedding the real corpus | **Done**: 360 chunks → 1,175 windows in about 37 s on CPU; 0 failed, 0 stale |
| Search over the real corpus | **Works**: e.g. "How long should drug addiction treatment last?" returns the NIDA principle on treatment duration at rank 1 (similarity 0.816) |
| Retrieval evaluation (12 hand-made queries) | **Run once**, results below |

### Evaluation results (first run, real model)

| k | Passage hit@k | Passage recall@k | Document hit@k |
| --- | --- | --- | --- |
| 1 | 0.67 | 0.67 | 1.00 |
| 3 | 0.75 | 0.47 | 1.00 |
| 5 | 0.75 | 0.45 | 1.00 |
| 10 | 0.83 | 0.52 | 1.00 |

MRR 0.719. Mean similarity: top-1 0.693; relevant results 0.645; other results 0.614.

How to read this honestly:

- **Document level is strong**: for all 12 queries the expected document appeared in the top 1. **Passage level is weaker**: 8 of 12 queries had a relevant passage at rank 1, and 10 of 12 within the top 10.
- **Q08 is a real miss.** The question asks for the Cochrane authors' conclusions, and the expected chunk (the end of the abstract) was not retrieved in the top 10. The generic phrase "authors' conclusions" carries little meaning in embedding space.
- **Q11 is mostly a rule problem.** The WHO passage on how evidence is graded (`WHO-GL-2023-c0029`, section 2.3) was retrieved at rank 1 but was not counted, because it lacks one of the required words (`certainty of evidence`). The substring rule is stricter than a human reader. The rules were deliberately **not** loosened after seeing the results, so the numbers are not flattered. A better evaluation set would use human-judged relevance.
- **Similarity scores barely separate relevant from non-relevant results** (means 0.645 vs 0.614). That is why no similarity threshold is applied by default: a cut-off would discard relevant passages along with the noise.
- Twelve queries is too small for firm conclusions. Treat these numbers as a regression baseline, not as the system's accuracy.

## Setup

1. **Install pgvector** for your PostgreSQL version ([instructions](https://github.com/pgvector/pgvector#installation)), then once per database, as a superuser:
   ```sql
   CREATE EXTENSION vector;
   ```
   Migration `0005` checks for the extension and stops with a clear message if it is missing, before changing anything.
2. **Install the embedding model package** (large: it includes PyTorch). The model weights (about 90 MB) download from Hugging Face on first use:
   ```bash
   .venv/Scripts/python -m pip install -r requirements-embeddings.txt
   ```
3. From `backend/`:
   ```bash
   .venv/Scripts/alembic upgrade head
   ```
   ```bash
   .venv/Scripts/python -m app.retrieval embed
   ```
   ```bash
   .venv/Scripts/python -m app.retrieval search "How long should treatment last?" -k 5
   ```
   ```bash
   .venv/Scripts/python -m app.retrieval eval
   ```

CLI commands: `status`, `embed [--rebuild] [--only DOC_ID …]`, `search "…" [-k N] [--topic …] [--doc …] [--min-similarity X]`, `eval [--validate]`.

For development without PyTorch, set `EMBEDDING_BACKEND=hash`. This uses a deterministic bag-of-words embedder that is **not semantic**. It exists to test the plumbing, not to judge quality.

## How it works

### Embedding

- **Model:** `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions, English). It is configurable behind an `Embedder` interface (`app/retrieval/embedder.py`).
- **Windows:** this model reads only about 256 word-piece tokens, but chunks target about 550. Embedding a whole chunk would ignore its second half. So each chunk is split into overlapping windows of at most 150 words (30 words of overlap, on sentence boundaries). Each window is embedded, with the section heading prepended as context.
- **Scoring:** a chunk's score is its **best window** (minimum cosine distance). The best window is returned too (`matched_passage`, with character offsets into the chunk text).
- **Identity stored with the vectors:** `corpus.embedding_models` holds the exact model name, version (`rev-<commit>;st-<library version>`), dimension, metric, and window settings. Pin the Hugging Face revision with `EMBEDDING_MODEL_REVISION` for a reproducible index.
- **Batching and retries:** batches of 32 chunks. A failing batch is retried with exponential backoff (default 3 retries), then retried chunk by chunk so one bad chunk cannot block the rest. Chunks that still fail are listed in the report and recorded in `corpus.embedding_failures`. The next run retries them. Output is validated (count, dimension, NaN/∞) before it reaches the database.
- **Idempotent:** chunks whose stored embeddings match the current text hash and model are skipped.
- **Stale vectors cannot survive:** embeddings reference chunks with `ON DELETE CASCADE`. When ingestion replaces a document's chunks, their vectors disappear, and `embed` fills them in again.

### Preventing mixed models and dimensions

- The column is `vector(384)`: PostgreSQL rejects any other dimension. Changing the dimension needs a new migration and a full re-embed.
- Only one model may be **active**. A trigger rejects an embedding row from any other model, so two models can never share an index.
- `embed` refuses to run if embeddings from a *different* model or version exist, unless you pass `--rebuild` (which deletes all embeddings first).
- Search refuses with **409 `embedding_model_mismatch`** when the configured model differs from the one that built the index, instead of comparing vectors from different spaces.

### Search

`POST /api/retrieval/search` (sign-in and CSRF token required):

```json
{"query": "How long should drug addiction treatment last?", "top_k": 5,
 "min_similarity": null, "filters": {"document_ids": ["NIDA-PDAT"], "document_types": ["guideline"], "topics": ["opioids"]}}
```

- **Result:** each hit has `rank`, `chunk_id`, `document` (id, title, year, type), `section_path`, PDF pages and printed page labels, a readable `location`, the full chunk `text`, the `matched_passage`, `distance`, `similarity` (= 1 − distance), `content_hash`, and `token_count`.
- **Settings are echoed** in every response: model, version, dimension, metric, search type, `top_k` (and its cap), `min_similarity`, filters, tie-break rule.
- **Top-k:** default 5, capped at `RETRIEVAL_MAX_TOP_K` (20).
- **Filters:** `document_ids`, `document_types`, and `topics`. Topics are **lexical filters** (a chunk matches when its text mentions the topic's terms, e.g. "opioid", "heroin", "methadone" for opioids). They are keyword filters, not curated labels.
- **Determinism:** the search is exact, with ties broken by `chunk_id`. The same index and query give the same ordered results. (This relies on the same model producing the same query vector; floating-point results can differ slightly between hardware or library versions.)
- **Errors:** 503 `retrieval_unavailable` (no pgvector tables or no embeddings), 409 `embedding_model_mismatch`, 502 `embedding_failed`, 422 for invalid input.
- `GET /api/retrieval/status` reports index readiness (chunks, embedded, windows, stale, failed, whether the configured model matches).

### Index choice: exact search, no ANN index

The corpus has about 360 chunks and 1,175 windows. A sequential scan over a thousand 384-dimensional vectors takes a few milliseconds and returns exact, deterministic results. An approximate index (HNSW or IVFFlat) adds build parameters, approximate recall, and result variability for no benefit at this size. **Revisit above roughly 50,000–100,000 windows** or when query latency matters: add an HNSW index with `vector_cosine_ops`, and re-run the evaluation to confirm recall did not drop.

## Evaluation

`data/eval/retrieval_queries.json` holds 12 queries. A retrieved chunk counts as **relevant** when it belongs to an expected document **and** contains every expected term. The rules were written by hand from the source PDFs, and `eval --validate` confirms that each query has at least one relevant stored chunk. Each query has between 1 and 18 relevant chunks; the report prints that count so a loose rule is visible.

`python -m app.retrieval eval` prints, for k = 1, 3, 5, 10:

- **Passage hit@k**: the share of queries with a relevant chunk in the top k (this is "Recall@k" in the single-answer sense);
- **Passage recall@k**: relevant chunks found in the top k, out of min(k, total relevant);
- **Document hit@k**: the share of queries that retrieved a chunk from an expected document;
- **MRR**: the mean reciprocal rank of the first relevant chunk.

**Limitations of this evaluation:** the set is small and hand-made, so it catches regressions and gross failures. It is not a benchmark of general retrieval quality. Substring relevance rules can miss relevant passages that use different wording, and can count a passage that merely mentions the terms. Do not report these numbers as the system's accuracy.

### Similarity scores are not probabilities

A cosine similarity measures how close two texts are *in the embedding model's space*. It is not the probability that a passage is correct, relevant, or supports a claim:

- the same score can mean different things for different questions, documents, and models;
- scores are not calibrated: 0.6 does not mean "60% likely to be right";
- a short or generic question can score high against irrelevant text, and a well-phrased question can score low against the right passage;
- a passage can be on-topic yet contradict, qualify, or not apply to the case at hand.

For that reason the API and UI show the full text and its source, label scores as "similarity, not probability", and no similarity cut-off is applied by default (`min_similarity` is opt-in). A threshold chosen from the evaluation set would be a noise filter that must be re-validated whenever the model, corpus, or chunking changes. Students are expected to read the passage.

## Known limitations

- The answer text in the UI is still a placeholder until Batch 07; only the retrieved evidence is real.
- Chunk text is research-document text, treated as untrusted data (it is displayed as plain text and never executed or interpreted as instructions).
- Tables extracted as running text embed poorly. Cochrane's study-characteristics tables are a likely source of low-quality hits.
- The behavioural-addiction questions (gambling, gaming) have almost no support in the corpus. Retrieval will still return the *closest* chunks, which may be unrelated, which is why the scores and text must be inspected (see [corpus.md](corpus.md)).
