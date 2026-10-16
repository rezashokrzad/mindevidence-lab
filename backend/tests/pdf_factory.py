"""Generate small PDFs for ingestion tests (PyMuPDF). All text here is invented filler."""
from __future__ import annotations

from pathlib import Path

import fitz

BODY = ("Treatment programmes in this synthetic test document describe general procedures. "
        "Each sentence is filler text used only to exercise the extractor and the chunker. ")


def body_text(sentences: int, tag: str = "") -> str:
    return " ".join(f"{tag}Sentence {i} of the test body explains a generic procedure step." for i in range(sentences))


def _write_wrapped(page: fitz.Page, text: str, y: float, *, size: float = 10, font: str = "helv",
                   width_chars: int = 95) -> float:
    words, line = text.split(), ""
    for w in words:
        if len(line) + len(w) + 1 > width_chars:
            page.insert_text((60, y), line, fontsize=size, fontname=font)
            y += size * 1.4
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        page.insert_text((60, y), line, fontsize=size, fontname=font)
        y += size * 1.4
    return y + size * 0.8


def make_text_pdf(path: Path, sections: list[tuple[str, list[str]]], *, footer: str | None = "Test Report 2024",
                  outline: bool = False, front_matter: str | None = None) -> Path:
    """One page per section: a bold 14 pt heading followed by 10 pt paragraphs."""
    doc = fitz.open()
    toc = []
    if front_matter:
        page = doc.new_page()
        _write_wrapped(page, front_matter, 80)
        if footer:
            page.insert_text((60, 820), footer, fontsize=8)
    for title, paragraphs in sections:
        page = doc.new_page()
        page.insert_text((60, 70), title, fontsize=14, fontname="hebo")
        toc.append([1, title, doc.page_count])
        y = 100
        for para in paragraphs:
            y = _write_wrapped(page, para, y)
        if footer:
            page.insert_text((60, 820), footer, fontsize=8)
            page.insert_text((520, 820), str(doc.page_count), fontsize=8)
    if outline:
        doc.set_toc(toc)
    doc.save(path)
    doc.close()
    return path


def make_scanned_pdf(path: Path, pages: int = 3) -> Path:
    """Pages that contain only an image (what a scan without OCR looks like)."""
    doc = fitz.open()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 260), False)
    pix.clear_with(200)
    png = pix.tobytes("png")
    for _ in range(pages):
        page = doc.new_page()
        page.insert_image(fitz.Rect(50, 50, 550, 750), stream=png)
    doc.save(path)
    doc.close()
    return path


def make_blank_pdf(path: Path) -> Path:
    doc = fitz.open()
    doc.new_page()
    doc.save(path)
    doc.close()
    return path


def make_encrypted_pdf(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 80), "Secret text " * 20, fontsize=10)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="user-pw", owner_pw="owner-pw")
    doc.close()
    return path
