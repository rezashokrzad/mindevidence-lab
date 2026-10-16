"""PDF text extraction with PyMuPDF.

Produces body paragraphs that each know their page(s) and section path, plus the
document outline and metadata found in the PDF itself. Nothing here touches the
database or invents data: values the PDF does not contain are left out.

Pipeline per document
1. Open and quality-check (unreadable, encrypted, empty, scanned → explicit errors).
2. Read every line with its font size/weight and position.
3. Remove repeated page furniture (running headers/footers, page numbers). A running
   header that changes between chapters (e.g. WHO's "3. Recommendations") is kept as
   chapter context instead of body text.
4. Find headings: from the PDF outline when one exists, otherwise from font styling.
5. Build paragraphs from text blocks, splitting at bullets and merging paragraphs that
   continue across a page break.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

MIN_PAGE_CHARS = 50            # fewer characters than this = page without usable text
OCR_PAGE_SHARE = 0.20          # more image-only pages than this share = needs OCR
GARBLED_SHARE = 0.02           # more U+FFFD replacement chars than this share = broken text layer
BAND = 0.08                    # top/bottom share of the page searched for headers/footers
BOILERPLATE_SHARE = 0.45       # header text on more pages than this is boilerplate, not chapter context
GLOBAL_REPEAT_SHARE = 0.50     # a line in the outer zone repeated on more pages than this is furniture
FURNITURE_ZONE = 0.15          # outer share of the page (top and bottom) where such repeats are removed
MIN_REPEAT_PAGES = 3

_TERMINAL = tuple('.?!:;"\')]”’')
_DOT_LEADER = re.compile(r"\.{5,}|(?:\. ){4,}")
_PAGE_NUMBER = re.compile(r"^(?:page\s*)?(?:[0-9]{1,4}|[ivxlcdm]{1,7})$", re.I)
_SPACED_LETTERS = re.compile(r"^(?:\S ){4,}\S(?: {2,}(?:\S ){0,}\S)*$")
_BULLET_FONTS = ("wingdings", "symbol", "zapfdingbats", "dingbat")
_BULLET_CHARS = "•▪●◦‣∙-–"
_NUMBERED_HEADING = re.compile(r"^\d+(?:\.\d+)*\.?\s+[A-Z]")


class ExtractionError(Exception):
    """The document cannot be ingested as-is. `code` is stable and shown to users."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Line:
    text: str
    size: float
    bold: bool
    page: int          # 0-based page index
    block: int
    y0: float
    y1: float
    bullet: bool = False
    rotated: bool = False   # vertical text, e.g. footers printed along the edge of landscape pages


@dataclass
class Paragraph:
    text: str
    page_start: int    # 1-based
    page_end: int
    section_path: tuple[str, ...]


@dataclass
class Heading:
    level: int
    title: str
    page: int          # 1-based


@dataclass
class ExtractedMetadata:
    embedded: dict = field(default_factory=dict)
    title_guess: str | None = None
    authors_guess: list[str] = field(default_factory=list)
    doi: str | None = None
    isbn: str | None = None
    year_candidates: list[int] = field(default_factory=list)
    topic_mentions: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "embedded": self.embedded, "title_guess": self.title_guess, "authors_guess": self.authors_guess,
            "doi": self.doi, "isbn": self.isbn, "year_candidates": self.year_candidates,
            "topic_mentions": self.topic_mentions,
        }


@dataclass
class ExtractionResult:
    page_count: int
    page_labels: list[str | None]
    paragraphs: list[Paragraph]
    outline: list[Heading]
    heading_source: str            # "pdf_outline" | "font_styles"
    text_char_count: int
    quality: dict
    metadata: ExtractedMetadata


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).replace("­", "")
    return re.sub(r"[ \t ]+", " ", s).strip()


def _collapse_spaced_letters(s: str) -> str:
    """'P L A I N   L A N G U A G E' → 'PLAIN LANGUAGE'."""
    if _SPACED_LETTERS.match(s):
        return " ".join(w.replace(" ", "") for w in re.split(r" {2,}", s))
    return s


def _key(s: str) -> str:
    return re.sub(r"\d+", "#", s.lower()).strip()


# ---------------------------------------------------------------- open + quality

def open_pdf(path: Path) -> fitz.Document:
    try:
        doc = fitz.open(path)
    except Exception as exc:  # PyMuPDF raises FileDataError / RuntimeError subclasses
        raise ExtractionError("unreadable_pdf", f"PyMuPDF could not open the file: {exc}") from None
    if not doc.is_pdf:
        raise ExtractionError("unreadable_pdf", "file is not a PDF")
    if doc.needs_pass:
        raise ExtractionError("encrypted", "PDF is password-protected")
    if doc.page_count == 0:
        raise ExtractionError("empty_document", "PDF has no pages")
    return doc


