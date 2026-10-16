# MindEvidence Lab

An **educational** research assistant for psychology students studying addiction treatment. It answers questions about **10 fictional patients** using a small authorized research corpus, with citations, labelled sources and an audit trail. It does not diagnose, prescribe or choose treatment, and it is not for real patients.

Details: [docs/SPEC.md](docs/SPEC.md) · [architecture](docs/architecture.md) · [retrieval and measured quality](docs/retrieval.md) · [corpus](docs/corpus.md) · [data dictionary](docs/data-dictionary.md) · [Google setup](docs/google-oauth-setup.md) · [backend](backend/README.md) · [frontend](frontend/README.md)

## Quick start

1. Start PostgreSQL with pgvector (Docker, port 5440):
   ```bash
   docker run -d --name mindevidence-pg -p 127.0.0.1:5440:5432 -e POSTGRES_PASSWORD=<admin-password> pgvector/pgvector:pg16
   ```
2. Backend: follow [backend/README.md](backend/README.md): create the role and databases, `cp .env.example .env` and fill the secrets, `pip install -r requirements.txt -r requirements-embeddings.txt`, `alembic upgrade head`, seed, `python -m app.ingestion`, then `python -m app.retrieval index`.
3. From `backend/` run `uvicorn app.main:create_app --factory --port 8000`. Serve `frontend/` on port 5173 (`python -m http.server 5173 --directory frontend`).
4. Open http://localhost:5173 and sign in with Google.

## Classroom demo script (about 10 minutes)

| Step | Do | What to point out |
|---|---|---|
| 1 | Sign in, select **P-001**. | Real data from the API; the record is synthetic. |
| 2 | **RAG**: ask what evidence supports contingency management. | The Evidence inspector fills **before** the answer; E-ids open the exact passage with page and section. |
| 3 | **General Knowledge**: ask the same. | No retrieval, no citations, labelled as uncited. |
| 4 | **Both**: ask which research is most relevant to this patient's history. | Patient-record claims are kept apart from research claims. |
| 5 | **Auto**: ask a patient-fact question, then a research one. | Auto searches only when needed; the Answers tab shows whether it did. |
| 6 | Ask "Which drug should I prescribe him?" | Refused without a model call. A question suggesting self-harm gets an urgent-escalation notice. |
| 7 | Ask about gambling or gaming (thin coverage). | Discuss why similarity alone cannot detect thin coverage ([docs/retrieval.md](docs/retrieval.md)). |
| 8 | Open **View trace** and the **Activity** tabs; rate an answer **Not helpful**. | Node timings, chunk ids, model settings; no question text is stored. |
| 9 | Optional: **Email reports**. | Opt-in, counts only, revocable. |

## Honest status

- **Tested automatically:** 543 backend tests pass (2 skipped) against Docker PostgreSQL 16 with pgvector, using fake Google, fake Gmail and a scripted model.
- **Tested manually:** the real embedding model on the real corpus; a few live DeepSeek calls in `rag`, `gk` and `auto` modes (citations valid, the search tool call worked).
- **Retrieval quality (12-query set):** hit@1 0.67, hit@5 0.75, hit@10 0.83, MRR 0.72. The set is small and the similarity floors are heuristics, not calibrated thresholds.
- **Not tested:** a real Google consent round trip with this final build, a real Gmail send (offline fakes only), the signed-in UI against live answers in a browser, load, accessibility audit, and a security review. One document (WHO-IG-2016) has no file in the corpus.
- Answers are model-generated and checked mechanically (citation ids, claim labels). The checks do not prove a claim is true. Verify against the cited passage.
