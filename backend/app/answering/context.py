"""Turn database objects into the labelled inputs the model sees: patient facts (F-ids) and evidence (E-ids)."""
from __future__ import annotations

from app.answering.schemas import EvidenceItem, PatientFact
from app.schemas.patients import PatientDetail
from app.schemas.retrieval import SearchResult

MAX_FACT_CHARS = 400


def _clip(text: str) -> str:
    return text if len(text) <= MAX_FACT_CHARS else text[: MAX_FACT_CHARS - 1] + "…"


def facts_from_patient(p: PatientDetail) -> list[PatientFact]:
    """Facts from the synthetic case record. Values come straight from the database; nothing is added."""
    rows: list[tuple[str, str, str]] = [
        ("Patient", f"{p.patient_id}, synthetic case; {p.age_years} years; {p.gender}", "patient"),
        ("Presenting concern", p.presenting_concern, "presenting_concern"),
        ("Occupation", p.occupation or "not recorded", "occupation"),
        ("Living situation", p.living_situation, "living_situation"),
        ("Social context", p.social_context, "social_context"),
        ("Motivation stage", p.motivation.stage + (f" ({p.motivation.notes})" if p.motivation.notes else ""), "motivation"),
        ("Progress summary", p.progress_summary, "progress_summary"),
    ]
    for u in p.uses:
        rows.append((f"Use: {u.target}" + (" (primary)" if u.is_primary else ""),
                     f"{u.pattern_description}; status {u.current_status}; frequency at intake "
                     f"{u.frequency_at_intake or 'not recorded'}; quantity {u.quantity_at_intake or 'not recorded'}", "uses"))
    for label, items in (("Trigger", p.triggers), ("Risk factor", p.risk_factors), ("Protective factor", p.protective_factors)):
        for f in items:
            rows.append((label, f.description, "factors"))
    for i in p.previous_interventions:
        end = "ongoing" if i.status == "ongoing" else (i.end.value if i.end and i.end.value else "end not recorded")
        rows.append((f"Previous intervention: {i.name}", f"{i.start.value or 'start not recorded'} to {end}; "
                     f"{i.outcome_summary}", "previous_interventions"))
    for s in p.co_occurring_symptoms:
        rows.append((f"Symptom ({s.status})", s.description + (f"; {s.notes}" if s.notes else ""), "co_occurring_symptoms"))
    for m in p.progress_measures:
        rows.append((f"Progress measure: {m.label}", m.value_text + (f" (baseline {m.baseline_text})" if m.baseline_text else ""),
                     "progress_measures"))
    for b in p.observed_behaviors:
        rows.append((f"Observed behaviour: {b.label}", b.value_text, "observed_behaviors"))
    return [PatientFact(citation_id=f"F{i}", label=label, value=_clip(value), field=field)
            for i, (label, value, field) in enumerate(rows, start=1)]


def evidence_from_results(results: list[SearchResult], *, start: int = 1) -> list[EvidenceItem]:
    items = []
    for offset, r in enumerate(results):
        items.append(EvidenceItem(
            citation_id=f"E{start + offset}", chunk_id=r.chunk_id, document_id=r.document.document_id,
            document_title=r.document.title, document_short_title=r.document.short_title,
            section=" › ".join(r.section_path) if r.section_path else "no section heading",
            page_start=r.page_start, page_end=r.page_end, page_label=r.page_label_start, location=r.location,
            text=r.text, matched_passage=r.matched_passage, similarity=r.similarity, distance=r.distance,
            content_hash=r.content_hash))
    return items