def assess_quality(doc: fitz.Document) -> dict:
    """Raise ExtractionError when the text layer is missing or unusable."""
    chars_per_page, image_only, total, replacement = [], [], 0, 0
    for i, page in enumerate(doc):
        text = page.get_text()
        n = len(text.strip())
        chars_per_page.append(n)
        total += n
        replacement += text.count("�")
        if n < MIN_PAGE_CHARS and page.get_images(full=True):
            image_only.append(i + 1)
    quality = {
        "pages": doc.page_count,
        "pages_with_text": sum(1 for n in chars_per_page if n >= MIN_PAGE_CHARS),
        "image_only_pages": image_only,
        "text_chars": total,
        "replacement_char_share": round(replacement / total, 4) if total else 0.0,
    }
    if total == 0:
        if image_only:
            raise ExtractionError("needs_ocr", f"no text layer; {len(image_only)} page(s) are images only (scanned?)")
        raise ExtractionError("empty_document", "PDF contains no extractable text")
    if len(image_only) / doc.page_count > OCR_PAGE_SHARE:
        raise ExtractionError(
            "needs_ocr", f"{len(image_only)} of {doc.page_count} pages have no text layer but contain images "
                         f"(scanned pages: {image_only[:10]}{'…' if len(image_only) > 10 else ''}); run OCR first")
    if quality["replacement_char_share"] > GARBLED_SHARE:
        raise ExtractionError("garbled_text", "text layer is mostly unmappable glyphs (broken font encoding)")
    if quality["pages_with_text"] == 0:
        raise ExtractionError("empty_document", "no page has a usable amount of text")
    return quality


# ---------------------------------------------------------------- lines

def read_lines(doc: fitz.Document) -> list[list[Line]]:
    flags = fitz.TEXTFLAGS_DICT | fitz.TEXT_DEHYPHENATE
    pages = []
    for pno, page in enumerate(doc):
        lines = []
        for bno, block in enumerate(page.get_text("dict", flags=flags)["blocks"]):
            for ln in block.get("lines", []):
                parts, bullet, sizes = [], False, Counter()
                bold_chars = 0
                for span in ln["spans"]:
                    t = span["text"]
                    if not t.strip():
                        parts.append(t)
                        continue
                    if span["font"].lower().startswith(_BULLET_FONTS) and len(t.strip()) == 1:
                        bullet = True
                        parts.append("• ")
                        continue
                    parts.append(t)
                    sizes[round(span["size"], 1)] += len(t.strip())
                    if span["flags"] & 16 or "bold" in span["font"].lower():
                        bold_chars += len(t.strip())
                text = _collapse_spaced_letters(normalize_text("".join(parts)))
                if not text or not sizes:
                    continue
                if text[0] in _BULLET_CHARS and len(text) > 2 and text[1] == " ":
                    bullet = True
                n_chars = sum(sizes.values())
                lines.append(Line(text=text, size=sizes.most_common(1)[0][0], bold=bold_chars > n_chars / 2,
                                  page=pno, block=bno, y0=ln["bbox"][1], y1=ln["bbox"][3], bullet=bullet,
                                  rotated=abs(ln.get("dir", (1, 0))[1]) > 0.5))
        pages.append(lines)
    return pages


def _zone(ln: Line, h: float) -> tuple[str, int] | None:
    """('top'|'bottom', distance-from-edge bucket) for lines in the outer zone, else None."""
    if ln.y1 <= h * FURNITURE_ZONE:
        return "top", round(ln.y0 / 4)
    if ln.y0 >= h * (1 - FURNITURE_ZONE):
        return "bottom", round((h - ln.y0) / 4)
    return None


