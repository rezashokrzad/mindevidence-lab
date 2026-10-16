"""Command-line ingestion.

    python -m app.ingestion run                 # ingest everything in the manifest (idempotent)
    python -m app.ingestion run --force         # re-extract even if nothing changed
    python -m app.ingestion run --only NIDA-PDAT
    python -m app.ingestion check               # validate the manifest and list source files, no DB writes
    python -m app.ingestion show NIDA-PDAT-c0007 # print one chunk with its provenance

Paths and chunk sizes come from environment variables (see backend/.env.example).
Exit code: 0 = succeeded, 1 = partial/failed, 2 = could not run.
"""
import argparse
import logging
import sys

from app.config import database_url
from app.database import build_session_factory
from app.ingestion.config import IngestionSettings
from app.ingestion.manifest import ManifestError, load_manifest
from app.ingestion.pipeline import IngestionBusy, run_ingestion
from app.ingestion.storage import sha256_file


def _session_factory():
    from sqlalchemy import create_engine

    return build_session_factory(create_engine(database_url(), pool_pre_ping=True))


def cmd_run(args, settings: IngestionSettings) -> int:
    try:
        report = run_ingestion(_session_factory(), settings, triggered_by="cli", force=args.force,
                               only=set(args.only) if args.only else None)
    except (ManifestError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except IngestionBusy as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"run {report.run_id}: {report.status}")
    for r in report.results:
        mark = {"error": "✗", "warning": "!", "info": "✓"}[r.severity]
        print(f"  {mark} {(r.document_id or '-'):<14} {r.action:<17} {r.message}")
        for w in r.warnings:
            print(f"      ! {w}")
    return 0 if report.status == "succeeded" else 1


def cmd_check(_args, settings: IngestionSettings) -> int:
    try:
        manifest = load_manifest(settings.corpus_manifest)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"manifest: {settings.corpus_manifest} ({len(manifest.documents)} documents)")
    print(f"source:   {settings.corpus_source_dir}")
    present = {p.name: p for p in settings.corpus_source_dir.glob("*.pdf")} if settings.corpus_source_dir.is_dir() else {}
    ok = True
    for d in manifest.documents:
        p = present.get(d.filename or "")
        if not p:
            print(f"  ! {d.document_id:<14} file missing ({d.filename})")
            continue
        sha = sha256_file(p)
        match = d.sha256 is None or sha == d.sha256
        ok &= match
        print(f"  {'✓' if match else '✗'} {d.document_id:<14} {d.filename} sha256 {'matches' if match else 'MISMATCH'}")
    for name in sorted(set(present) - {d.filename for d in manifest.documents}):
        print(f"  ! unlisted file (will be ignored): {name}")
    return 0 if ok else 1


def cmd_show(args, _settings: IngestionSettings) -> int:
    from app.repositories import documents as repo

    with _session_factory()() as db:
        chunk = repo.get_chunk(db, args.chunk_id)
        if chunk is None:
            print(f"no chunk {args.chunk_id}", file=sys.stderr)
            return 1
        doc = repo.get_document(db, chunk.document_id)
        label = f" (printed {chunk.page_label_start})" if chunk.page_label_start else ""
        print(f"{chunk.chunk_id} · {doc.title}")
        print(f"section: {' > '.join(chunk.section_path) or '(none)'}")
        print(f"pages:   {chunk.page_start}-{chunk.page_end}{label} · {chunk.token_count} tokens · sha256 {chunk.content_hash[:16]}…")
        print("-" * 72)
        print(chunk.text)
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m app.ingestion", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="ingest manifest documents")
    run.add_argument("--force", action="store_true", help="re-extract even when the fingerprint is unchanged")
    run.add_argument("--only", nargs="+", metavar="DOCUMENT_ID", help="limit to these documents")
    sub.add_parser("check", help="validate manifest and files without touching the database")
    show = sub.add_parser("show", help="print one chunk with provenance")
    show.add_argument("chunk_id")
    args = parser.parse_args(argv)
    settings = IngestionSettings()
    return {"run": cmd_run, "check": cmd_check, "show": cmd_show}[args.cmd](args, settings)


if __name__ == "__main__":
    sys.exit(main())
