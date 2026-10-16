"""Database access for synthetic patient cases. No business rules here."""
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import HistoryEvent, Patient


def list_patients(db: Session, *, category: str | None, q: str | None,
                  limit: int, offset: int) -> tuple[list[Patient], int]:
    stmt = select(Patient)
    if category:
        stmt = stmt.where(Patient.primary_category == category)
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(or_(
            Patient.patient_id.ilike(pattern, escape="\\"),
            Patient.primary_concern_label.ilike(pattern, escape="\\"),
            Patient.presenting_concern.ilike(pattern, escape="\\"),
            Patient.brief_history.ilike(pattern, escape="\\"),
            Patient.treatment_summary.ilike(pattern, escape="\\"),
        ))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Patient.patient_id).limit(limit).offset(offset)).all()
    return list(rows), total or 0


def get_patient_detail(db: Session, patient_id: str) -> Patient | None:
    stmt = (
        select(Patient)
        .where(Patient.patient_id == patient_id)
        .options(
            selectinload(Patient.uses),
            selectinload(Patient.interventions),
            selectinload(Patient.factors),
            selectinload(Patient.observations),
            selectinload(Patient.symptoms),
        )
    )
    return db.scalars(stmt).first()


def patient_exists(db: Session, patient_id: str) -> bool:
    return db.scalar(select(func.count()).where(Patient.patient_id == patient_id)) == 1


def list_history(db: Session, patient_id: str, *, limit: int, offset: int) -> tuple[list[HistoryEvent], int]:
    total = db.scalar(select(func.count()).where(HistoryEvent.patient_id == patient_id))
    rows = db.scalars(
        select(HistoryEvent)
        .where(HistoryEvent.patient_id == patient_id)
        .options(selectinload(HistoryEvent.related_use), selectinload(HistoryEvent.related_intervention))
        .order_by(HistoryEvent.sequence_no)
        .limit(limit).offset(offset)
    ).all()
    return list(rows), total or 0
