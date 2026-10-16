"""Patient use cases: existence rules and shaping ORM rows into API schemas."""
from datetime import date

from sqlalchemy.orm import Session

from app.errors import PatientNotFound
from app.models import HistoryEvent, Patient
from app.repositories import patients as repo
from app.schemas.common import PartialDate
from app.schemas.patients import (
    FactorOut,
    HistoryEventOut,
    HistoryPage,
    InterventionOut,
    Motivation,
    ObservationOut,
    PatientDetail,
    PatientPage,
    PatientSummary,
    RelatedIntervention,
    RelatedUse,
    StageReading,
    SymptomOut,
    UseOut,
)

_FORMATS = {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}


def partial_date(value: date | None, precision: str) -> PartialDate:
    if value is None or precision == "unknown":
        return PartialDate(value=None, precision="unknown")
    return PartialDate(value=value.strftime(_FORMATS[precision]), precision=precision)


def list_patients(db: Session, *, category: str | None, q: str | None, limit: int, offset: int) -> PatientPage:
    rows, total = repo.list_patients(db, category=category, q=q, limit=limit, offset=offset)
    return PatientPage(items=[PatientSummary.model_validate(r) for r in rows],
                       total=total, limit=limit, offset=offset)


def get_patient(db: Session, patient_id: str) -> PatientDetail:
    p = repo.get_patient_detail(db, patient_id)
    if p is None:
        raise PatientNotFound(patient_id)
    return _to_detail(p)


def get_history(db: Session, patient_id: str, *, limit: int, offset: int) -> HistoryPage:
    if not repo.patient_exists(db, patient_id):
        raise PatientNotFound(patient_id)
    rows, total = repo.list_history(db, patient_id, limit=limit, offset=offset)
    return HistoryPage(patient_id=patient_id, items=[_to_event(e) for e in rows],
                       total=total, limit=limit, offset=offset)


def _to_event(e: HistoryEvent) -> HistoryEventOut:
    return HistoryEventOut(
        sequence_no=e.sequence_no,
        date=partial_date(e.event_date, e.date_precision),
        event_type=e.event_type,
        description=e.description,
        related_use=RelatedUse.model_validate(e.related_use) if e.related_use else None,
        related_intervention=(RelatedIntervention.model_validate(e.related_intervention)
                              if e.related_intervention else None),
        source=e.source,
    )


def _to_detail(p: Patient) -> PatientDetail:
    def factors(kind: str) -> list[FactorOut]:
        return [FactorOut.model_validate(f) for f in p.factors if f.factor_type == kind]

    def observations(kind: str) -> list[ObservationOut]:
        return [
            ObservationOut(
                observation_id=o.observation_id,
                observed=partial_date(o.observed_on, o.observed_precision),
                label=o.label,
                value_text=o.value_text,
                value_numeric=float(o.value_numeric) if o.value_numeric is not None else None,
                unit=o.unit,
                baseline_text=o.baseline_text,
                source=o.source,
            )
            for o in p.observations if o.observation_type == kind
        ]

    summary = PatientSummary.model_validate(p).model_dump()
    return PatientDetail(
        **summary,
        synthetic_notice=p.synthetic_notice,
        case_as_of=p.case_as_of,
        presenting_concern=p.presenting_concern,
        occupation=p.occupation,
        living_situation=p.living_situation,
        social_context=p.social_context,
        motivation=Motivation(stage=p.motivation_stage, notes=p.motivation_notes),
        progress_summary=p.progress_summary,
        uses=[UseOut.model_validate(u) for u in p.uses],
        triggers=factors("trigger"),
        risk_factors=factors("risk_factor"),
        protective_factors=factors("protective_factor"),
        previous_interventions=[
            InterventionOut(
                intervention_id=i.intervention_id,
                name=i.name,
                intervention_type=i.intervention_type,
                setting=i.setting,
                start=partial_date(i.start_date, i.start_precision),
                end=None if i.status == "ongoing" else partial_date(i.end_date, i.end_precision or "unknown"),
                status=i.status,
                sessions_offered=i.sessions_offered,
                sessions_attended=i.sessions_attended,
                outcome_summary=i.outcome_summary,
            )
            for i in p.interventions
        ],
        observed_behaviors=observations("behavior"),
        progress_measures=observations("measure"),
        stage_history=[
            StageReading(observed=partial_date(o.observed_on, o.observed_precision), stage=o.value_text,
                         source=o.source)
            for o in p.observations if o.observation_type == "motivation_stage"
        ],
        co_occurring_symptoms=[
            SymptomOut(
                symptom_id=s.symptom_id,
                description=s.description,
                domain=s.domain,
                status=s.status,
                noted_since=partial_date(s.noted_since, s.noted_precision),
                source=s.source,
                notes=s.notes,
            )
            for s in p.symptoms
        ],
    )
