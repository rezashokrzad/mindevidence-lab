"""Synthetic patient cases (schema `cases`, migration 0001)."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, ForeignKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

SCHEMA = {"schema": "cases"}


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = SCHEMA

    patient_id: Mapped[str] = mapped_column(primary_key=True)
    is_synthetic: Mapped[bool]
    synthetic_notice: Mapped[str]
    case_as_of: Mapped[date]
    age_years: Mapped[int]
    gender: Mapped[str]
    primary_category: Mapped[str]
    primary_concern_label: Mapped[str]
    presenting_concern: Mapped[str]
    brief_history: Mapped[str]
    treatment_summary: Mapped[str]
    occupation: Mapped[str | None]
    living_situation: Mapped[str]
    social_context: Mapped[str]
    motivation_stage: Mapped[str]
    motivation_notes: Mapped[str | None]
    progress_summary: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    uses: Mapped[list["SubstanceOrBehaviorUse"]] = relationship(
        order_by="(SubstanceOrBehaviorUse.is_primary.desc(), SubstanceOrBehaviorUse.use_id)", viewonly=True)
    interventions: Mapped[list["Intervention"]] = relationship(
        order_by="(Intervention.start_date.asc().nulls_first(), Intervention.intervention_id)", viewonly=True)
    factors: Mapped[list["RiskFactor"]] = relationship(order_by="RiskFactor.factor_id", viewonly=True)
    observations: Mapped[list["ProgressObservation"]] = relationship(
        order_by="(ProgressObservation.observed_on.asc().nulls_first(), ProgressObservation.observation_id)",
        viewonly=True)
    symptoms: Mapped[list["CoOccurringSymptom"]] = relationship(
        order_by="CoOccurringSymptom.symptom_id", viewonly=True)


class SubstanceOrBehaviorUse(Base):
    __tablename__ = "substance_or_behavior_use"
    __table_args__ = SCHEMA

    use_id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("cases.patients.patient_id"))
    target: Mapped[str]
    category: Mapped[str]
    is_primary: Mapped[bool]
    first_use_age: Mapped[int | None]
    regular_use_age: Mapped[int | None]
    pattern_description: Mapped[str]
    frequency_at_intake: Mapped[str | None]
    quantity_at_intake: Mapped[str | None]
    route: Mapped[str]
    current_status: Mapped[str]
    notes: Mapped[str | None]


class Intervention(Base):
    __tablename__ = "previous_interventions"
    __table_args__ = SCHEMA

    intervention_id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("cases.patients.patient_id"))
    name: Mapped[str]
    intervention_type: Mapped[str]
    setting: Mapped[str]
    start_date: Mapped[date | None]
    start_precision: Mapped[str]
    end_date: Mapped[date | None]
    end_precision: Mapped[str | None]
    status: Mapped[str]
    sessions_offered: Mapped[int | None]
    sessions_attended: Mapped[int | None]
    outcome_summary: Mapped[str]


class HistoryEvent(Base):
    __tablename__ = "patient_history_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["patient_id", "related_use_id"],
            ["cases.substance_or_behavior_use.patient_id", "cases.substance_or_behavior_use.use_id"]),
        ForeignKeyConstraint(
            ["patient_id", "related_intervention_id"],
            ["cases.previous_interventions.patient_id", "cases.previous_interventions.intervention_id"]),
        SCHEMA,
    )

    event_id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("cases.patients.patient_id"))
    sequence_no: Mapped[int]
    event_date: Mapped[date | None]
    date_precision: Mapped[str]
    event_type: Mapped[str]
    description: Mapped[str]
    related_use_id: Mapped[int | None]
    related_intervention_id: Mapped[int | None]
    source: Mapped[str]

    related_use: Mapped["SubstanceOrBehaviorUse | None"] = relationship(
        primaryjoin="HistoryEvent.related_use_id == SubstanceOrBehaviorUse.use_id",
        foreign_keys="HistoryEvent.related_use_id", viewonly=True)
    related_intervention: Mapped["Intervention | None"] = relationship(
        primaryjoin="HistoryEvent.related_intervention_id == Intervention.intervention_id",
        foreign_keys="HistoryEvent.related_intervention_id", viewonly=True)


class RiskFactor(Base):
    """Row of cases.triggers_and_risk_factors: a trigger, risk factor, or protective factor."""

    __tablename__ = "triggers_and_risk_factors"
    __table_args__ = SCHEMA

    factor_id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("cases.patients.patient_id"))
    factor_type: Mapped[str]
    domain: Mapped[str]
    description: Mapped[str]
    salience: Mapped[str]
    source: Mapped[str]


class ProgressObservation(Base):
    __tablename__ = "progress_observations"
    __table_args__ = SCHEMA

    observation_id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("cases.patients.patient_id"))
    observed_on: Mapped[date | None]
    observed_precision: Mapped[str]
    observation_type: Mapped[str]
    label: Mapped[str]
    value_text: Mapped[str]
    value_numeric: Mapped[Decimal | None]
    unit: Mapped[str | None]
    baseline_text: Mapped[str | None]
    source: Mapped[str]


class CoOccurringSymptom(Base):
    __tablename__ = "co_occurring_symptoms"
    __table_args__ = SCHEMA

    symptom_id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("cases.patients.patient_id"))
    description: Mapped[str]
    domain: Mapped[str]
    status: Mapped[str]
    noted_since: Mapped[date | None]
    noted_precision: Mapped[str]
    source: Mapped[str]
    notes: Mapped[str | None]
