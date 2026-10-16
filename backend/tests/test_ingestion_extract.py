"""PDF extraction and chunking. No database required."""
import re

import pytest

from app.ingestion.chunking import ChunkingConfig, chunk_paragraphs, count_tokens
from app.ingestion.pdf_extract import ExtractionError, Paragraph, extract
from tests.pdf_factory import body_text, make_blank_pdf, make_encrypted_pdf, make_scanned_pdf, make_text_pdf

SECTIONS = [
    ("Introduction", [body_text(12, "A"), body_text(10, "B")]),
    ("Methods", [body_text(14, "C")]),
    ("Results", [body_text(12, "D"), body_text(8, "E")]),
    ("Discussion", [body_text(10, "F")]),
]


# ---------- extraction ----------

def test_headings_from_font_styles_and_pages(tmp_path):
    r = extract(make_text_pdf(tmp_path / "a.pdf", SECTIONS))
    assert r.heading_source == "font_styles"
    assert [h.title for h in r.outline] == ["Introduction", "Methods", "Results", "Discussion"]
    assert [h.page for h in r.outline] == [1, 2, 3, 4]
    by_section = {p.section_path: p for p in r.paragraphs}
    assert by_section[("Methods",)].page_start == 2
    assert all(p.section_path for p in r.paragraphs)


def test_headings_from_pdf_outline(tmp_path):
    r = extract(make_text_pdf(tmp_path / "a.pdf", SECTIONS, outline=True))
    assert r.heading_source == "pdf_outline"
    assert ("Results",) in {p.section_path for p in r.paragraphs}


def test_repeated_footer_and_page_numbers_removed(tmp_path):
    r = extract(make_text_pdf(tmp_path / "a.pdf", SECTIONS, footer="Running Footer Line 2024"))
    text = " ".join(p.text for p in r.paragraphs)
    assert "Running Footer" not in text
    assert not any(re.fullmatch(r"\d+", p.text) for p in r.paragraphs)


def test_paragraph_text_is_complete(tmp_path):
    r = extract(make_text_pdf(tmp_path / "a.pdf", SECTIONS))
    text = " ".join(p.text for p in r.paragraphs)
    for tag, n in [("A", 12), ("C", 14), ("F", 10)]:
        assert f"{tag}Sentence {n - 1} of the test body" in text


def test_metadata_patterns_found_in_front_matter(tmp_path):
    front = ("Copyright © 2021 Example Publishing. ISBN 978-1-23-456789-7. "
             "Available at doi: 10.1234/example.5678. " + body_text(5, "Z"))
    r = extract(make_text_pdf(tmp_path / "a.pdf", SECTIONS, front_matter=front))
    assert r.metadata.doi == "10.1234/example.5678"
    assert r.metadata.isbn == "978-1-23-456789-7"
    assert 2021 in r.metadata.year_candidates


def test_topic_mentions_are_counted_not_guessed(tmp_path):
    r = extract(make_text_pdf(tmp_path / "a.pdf", [("Alcohol", [body_text(5) + " alcohol alcohol cannabis"])] * 3))
    assert r.metadata.topic_mentions["alcohol"] >= 6
    assert r.metadata.topic_mentions["gambling"] == 0


def test_scanned_pdf_needs_ocr(tmp_path):
    with pytest.raises(ExtractionError) as exc:
        extract(make_scanned_pdf(tmp_path / "scan.pdf"))
    assert exc.value.code == "needs_ocr"


def test_blank_pdf_is_empty(tmp_path):
    with pytest.raises(ExtractionError) as exc:
        extract(make_blank_pdf(tmp_path / "blank.pdf"))
    assert exc.value.code == "empty_document"


