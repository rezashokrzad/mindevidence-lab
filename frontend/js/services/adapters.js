// Map API responses (snake_case, see /api/docs) to the shapes the UI renders.
// Missing values stay explicit: they render as "Not recorded", never blank.

import { SUGGESTED_QUESTIONS, DEFAULT_SUGGESTIONS } from '../data/suggestedQuestions.js';

export const NOT_RECORDED = 'Not recorded';

const CATEGORY_LABELS = {
  alcohol: 'Alcohol', cannabis: 'Cannabis', opioids: 'Opioids', stimulants: 'Stimulants', behavioral: 'Behavioral',
};
const GENDER_LABELS = { female: 'Female', male: 'Male', non_binary: 'Non-binary', not_recorded: 'Gender not recorded' };

export const humanize = (value) => (value ? (value.charAt(0).toUpperCase() + value.slice(1)).replaceAll('_', ' ') : NOT_RECORDED);
export const categoryLabel = (c) => CATEGORY_LABELS[c] ?? humanize(c);
export const formatPartialDate = (d) => d?.value ?? 'Date not recorded';
const orNotRecorded = (v) => (v === null || v === undefined || v === '' ? NOT_RECORDED : v);

export function toPatientSummary(p) {
  return {
    id: p.patient_id,
    synthetic: p.is_synthetic,
    age: p.age_years,
    gender: GENDER_LABELS[p.gender] ?? humanize(p.gender),
    category: categoryLabel(p.primary_category),
    primaryConcern: p.primary_concern_label,
    briefHistory: p.brief_history,
    treatmentSummary: p.treatment_summary,
    stage: humanize(p.motivation_stage),
  };
}

function period(i) {
  const start = formatPartialDate(i.start);
  if (i.status === 'ongoing') return `${start} – present`;
  const end = i.end?.value ?? 'end not recorded';
  return end === start ? start : `${start} – ${end}`;
}

export function toPatientDetail(p, history) {
  const summary = toPatientSummary(p);
  return {
    ...summary,
    syntheticNotice: p.synthetic_notice,
    caseAsOf: p.case_as_of,
    presentingConcern: p.presenting_concern,
    occupation: orNotRecorded(p.occupation),
    livingSituation: p.living_situation,
    socialContext: p.social_context,
    // Used by the mock answer engine as a background fact.
    background: [p.occupation, p.living_situation, p.social_context].filter(Boolean).join('. '),
    motivationNotes: p.motivation_notes,
    uses: p.uses.map((u) => ({
      target: u.target,
      category: categoryLabel(u.category),
      isPrimary: u.is_primary,
      pattern: u.pattern_description,
      firstUseAge: orNotRecorded(u.first_use_age),
      regularUseAge: orNotRecorded(u.regular_use_age),
      frequency: orNotRecorded(u.frequency_at_intake),
      quantity: orNotRecorded(u.quantity_at_intake),
      route: u.route === 'unknown' ? NOT_RECORDED : humanize(u.route),
      status: humanize(u.current_status),
      notes: u.notes,
    })),
    observedBehaviors: p.observed_behaviors.map((o) => ({
      text: o.value_text, label: o.label, date: formatPartialDate(o.observed), source: humanize(o.source),
    })),
    history: history.map((e) => ({
      date: formatPartialDate(e.date), event: e.description, type: e.event_type,
    })),
    triggers: p.triggers.map((f) => f.description),
    riskFactors: p.risk_factors.map((f) => f.description),
    protectiveFactors: p.protective_factors.map((f) => f.description),
    previousInterventions: p.previous_interventions.map((i) => ({
      name: i.name, period: period(i), outcome: i.outcome_summary, status: humanize(i.status),
    })),
    symptoms: p.co_occurring_symptoms.map((s) => ({
      description: s.description,
      domain: humanize(s.domain),
      status: s.status,
      since: formatPartialDate(s.noted_since),
      notes: s.notes,
    })),
    progress: {
      stage: humanize(p.motivation.stage),
      summary: p.progress_summary,
      indicators: p.progress_measures.map((m) => ({
        label: m.label,
        value: m.baseline_text ? `${m.value_text} (baseline ${m.baseline_text})` : m.value_text,
      })),
    },
    stageHistory: p.stage_history.map((s) => ({ date: formatPartialDate(s.observed), stage: humanize(s.stage) })),
    suggestedQuestions: SUGGESTED_QUESTIONS[p.patient_id] ?? DEFAULT_SUGGESTIONS,
  };
}
