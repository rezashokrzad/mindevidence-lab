"""Paragraph-aware chunking.

Chunks are built from whole paragraphs whenever possible:
* a chunk closes once it reaches the target size, before it would exceed the maximum,
  at a section boundary once it has reached the minimum, and always at a top-level
  section (chapter) boundary;
* when a chunk spans several subsections, its section path is the path they share, and
  each subsection's heading is kept as a line in the text;
* a paragraph larger than the target is split at sentence boundaries;
* consecutive chunks in the same section overlap by up to `overlap_tokens` of trailing
  sentences, so a statement cut by a boundary appears whole in one of them;
* no overlap is carried across section boundaries.

Token counts are an ESTIMATE: words plus punctuation marks (`\\w+|[^\\w\\s]`). This tracks
BPE/WordPiece tokenizers roughly but not exactly; long technical words count as one
token here and several in a model tokenizer.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass

from app.ingestion.pdf_extract import Paragraph

_TOKEN = re.compile(r"\w+|[^\w\s]")
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[\"'(\[“]?[A-Z0-9•])")


@dataclass(frozen=True)
class ChunkingConfig:
    target_tokens: int = 550
    min_tokens: int = 400
    max_tokens: int = 700
    overlap_tokens: int = 80

    def __post_init__(self):
        if not (0 < self.min_tokens <= self.target_tokens <= self.max_tokens):
            raise ValueError("chunk sizes must satisfy 0 < min <= target <= max")
        if not (0 <= self.overlap_tokens < self.min_tokens // 2):
            raise ValueError("overlap must be smaller than half the minimum chunk size")

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Chunk:
    ordinal: int
    text: str
    section_path: tuple[str, ...]
    page_start: int
    page_end: int
    token_count: int

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass
class _Unit:
    text: str
    page_start: int
    page_end: int
    section_path: tuple[str, ...]
    tokens: int
    overlap: bool = False


def count_tokens(text: str) -> int:
    return len(_TOKEN.findall(text))


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text) if s.strip()]


def _pack(pieces: list[str], limit: int) -> list[str]:
    """Greedily pack pieces (sentences or words) into groups of at most `limit` tokens."""
    groups, cur, cur_n = [], [], 0
    for piece in pieces:
        n = count_tokens(piece)
        if cur and cur_n + n > limit:
            groups.append(" ".join(cur))
            cur, cur_n = [], 0
        cur.append(piece)
        cur_n += n
    if cur:
        groups.append(" ".join(cur))
    return groups


def _units(paragraphs: list[Paragraph], cfg: ChunkingConfig) -> list[_Unit]:
    units = []
    for p in paragraphs:
        n = count_tokens(p.text)
        if n <= cfg.target_tokens:
            units.append(_Unit(p.text, p.page_start, p.page_end, p.section_path, n))
            continue
        sentences = []
        for s in split_sentences(p.text):
            # A single "sentence" longer than the target (tables, lists) is split by words.
            sentences.extend(_pack(s.split(), cfg.target_tokens) if count_tokens(s) > cfg.target_tokens else [s])
        for piece in _pack(sentences, cfg.target_tokens):
            units.append(_Unit(piece, p.page_start, p.page_end, p.section_path, count_tokens(piece)))
    return units


def _overlap_tail(units: list[_Unit], budget: int) -> list[_Unit]:
    """Trailing sentences of the previous chunk, at most `budget` tokens."""
    if budget <= 0 or not units:
        return []
    last = units[-1]
    picked, total = [], 0
    for sentence in reversed(split_sentences(last.text)):
        n = count_tokens(sentence)
        if total + n > budget:
            break
        picked.insert(0, sentence)
        total += n
    if not picked:
        return []
    text = " ".join(picked)
    return [_Unit(text, last.page_end, last.page_end, last.section_path, count_tokens(text), overlap=True)]


def _common_prefix(paths: list[tuple[str, ...]]) -> tuple[str, ...]:
    prefix = paths[0]
    for p in paths[1:]:
        k = 0
        while k < min(len(prefix), len(p)) and prefix[k] == p[k]:
            k += 1
        prefix = prefix[:k]
    return prefix


def chunk_paragraphs(paragraphs: list[Paragraph], cfg: ChunkingConfig) -> list[Chunk]:
    groups: list[list[_Unit]] = []
    cur: list[_Unit] = []

    def size(us: list[_Unit]) -> int:
        return sum(u.tokens for u in us)

    def own(us: list[_Unit]) -> list[_Unit]:
        return [u for u in us if not u.overlap]

    for unit in _units(paragraphs, cfg):
        if own(cur):
            prev_path = own(cur)[-1].section_path
            section_change = unit.section_path != prev_path
            chapter_change = unit.section_path[:1] != prev_path[:1]
            n = size(cur)
            if (n + unit.tokens > cfg.max_tokens or n >= cfg.target_tokens
                    or (section_change and n >= cfg.min_tokens)
                    or (chapter_change and n >= cfg.min_tokens // 4)):
                groups.append(cur)
                cur = [] if section_change else _overlap_tail(own(cur), cfg.overlap_tokens)
        cur.append(unit)
    if own(cur):
        groups.append(cur)

    # A small trailing piece is folded into the previous chunk of the same section when it fits.
    if len(groups) >= 2:
        last, prev = groups[-1], groups[-2]
        last_own = own(last)
        if (size(last_own) < cfg.min_tokens // 4 and last_own[0].section_path == own(prev)[-1].section_path
                and size(prev) + size(last_own) <= cfg.max_tokens):
            prev.extend(last_own)
            groups.pop()

    chunks = []
    for i, group in enumerate(groups, start=1):
        paths = [u.section_path for u in own(group)]
        shared = _common_prefix(paths) or paths[0]
        parts, prev_path = [], None
        for u in group:
            # Inside a multi-section chunk, keep each subsection's own heading for context.
            if (not u.overlap and len(set(paths)) > 1 and u.section_path != prev_path
                    and len(u.section_path) > len(shared)):
                parts.append(u.section_path[-1])
            parts.append(u.text)
            prev_path = u.section_path
        text = "\n\n".join(parts)
        chunks.append(Chunk(
            ordinal=i,
            text=text,
            section_path=shared,
            page_start=min(u.page_start for u in group),
            page_end=max(u.page_end for u in group),
            token_count=count_tokens(text),
        ))
    return chunks