def test_corrupt_file_is_unreadable(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7\n this is not really a pdf \n%%EOF")
    with pytest.raises(ExtractionError) as exc:
        extract(bad)
    assert exc.value.code in {"unreadable_pdf", "empty_document"}


def test_encrypted_pdf_rejected(tmp_path):
    with pytest.raises(ExtractionError) as exc:
        extract(make_encrypted_pdf(tmp_path / "locked.pdf"))
    assert exc.value.code == "encrypted"


# ---------- chunking ----------

def paras(n_paras: int, sentences: int, section=("S",), page=1):
    return [Paragraph(body_text(sentences, f"P{i}-"), page + i // 3, page + i // 3, section) for i in range(n_paras)]


CFG = ChunkingConfig(target_tokens=550, min_tokens=400, max_tokens=700, overlap_tokens=80)


def test_chunks_stay_within_bounds():
    chunks = chunk_paragraphs(paras(40, 6), CFG)
    assert len(chunks) > 3
    assert all(c.token_count <= CFG.max_tokens + CFG.overlap_tokens for c in chunks)
    assert all(c.token_count >= CFG.min_tokens for c in chunks[:-1])


def test_paragraph_boundaries_preserved():
    source = paras(20, 5)
    chunks = chunk_paragraphs(source, CFG)
    texts = {p.text for p in source}
    for c in chunks:
        for part in c.text.split("\n\n"):
            # every piece is a whole paragraph, or an overlap tail (a suffix of one)
            assert part in texts or any(t.endswith(part) for t in texts)


def test_overlap_within_section():
    chunks = chunk_paragraphs(paras(20, 6), CFG)
    first_tail = chunks[0].text.split("\n\n")[-1].split(". ")[-1]
    assert first_tail.rstrip(".") in chunks[1].text


def test_no_overlap_across_top_level_sections():
    a = paras(6, 6, section=("Chapter A",))
    b = paras(6, 6, section=("Chapter B",), page=10)
    chunks = chunk_paragraphs(a + b, CFG)
    b_chunks = [c for c in chunks if c.section_path == ("Chapter B",)]
    assert b_chunks and "P5-" not in b_chunks[0].text.split("\n\n")[0][:5]
    assert all(c.section_path in {("Chapter A",), ("Chapter B",)} for c in chunks)


def test_multi_section_chunk_uses_shared_path_and_keeps_headings():
    small = [Paragraph(body_text(4, f"{t}-"), 1, 1, ("Chapter", t)) for t in ("Remarks", "Gaps", "Notes")]
    chunks = chunk_paragraphs(small, CFG)
    assert len(chunks) == 1
    assert chunks[0].section_path == ("Chapter",)
    for t in ("Remarks", "Gaps", "Notes"):
        assert f"\n\n{t}\n\n" in "\n\n" + chunks[0].text


def test_long_paragraph_split_at_sentences():
    long = [Paragraph(body_text(150), 3, 4, ("S",))]
    chunks = chunk_paragraphs(long, CFG)
    assert len(chunks) >= 2
    assert all(c.text.rstrip().endswith(".") for c in chunks)
    assert chunks[0].page_start == 3 and chunks[-1].page_end == 4


def test_chunking_is_deterministic():
    a = chunk_paragraphs(paras(30, 6), CFG)
    b = chunk_paragraphs(paras(30, 6), CFG)
    assert [(c.ordinal, c.content_hash) for c in a] == [(c.ordinal, c.content_hash) for c in b]


def test_page_ranges_cover_source_paragraphs():
    chunks = chunk_paragraphs(paras(30, 6), CFG)
    assert chunks[0].page_start == 1
    assert all(c.page_start <= c.page_end for c in chunks)


@pytest.mark.parametrize("kwargs", [
    {"target_tokens": 300, "min_tokens": 400, "max_tokens": 700, "overlap_tokens": 80},
    {"target_tokens": 550, "min_tokens": 400, "max_tokens": 500, "overlap_tokens": 80},
    {"target_tokens": 550, "min_tokens": 400, "max_tokens": 700, "overlap_tokens": 250},
])
def test_invalid_chunking_config_rejected(kwargs):
    with pytest.raises(ValueError):
        ChunkingConfig(**kwargs)


def test_token_estimate():
    assert count_tokens("Hello, world.") == 4
