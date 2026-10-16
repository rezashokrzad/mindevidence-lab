"""Split a chunk into overlapping windows that fit the embedding model's input limit."""
from __future__ import annotations

import re
from dataclasses import dataclass

_SENTENCE = re.compile(r"(?<=[.!?;:])\s+|\n{2,}")


@dataclass(frozen=True)
class Window:
    number: int        # 1-based
    start: int         # character offset into the chunk text
    end: int
    text: str          # chunk_text[start:end]


def split_windows(text: str, max_words: int, overlap_words: int) -> list[Window]:
    """Greedy sentence packing. Each window ends on a sentence boundary where possible and
    repeats the last sentences of the previous window up to `overlap_words`."""
    spans, pos = [], 0
    for m in _SENTENCE.finditer(text):
        if m.start() > pos:
            spans.append((pos, m.start()))
        pos = m.end()
    if pos < len(text):
        spans.append((pos, len(text)))

    # A sentence longer than the window (tables, lists) is cut by words.
    pieces: list[tuple[int, int, int]] = []        # (start, end, words)
    for s, e in spans:
        words = list(re.finditer(r"\S+", text[s:e]))
        if len(words) <= max_words:
            pieces.append((s, e, len(words)))
        else:
            for i in range(0, len(words), max_words):
                chunk = words[i:i + max_words]
                pieces.append((s + chunk[0].start(), s + chunk[-1].end(), len(chunk)))

    windows: list[Window] = []
    i = 0
    while i < len(pieces):
        j, total = i, 0
        while j < len(pieces) and (total + pieces[j][2] <= max_words or j == i):
            total += pieces[j][2]
            j += 1
        start, end = pieces[i][0], pieces[j - 1][1]
        windows.append(Window(len(windows) + 1, start, end, text[start:end]))
        if j >= len(pieces):
            break
        back, carried = j, 0                       # step back to overlap
        while back > i + 1 and carried + pieces[back - 1][2] <= overlap_words:
            back -= 1
            carried += pieces[back][2]
        i = max(back, i + 1)
    return windows