def strip_furniture(doc: fitz.Document, pages: list[list[Line]], *,
                    has_outline: bool) -> tuple[list[list[Line]], dict[int, str]]:
    """Remove running headers/footers and page numbers. Returns (body lines, chapter header per page).

    Furniture = the same text at the same distance from the page edge, in the outer zone,
    on several pages. Repeated sentences in the body are left alone because their position
    varies. A top header whose text changes between chapters (and appears on at most
    BOILERPLATE_SHARE of pages) becomes chapter context, unless the PDF has an outline,
    which is then authoritative.
    """
    n_pages = len(pages)
    body = body_font_size(pages)

    def fkey(ln: Line) -> str:
        # Small text: ignore numbers ("Page 3 of 47"). Heading-sized text must match exactly,
        # so numbered headings such as "Table 1", "Table 2" at the same spot are kept.
        return _key(ln.text) if ln.size <= body + 0.5 else ln.text.lower().strip()

    seen: dict[tuple, set[int]] = defaultdict(set)
    text_pages: dict[tuple[str, str], set[int]] = defaultdict(set)
    rotated_pages: dict[str, set[int]] = defaultdict(set)
    for pno, lines in enumerate(pages):
        h = doc[pno].rect.height
        for ln in lines:
            if ln.rotated:
                rotated_pages[fkey(ln)].add(pno)
                continue
            z = _zone(ln, h)
            if z:
                seen[(z[0], z[1], fkey(ln))].add(pno)
                text_pages[(z[0], fkey(ln))].add(pno)

    chapters: dict[int, str] = {}
    body_pages = []
    for pno, lines in enumerate(pages):
        h = doc[pno].rect.height
        kept = []
        for ln in lines:
            if ln.rotated:
                if len(rotated_pages[fkey(ln)]) < MIN_REPEAT_PAGES:
                    kept.append(ln)              # one-off rotated text (e.g. a table label) stays
                continue                         # repeated rotated text = page furniture
            z = _zone(ln, h)
            if z is None:
                kept.append(ln)
                continue
            if _PAGE_NUMBER.match(ln.text) and (ln.y1 <= h * BAND or ln.y0 >= h * (1 - BAND)):
                continue
            repeats = len(seen[(z[0], z[1], fkey(ln))])
            if repeats < MIN_REPEAT_PAGES and len(text_pages[(z[0], fkey(ln))]) / n_pages <= GLOBAL_REPEAT_SHARE:
                kept.append(ln)
                continue
            share = len(text_pages[(z[0], fkey(ln))]) / n_pages
            if (not has_outline and z[0] == "top" and ln.y1 <= h * BAND and share <= BOILERPLATE_SHARE
                    and any(c.isalpha() for c in ln.text)):
                chapters.setdefault(pno, ln.text)      # varying running header = chapter context
            # otherwise: boilerplate header/footer, dropped
        body_pages.append(kept)
    return body_pages, chapters


# ---------------------------------------------------------------- headings

def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def body_font_size(pages: list[list[Line]]) -> float:
    sizes = Counter()
    for lines in pages:
        for ln in lines:
            if not ln.bold:
                sizes[ln.size] += len(ln.text)
    return sizes.most_common(1)[0][0] if sizes else 10.0


def headings_from_outline(doc: fitz.Document, pages: list[list[Line]]) -> dict[tuple[int, int], Heading]:
    """Place outline (bookmark) entries on the line where their title appears.

    Returns {(page_index, line_index): Heading}. Entries whose title is not found on the
    page are anchored at the first line of that page.
    """
    anchors: dict[tuple[int, int], Heading] = {}
    for level, title, page in doc.get_toc(simple=True):
        pno = page - 1
        if not (0 <= pno < len(pages)) or not title.strip():
            continue
        heading = Heading(level=level, title=normalize_text(title), page=page)
        target = _norm_title(title)
        idx = 0
        for i, ln in enumerate(pages[pno]):
            ln_norm = _norm_title(ln.text)
            if ln_norm and (ln_norm.startswith(target[:40]) or (len(ln_norm) >= 10 and target.startswith(ln_norm))):
                if (pno, i) not in anchors:
                    idx = i
                    break
        while (pno, idx) in anchors:     # two entries on the same anchor line: keep order
            idx += 1
        anchors[(pno, idx)] = heading
    return anchors


