"""Retrieval evaluation: Recall@k, MRR and document hit-rate over a hand-made query set.

A retrieved chunk is RELEVANT for a query when it belongs to one of the query's expected
documents and its text contains every expected term (case-insensitive). The relevance
rules live in data/eval/retrieval_queries.json.

Metrics (computed per k):
* Passage hit@k   - share of queries with at least one relevant chunk in the top k
                    (this is "Recall@k" in the usual single-answer sense)
* Passage recall@k- mean over queries of (relevant chunks in top k) / min(k, total relevant chunks)
* Document hit@k  - share of queries with a chunk from an expected document in the top k
* MRR             - mean reciprocal rank of the first relevant chunk (0 if none within max k)

Similarity values are reported only as descriptive statistics. They are not probabilities of
correctness, and no threshold derived from them should be read as a correctness guarantee.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

KS = (1, 3, 5, 10)


@dataclass
class QueryCase:
    id: str
    query: str
    expected_documents: list[str]
    expected_terms: list[str]


@dataclass
class QueryOutcome:
    case: QueryCase
    retrieved: list[dict]                  # [{chunk_id, document_id, text, similarity}, ...] best first
    total_relevant: int
    first_relevant_rank: int | None = None
    relevant_ranks: list[int] = field(default_factory=list)


def load_cases(path: Path) -> list[QueryCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))["queries"]
    return [QueryCase(q["id"], q["query"], q["expected_documents"], q["expected_terms"]) for q in raw]


def is_relevant(case: QueryCase, document_id: str, text: str) -> bool:
    body = text.lower()
    return document_id in case.expected_documents and all(t.lower() in body for t in case.expected_terms)


def count_relevant(case: QueryCase, chunks: list[dict]) -> int:
    return sum(is_relevant(case, c["document_id"], c["text"]) for c in chunks)


def evaluate(cases: list[QueryCase], search: Callable[[str, int], list[dict]], all_chunks: list[dict],
             max_k: int = max(KS)) -> tuple[list[QueryOutcome], dict]:
    outcomes = []
    for case in cases:
        hits = search(case.query, max_k)
        ranks = [i for i, h in enumerate(hits, 1) if is_relevant(case, h["document_id"], h["text"])]
        outcomes.append(QueryOutcome(case, hits, count_relevant(case, all_chunks),
                                     ranks[0] if ranks else None, ranks))
    return outcomes, summarize(outcomes)


def summarize(outcomes: list[QueryOutcome]) -> dict:
    n = len(outcomes)
    summary: dict = {"queries": n, "per_k": {}}
    for k in KS:
        passage_hit = sum(1 for o in outcomes if any(r <= k for r in o.relevant_ranks))
        doc_hit = sum(1 for o in outcomes
                      if any(h["document_id"] in o.case.expected_documents for h in o.retrieved[:k]))
        recalls = [sum(1 for r in o.relevant_ranks if r <= k) / max(1, min(k, o.total_relevant)) for o in outcomes]
        summary["per_k"][k] = {"passage_hit": passage_hit / n, "passage_recall": statistics.mean(recalls),
                               "document_hit": doc_hit / n}
    summary["mrr"] = statistics.mean(1 / o.first_relevant_rank if o.first_relevant_rank else 0 for o in outcomes)
    sims = [h["similarity"] for o in outcomes for h in o.retrieved[:1]]
    rel_sims = [h["similarity"] for o in outcomes for i, h in enumerate(o.retrieved, 1) if i in o.relevant_ranks]
    other = [h["similarity"] for o in outcomes for i, h in enumerate(o.retrieved, 1) if i not in o.relevant_ranks]
    summary["similarity"] = {
        "top1_mean": statistics.mean(sims) if sims else None,
        "relevant_mean": statistics.mean(rel_sims) if rel_sims else None,
        "not_relevant_mean": statistics.mean(other) if other else None,
        "note": "Descriptive only. Scores are not probabilities of correctness.",
    }
    return summary


def format_report(outcomes: list[QueryOutcome], summary: dict, label: str) -> str:
    lines = [f"Retrieval evaluation: {label}", f"{summary['queries']} queries; relevant = expected document AND all expected terms", ""]
    lines.append(f"{'id':<5}{'first rel.':>11}{'#relevant':>10}  top-1 chunk (similarity)")
    for o in outcomes:
        top = o.retrieved[0] if o.retrieved else None
        first = o.first_relevant_rank if o.first_relevant_rank else "-"
        lines.append(f"{o.case.id:<5}{first!s:>11}{o.total_relevant:>10}  "
                     + (f"{top['chunk_id']} ({top['similarity']:.3f})" if top else "(none)"))
    lines += ["", f"{'k':<4}{'passage hit@k':>15}{'passage recall@k':>18}{'document hit@k':>16}"]
    for k, m in summary["per_k"].items():
        lines.append(f"{k:<4}{m['passage_hit']:>15.2f}{m['passage_recall']:>18.2f}{m['document_hit']:>16.2f}")
    lines.append(f"MRR: {summary['mrr']:.3f}")
    s = summary["similarity"]
    fmt = lambda v: "n/a" if v is None else f"{v:.3f}"
    lines += ["", f"Mean similarity: top-1 {fmt(s['top1_mean'])}; relevant results {fmt(s['relevant_mean'])}; "
              f"other results {fmt(s['not_relevant_mean'])}",
              "Reminder: similarity scores rank passages; they are NOT probabilities that a passage is correct."]
    return "\n".join(lines)
