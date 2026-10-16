# Research Corpus

The corpus is the set of authorized research documents that MindEvidence Lab may cite. It is defined by **`data/corpus_manifest.json`**: only files listed there are ingested, and their bibliographic metadata comes from that manifest.

Research text lives in the `corpus` schema. Synthetic patient cases live in `cases`. Nothing links the two: no foreign keys join them, a test checks that no chunk mentions a patient ID, and the API serves them through separate endpoints.

## Current documents

| ID | Document | Type | Licence / rights (as printed in the PDF) | Status |
| --- | --- | --- | --- | --- |
| `WHO-GL-2023` | World Health Organization. *Mental Health Gap Action Programme (mhGAP) guideline for mental, neurological and substance use disorders.* 2023. ISBN 978-92-4-008427-8 | Guideline | CC BY-NC-SA 3.0 IGO | ingested |
| `NIDA-PDAT` | National Institute on Drug Abuse. *Principles of Drug Addiction Treatment: A Research-Based Guide (Third Edition)*, revised January 2018 | Research guide | No licence statement printed | ingested |
| `COCH-AUD-2025` | Minozzi S, La Rosa GRM, Salis F, Camposeragna A, Saulle R, Leggio L, Agabio R. *Combined pharmacological and psychosocial interventions for alcohol use disorder.* Cochrane Database of Systematic Reviews 2025, Issue 3, CD015673. doi:10.1002/14651858.CD015673.pub2 | Systematic review | © 2025 The Cochrane Collaboration, published by John Wiley & Sons. **No open-access licence is stated.** Used as course-authorized material; do not redistribute | ingested |
| `WHO-IG-2016` | WHO *mhGAP Intervention Guide … Version 2.0* | Intervention guide | — | **missing file**: listed in `docs/SPEC.md`, but no PDF was provided. Metadata is provisional (`metadata_verified: false`) |

### How the metadata was verified

Every bibliographic field for the three ingested documents was transcribed from the PDF itself:
- **WHO:** the copyright page (PDF page 4).
- **NIDA:** the title page and embedded metadata, plus "Revised January 2018" on pages 2–3.
- **Cochrane:** the citation block on page 1 and the publication status on page 4.

Fields the PDF does not print are `null`. For example, NIDA has no DOI or licence statement, so none is recorded.

On every run, the pipeline independently extracts the title, DOI, ISBN, and printed years from each PDF and compares them with the manifest. Disagreements are reported as warnings. The current corpus produces **no** warnings.

## Topic coverage (counted, not estimated)

These are case-insensitive keyword mentions in the extracted text. They show what the corpus can support. They are not a quality judgement.

| Document | Pages | Chunks | alcohol | cannabis | opioids¹ | stimulants² | gambling | gaming |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| WHO-GL-2023 | 184 | 177 | 66 | 29 | 9 | 61 | 0 | 2 |
| NIDA-PDAT | 47 | 45 | 61 | 10 | 107 | 30 | 4 | 0 |
| COCH-AUD-2025 | 113 | 138 | 454 | 8 | 7 | 39 | 6 | 0 |

¹ opioid, heroin, methadone, buprenorphine. ² stimulant, cocaine, (meth)amphetamine.

**Gaps:**
- **Behavioural addictions (gambling, gaming) are essentially not covered.** The few gambling mentions are passing references. Questions about P-008 (gambling) and P-009 (gaming) will rightly find little or no evidence.
- **Cannabis** coverage is thin.

To close these gaps, add authorized documents (see below). Until then, the answer pipeline must say "no evidence found" rather than stretch other evidence.

## Adding a document

1. Place the PDF in `data/papers/` (configurable with `CORPUS_SOURCE_DIR`). Use only documents you are allowed to use for the course. Check the licence printed in the document.
2. Add an entry to `data/corpus_manifest.json`:
   - a stable `document_id` (upper case, letters, digits, and hyphens; it becomes the chunk ID prefix);
   - `filename`, and `sha256` to pin the exact file (`python -m app.ingestion check` prints mismatches);
   - metadata copied **from the PDF**: title, authors, year, DOI or ISBN, venue, and licence. Leave unknown fields `null`.
   - Set `metadata_verified: true` only after checking the metadata against the document, and say where in `verification_note`.
3. Run ingestion (see `backend/README.md`) and read the run report.

A PDF in the folder that is not in the manifest is reported as `unlisted_file` and never read. A byte-identical copy of a listed PDF is reported as `duplicate_file`.

## Licensing notes

- **WHO (CC BY-NC-SA 3.0 IGO):** non-commercial use with attribution. Derivatives must use the same licence.
- **Cochrane:** copyrighted by The Cochrane Collaboration / Wiley, with no open licence stated. Keep it within the course. Do not publish the repository, the PDF, or bulk chunk exports. The development storage folder (`backend/storage/`) is git-ignored. The `data/papers/` folder is **not** git-ignored, so decide before pushing anywhere public.
- **NIDA:** no licence statement in the PDF. It is a U.S. federal agency publication. Cite it as such.

## Known extraction limitations

- Headings come from the PDF outline (NIDA) or font styling (WHO, Cochrane). Styling-based detection can mistake a few table headers or figure labels for headings (e.g. Cochrane "SE(log[RR])"). Letter-spaced headings lose word spacing ("SUMMARYOFFINDINGS").
- Tables (e.g. Cochrane's summary-of-findings and study-characteristics tables) are extracted as text in reading order, not as structured tables.
- A few paragraphs remain split where a sentence crosses a layout block (8–9 per document).
- Token counts are estimates (words plus punctuation). **Embedding model note for Batch 06:** `sentence-transformers/all-MiniLM-L6-v2` truncates its input at 256 word-piece tokens, while chunks here target about 550. Embedding the raw chunk would represent only its first part. Batch 06 should embed sub-windows of each chunk, or the chunk size should be reduced, before retrieval is built.
- Scanned PDFs are detected and rejected (`needs_ocr`). OCR is not performed.