def headings_from_fonts(pages: list[list[Line]], body_size: float) -> dict[tuple[int, int], Heading]:
    """Detect headings from styling: bigger-than-body text, or short bold lines on their own."""

    def is_candidate(ln: Line) -> bool:
        letters = sum(c.isalpha() for c in ln.text)
        if letters < 3 or ln.bullet or _DOT_LEADER.search(ln.text):
            return False
        if ln.text[0].isdigit() and not _NUMBERED_HEADING.match(ln.text):
            return False      # table values such as "23 per 100"
        bigger = ln.size >= body_size + 1.0
        return bigger or (ln.bold and ln.size >= body_size - 0.6)

    groups: list[tuple[int, list[int], float]] = []  # (page, line indexes, size)
    for pno, lines in enumerate(pages):
        i = 0
        while i < len(lines):
            if not is_candidate(lines[i]):
                i += 1
                continue
            j = i
            while (j + 1 < len(lines) and is_candidate(lines[j + 1]) and lines[j + 1].block == lines[i].block
                   and abs(lines[j + 1].size - lines[i].size) < 0.3):
                j += 1
            groups.append((pno, list(range(i, j + 1)), lines[i].size))
            i = j + 1

    def accepted(pno: int, idxs: list[int], size: float) -> bool:
        text = " ".join(pages[pno][k].text for k in idxs)
        bigger = size >= body_size * 1.15
        limit = 250 if bigger else 120
        if len(text) > limit:
            return False
        if not bigger and text.rstrip().endswith((".", ",", ";")):
            return False      # bold sentence, e.g. "Strong recommendation."
        return True

    kept = [(p, idxs, size) for p, idxs, size in groups if accepted(p, idxs, size)]
    # A heading style must recur on several pages; one-off display text (cover titles) is not a heading.
    pages_per_size: dict[float, set[int]] = defaultdict(set)
    for p, _, size in kept:
        pages_per_size[round(size, 1)].add(p)
    kept = [(p, idxs, size) for p, idxs, size in kept if len(pages_per_size[round(size, 1)]) >= MIN_REPEAT_PAGES]
    heading_sizes = sorted({round(size, 1) for _, _, size in kept}, reverse=True)
    level_of = {s: min(i + 1, 4) for i, s in enumerate(heading_sizes)}
    anchors = {}
    for pno, idxs, size in kept:
        title = " ".join(pages[pno][k].text for k in idxs)
        anchors[(pno, idxs[0])] = Heading(level=level_of[round(size, 1)], title=title, page=pno + 1)
        for k in idxs[1:]:
            anchors[(pno, k)] = None  # continuation line of a multi-line heading
    return anchors


# ---------------------------------------------------------------- paragraphs

_CONTENTS_TITLES = {"contents", "table of contents"}


def _is_contents(section: tuple[str, ...]) -> bool:
    # Compare without spaces: letter-spaced titles collapse to "TABLEOFCONTENTS".
    return bool(section) and _norm_title(section[-1]).replace(" ", "") in {t.replace(" ", "") for t in _CONTENTS_TITLES}


def build_paragraphs(pages: list[list[Line]], anchors: dict, chapters: dict[int, str]) -> list[Paragraph]:
    """Turn body lines into paragraphs that carry their page range and section path."""
    paragraphs: list[Paragraph] = []
    stack: list[Heading] = []
    chapter: str | None = None
    buf: list[str] = []
    buf_start = buf_end = buf_block = None

    def path() -> tuple[str, ...]:
        titles = [h.title for h in stack]
        if chapter and (not titles or _norm_title(titles[0]) != _norm_title(chapter)):
            titles = [chapter] + titles
        return tuple(titles)

    def flush() -> None:
        nonlocal buf
        if buf and _is_contents(path()):
            buf = []                         # table-of-contents pages are navigation, not content
        if buf:
            text = " ".join(buf).strip()
            if sum(c.isalpha() for c in text) >= 3:
                paragraphs.append(Paragraph(text=text, page_start=buf_start, page_end=buf_end, section_path=path()))
        buf = []

    for pno, lines in enumerate(pages):
        page_no = pno + 1
        if chapters.get(pno) and chapters[pno] != chapter:
            flush()
            chapter, stack = chapters[pno], []
        for i, ln in enumerate(lines):
            if (pno, i) in anchors:
                flush()
                heading = anchors[(pno, i)]
                if heading is not None:          # None = continuation line of a multi-line heading
                    while stack and stack[-1].level >= heading.level:
                        stack.pop()
                    stack.append(heading)
                continue
            if _DOT_LEADER.search(ln.text):      # table-of-contents leader lines
                continue
            if buf and (ln.block != buf_block or page_no != buf_end or ln.bullet):
                flush()
            if not buf:
                prev = paragraphs[-1] if paragraphs else None
                if (prev and page_no in (prev.page_end, prev.page_end + 1) and prev.section_path == path()
                        and not ln.bullet and ln.text[:1].islower() and not prev.text.rstrip().endswith(_TERMINAL)):
                    # An unfinished paragraph continues here (next column or next page): reopen it.
                    paragraphs.pop()
                    buf, buf_start = [prev.text], prev.page_start
                else:
                    buf_start = page_no
            buf.append(ln.text)
            buf_end, buf_block = page_no, ln.block
        # keep the buffer open across the page break; the next line decides
    flush()
    return paragraphs


# ---------------------------------------------------------------- metadata

