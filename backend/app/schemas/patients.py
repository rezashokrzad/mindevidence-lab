"""Patient response schemas. All patient data is synthetic."""
from datetime import date
from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel, Page, PartialDate

Category = Literal["alcohol", "cannabis", "opioids", "stimulants", "behavioral"]
UseCategory = Literal["alcohol", "cannabis", "opioids", "stimulants", "behavioral", "tobacco", "sedatives", "other"]
Stage = Literal["precontemplation", "contemplation", "preparation", "action", "maintenance", "unknown"]
Gender = Literal["female", "male", "non_binary", "not_recorded"]
Source = Literal["self_report", "clinician_observation", "family_report", "screening_tool", "lab_test",
                 "records", "unknown"]


class PatientSummary(ApiModel):
    patient_id: str = Field(examples=["P-001"])
    is_synthetic: Literal[True] = Field(description="Always true: every case is fictional")
    age_years: int
    gender: Gender
    primary_category: Category
    primary_concern_label: str
    brief_history: str
    treatment_summary: str
    motivation_stage: Stage


class PatientPage(Page[PatientSummary]):
    pass


class UseOut(ApiModel):
    use_id: int
    target: str
    category: UseCategory
    is_primary: bool
    first_use_age: int | None = Field(description="null = not recorded")
    regular_use_age: int | None = Field(description="null = not recorded")
    pattern_description: str
    frequency_at_intake: str | None
    quantity_at_intake: str | None
    route: Literal["oral", "smoked_or_vaped", "intranasal", "injected", "sublingual", "mixed",
                   "not_applicable", "unknown"]
    current_status: Literal["active", "reduced", "abstinent", "stable_on_treatment", "unknown"]
    notes: str | None


class FactorOut(ApiModel):
    factor_id: int
    domain: Literal["emotional", "social", "environmental", "physical", "occupational_or_academic",
                    "financial", "family", "other"]
    description: str
    salience: Literal["low", "moderate", "high", "unknown"]
    source: Source


class InterventionOut(ApiModel):
    intervention_id: int
    name: str
    intervention_type: str
    setting: str
    start: PartialDate
    end: PartialDate | None = Field(description="null while the intervention is ongoing")
    status: Literal["completed", "ongoing", "discontinued", "unknown"]
    sessions_offered: int | None
    sessions_attended: int | None
    outcome_summary: str = Field(description="What happened in this fictional case, not a research finding")


class ObservationOut(ApiModel):
    observation_id: int
    observed: PartialDate
    label: str
    value_text: str
    value_numeric: float | None
    unit: str | None
    baseline_text: str | None
    source: Source


class StageReading(ApiModel):
    observed: PartialDate
    stage: Stage
    source: Source


class SymptomOut(ApiModel):
    symptom_id: int
    description: str
    domain: Literal["mood", "anxiety", "sleep", "trauma_related", "psychotic", "cognitive", "physical_health",
                    "other"]
    status: Literal["current", "improving", "past", "unknown"]
    noted_since: PartialDate
    source: Source
    notes: str | None


class Motivation(ApiModel):
    stage: Stage
    notes: str | None


class PatientDetail(PatientSummary):
    synthetic_notice: str
    case_as_of: date
    presenting_concern: str
    occupation: str | None = Field(description="null = not recorded")
    living_situation: str
    social_context: str
    motivation: Motivation
    progress_summary: str
    uses: list[UseOut]
    triggers: list[FactorOut]
    risk_factors: list[FactorOut]
    protective_factors: list[FactorOut]
    previous_interventions: list[InterventionOut]
    observed_behaviors: list[ObservationOut]
    progress_measures: list[ObservationOut]
    stage_history: list[StageReading]
    co_occurring_symptoms: list[SymptomOut] = Field(description="Case descriptions, not diagnoses")


class RelatedUse(ApiModel):
    use_id: int
    target: str


class RelatedIntervention(ApiModel):
    intervention_id: int
    name: str
    status: Literal["completed", "ongoing", "discontinued", "unknown"]


class HistoryEventOut(ApiModel):
    sequence_no: int = Field(description="Authoritative chronological order")
    date: PartialDate
    event_type: Literal["use_onset", "use_escalation", "life_event", "health_event", "occupational_or_academic",
                        "referral", "treatment_start", "treatment_end", "lapse", "relapse", "milestone", "other"]
    description: str
    related_use: RelatedUse | None
    related_intervention: RelatedIntervention | None
    source: Source


class HistoryPage(Page[HistoryEventOut]):
    patient_id: str
