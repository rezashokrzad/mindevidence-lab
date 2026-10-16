"""Command line for embeddings and retrieval (no LLM involved).

    python -m app.retrieval status                      # index state
    python -m app.retrieval embed [--rebuild] [--only DOC_ID ...]
    python -m app.retrieval search "how long should treatment last?" -k 5 [--topic opioids] [--doc NIDA-PDAT]
    python -m app.retrieval eval [--validate]           # Recall@k / MRR over data/eval/retrieval_queries.json

Settings come from environment variables (see backend/.env.example); EMBEDDING_BACKEND=hash
uses a non-semantic test embedder and must not be used to judge retrieval quality.
Exit codes: 0 ok, 1 failures (e.g. chunks that could not be embedded), 2 cannot run.
"""
from __future__ import annotations

import argparse
import logging
import sys
import textwrap
from pathlib import Path

from sqlalchemy import create_engine, text

from app.config import database_url
from app.database import build_session_factory
from app.ingestion.config import REPO_ROOT
from app.ingestion.pipeline import IngestionBusy
from app.retrieval import evaluate as ev
from app.retrieval.config import RetrievalSettings
from app.retrieval.embedder import EmbeddingError, build_embedder, model_id
from app.retrieval.indexer import ModelMismatch, embed_corpus
from app.schemas.retrieval import SearchFilters, SearchRequest

EVAL_FILE = REPO_ROOT / "data" / "eval" / "retrieval_queries.json"


def _factory():
    return build_session_factory(create_engine(database_url(), pool_pre_ping=True))


def cmd_status(args, settings, embedder) -> int:
    from app.services.retrieval import index_status

    with _factory()() as db:
        st = index_status(db, embedder)
    print(f"configured model: {settings.embedding_model_name} (backend {settings.embedding_backend})")
    for k, v in st.model_dump().items():
        print(f"  {k}: {v}")
    return 0 if st.ready else 1


def cmd_embed(args, settings, embedder) -> int:
    try:
        report = embed_corpus(_factory(), embedder, settings, rebuild=args.rebuild, document_ids=args.only)
    except (ModelMismatch, IngestionBusy, EmbeddingError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"model: {report.model_id}{' (rebuilt)' if report.rebuilt else ''}")
    print(f"embedded {report.embedded_chunks} chunks ({report.windows} windows); skipped {report.skipped_chunks} up to date")
    for chunk_id, error in report.failed:
        print(f"  ✗ {chunk_id}: {error}", file=sys.stderr)
    if report.failed:
        print(f"{len(report.failed)} chunk(s) failed; recorded in corpus.embedding_failures; rerun to retry.", file=sys.stderr)
    return 0 if report.ok else 1


def cmd_search(args, settings, embedder) -> int:
    from app.errors import AppError
    from app.services.retrieval import run_search

    filters = SearchFilters(document_ids=args.doc, topics=args.topic) if (args.doc or args.topic) else None
    body = SearchRequest(query=args.query, top_k=args.k, min_similarity=args.min_similarity, filters=filters)
    try:
        with _factory()() as db:
            resp = run_search(db, embedder, settings, body)
    except AppError as exc:
        print(f"error ({exc.code}): {exc.message}", file=sys.stderr)
        return 2
    s = resp.settings
    print(f"query: {resp.query}\nmodel: {s.embedding_model} {s.embedding_model_version} · {s.metric} · "
          f"{s.search_type} · top_k={s.top_k} · min_similarity={s.min_similarity} · filters={s.filters}\n")
    for r in resp.results:
        print(f"#{r.rank}  {r.chunk_id}  similarity {r.similarity:.3f}  (distance {r.distance:.3f})")
        print(f"    {r.location}")
        print(textwrap.indent(textwrap.fill(r.matched_passage, 100), "    "))
        print()
    if not resp.results:
        print("No chunks passed the filters / similarity cut-off.")
    print(resp.note)
    return 0


def _load_chunks() -> list[dict]:
    with _factory()() as db:
        rows = db.execute(text("SELECT chunk_id, document_id, text FROM corpus.chunks")).mappings().all()
    return [dict(r) for r in rows]


def cmd_eval(args, settings, embedder) -> int:
    from app.errors import AppError
    from app.services.retrieval import run_search

    cases = ev.load_cases(Path(args.file))
    chunks = _load_chunks()
    if not chunks:
        print("error: no chunks in the database; run ingestion first", file=sys.stderr)
        return 2
    counts = {c.id: ev.count_relevant(c, chunks) for c in cases}
    empty = [cid for cid, n in counts.items() if n == 0]
    if args.validate:
        for c in cases:
            print(f"{c.id}: {counts[c.id]} relevant chunk(s) for terms {c.expected_terms} in {c.expected_documents}")
        if empty:
            print(f"error: no relevant chunk exists for {empty}; fix the relevance rules", file=sys.stderr)
        return 1 if empty else 0
    if empty:
        print(f"error: no relevant chunk exists for {empty}; run with --validate", file=sys.stderr)
        return 2

    def search(query: str, k: int) -> list[dict]:
        with _factory()() as db:
            resp = run_search(db, embedder, settings, SearchRequest(query=query, top_k=k))
        return [{"chunk_id": r.chunk_id, "document_id": r.document.document_id, "text": r.text,
                 "similarity": r.similarity} for r in resp.results]

    try:
        outcomes, summary = ev.evaluate(cases, search, chunks, max_k=min(max(ev.KS), settings.retrieval_max_top_k))
    except AppError as exc:
        print(f"error ({exc.code}): {exc.message}", file=sys.stderr)
        return 2
    label = f"{embedder.name} {embedder.version}"
    if settings.embedding_backend == "hash":
        label += "  [NON-SEMANTIC test embedder: these numbers say nothing about real retrieval quality]"
    print(ev.format_report(outcomes, summary, label))
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m app.retrieval", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    emb = sub.add_parser("embed")
    emb.add_argument("--rebuild", action="store_true", help="delete all embeddings first (required to change model)")
    emb.add_argument("--only", nargs="+", metavar="DOCUMENT_ID")
    se = sub.add_parser("search")
    se.add_argument("query")
    se.add_argument("-k", type=int, default=None)
    se.add_argument("--min-similarity", type=float, default=None)
    se.add_argument("--doc", nargs="+", metavar="DOCUMENT_ID")
    se.add_argument("--topic", nargs="+", choices=["alcohol", "cannabis", "opioids", "stimulants", "behavioral"])
    evl = sub.add_parser("eval")
    evl.add_argument("--file", default=str(EVAL_FILE))
    evl.add_argument("--validate", action="store_true", help="only check that each query has relevant chunks")
    args = parser.parse_args(argv)

    settings = RetrievalSettings()
    embedder = build_embedder(settings)
    try:
        return {"status": cmd_status, "embed": cmd_embed, "search": cmd_search, "eval": cmd_eval}[args.cmd](
            args, settings, embedder)
    except EmbeddingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