_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
_ISBN = re.compile(r"ISBN[^0-9]{0,12}((?:97[89][-\s]?)?\d{1,5}[-\s]?\d{1,7}[-\s]?\d{1,7}[-\s]?[\dXx])")
_YEAR_PATTERNS = [
    re.compile(r"Revised\s+[A-Z][a-z]+\s+((?:19|20)\d{2})"),
    re.compile(r"Issue\s+\d+,\s+((?:19|20)\d{2})"),
    re.compile(r"((?:19|20)\d{2}),\s+Issue\s+\d+"),
    re.compile(r"©\s*(?:[A-Za-z][\w&.,'\s]{0,60}?\s)?((?:19|20)\d{2})"),
    re.compile(r"Geneva:\s*World Health Organization;\s*((?:19|20)\d{2})"),
]
_AUTHOR_LIST = re.compile(r"^[A-Z][\w'’-]+(?: [A-Z][\w'’-]+)* [A-Z]{1,4}(?:, [A-Z][\w'’-]+(?: [A-Z][\w'’-]+)* [A-Z]{1,4})+$")
_TOPICS = {
    "alcohol": r"\balcohol",
    "cannabis": r"\bcannabi|\bmarijuana",
    "opioids": r"\bopioid|\bheroin|\bmethadone|\bbuprenorphine",
    "stimulants": r"\bstimulant|\bcocaine|\bamphetamine|\bmethamphetamine",
    "gambling": r"\bgambl",
    "gaming": r"\bgaming\b|\bvideo game",
}


def _clean_embedded(meta: dict) -> dict:
    out = {}
    for key in ("title", "author", "subject", "keywords", "creationDate"):
        v = (meta or {}).get(key) or ""
        v = re.sub(r"^Microsoft Word - ", "", normalize_text(v)).strip().strip('"')
        if v and v.lower() not in {"untitled", "title"}:
            out[key] = v
    return out


def extract_metadata(doc: fitz.Document, raw_pages: list[list[Line]], pages: list[list[Line]],
                     full_text: str) -> ExtractedMetadata:
    m = ExtractedMetadata(embedded=_clean_embedded(doc.metadata))
    front = "\n".join(doc[i].get_text() for i in range(min(6, doc.page_count)))
    front = normalize_text(front.replace("\n", " \n "))

    if dm := _DOI.search(front):
        m.doi = dm.group(1).rstrip(".,;)")
    if im := _ISBN.search(front):
        m.isbn = re.sub(r"\s", "-", im.group(1))
    years: list[int] = []
    for pattern in _YEAR_PATTERNS:
        for ym in pattern.finditer(front):
            y = int(ym.group(1))
            if y not in years:
                years.append(y)
    m.year_candidates = years

    # Title guess: the largest text on the first page that still has text after furniture removal.
    # Logos and journal names are large but short, so require at least three words.
    for lines in pages[:3]:
        for size in sorted({ln.size for ln in lines}, reverse=True):
            candidate = " ".join(ln.text for ln in lines if abs(ln.size - size) < 0.5)
            if len(candidate.split()) >= 3:
                m.title_guess = candidate[:300]
                break
        if m.title_guess:
            break
    if m.embedded.get("author"):
        m.authors_guess = [m.embedded["author"]]
    else:
        for ln in (raw_pages[0] if raw_pages else []):
            if _AUTHOR_LIST.match(ln.text):
                m.authors_guess = [a.strip() for a in ln.text.split(",")]
                break
    lowered = full_text.lower()
    m.topic_mentions = {topic: len(re.findall(rx, lowered)) for topic, rx in _TOPICS.items()}
    return m


# ---------------------------------------------------------------- entry point

def extract(path: Path) -> ExtractionResult:
    doc = open_pdf(path)
    try:
        quality = assess_quality(doc)
        raw_pages = read_lines(doc)
        has_outline = bool(doc.get_toc(simple=True))
        pages, chapters = strip_furniture(doc, raw_pages, has_outline=has_outline)
        if has_outline:
            anchors, source = headings_from_outline(doc, pages), "pdf_outline"
        else:
            anchors, source = headings_from_fonts(pages, body_font_size(pages)), "font_styles"
        paragraphs = build_paragraphs(pages, anchors, chapters)
        if not paragraphs:
            raise ExtractionError("empty_document", "no body text remained after removing headers and footers")
        full_text = "\n".join(p.text for p in paragraphs)
        outline = sorted((h for h in anchors.values() if h is not None), key=lambda h: h.page)
        labels = [(doc[i].get_label() or None) for i in range(doc.page_count)]
        return ExtractionResult(
            page_count=doc.page_count, page_labels=labels, paragraphs=paragraphs, outline=outline,
            heading_source=source, text_char_count=len(full_text), quality=quality,
            metadata=extract_metadata(doc, raw_pages, pages, full_text),
        )
    finally:
        doc.close()
