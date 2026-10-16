-- Full profile of one synthetic patient as a single JSON document.
-- This is the shape GET /api/patients/{id} will return in Batch 03.
--
-- psql:  psql -h localhost -U mindevidence -d mindevidence -v patient_id=P-004 -f db/queries/patient_profile.sql
-- (In application code, bind :patient_id as a parameter.)

SELECT json_build_object(
  'patient_id',          p.patient_id,
  'is_synthetic',        p.is_synthetic,
  'synthetic_notice',    p.synthetic_notice,
  'case_as_of',          p.case_as_of,
  'age_years',           p.age_years,
  'gender',              p.gender,
  'primary_category',    p.primary_category,
  'primary_concern_label', p.primary_concern_label,
  'presenting_concern',  p.presenting_concern,
  'brief_history',       p.brief_history,
  'treatment_summary',   p.treatment_summary,
  'occupation',          p.occupation,
  'living_situation',    p.living_situation,
  'social_context',      p.social_context,
  'motivation',          json_build_object('stage', p.motivation_stage, 'notes', p.motivation_notes),
  'progress_summary',    p.progress_summary,

  'uses', (
    SELECT coalesce(json_agg(json_build_object(
             'use_id', u.use_id, 'target', u.target, 'category', u.category, 'is_primary', u.is_primary,
             'first_use_age', u.first_use_age, 'regular_use_age', u.regular_use_age,
             'pattern', u.pattern_description, 'frequency_at_intake', u.frequency_at_intake,
             'quantity_at_intake', u.quantity_at_intake, 'route', u.route,
             'current_status', u.current_status, 'notes', u.notes)
           ORDER BY u.is_primary DESC, u.use_id), '[]'::json)
    FROM cases.substance_or_behavior_use u WHERE u.patient_id = p.patient_id),

  'history', (
    SELECT coalesce(json_agg(json_build_object(
             'sequence_no', e.sequence_no,
             'date', cases.format_partial_date(e.event_date, e.date_precision),
             'date_precision', e.date_precision,
             'event_type', e.event_type, 'description', e.description,
             'related_intervention_id', e.related_intervention_id,
             'related_use_id', e.related_use_id, 'source', e.source)
           ORDER BY e.sequence_no), '[]'::json)
    FROM cases.patient_history_events e WHERE e.patient_id = p.patient_id),

  'triggers', (
    SELECT coalesce(json_agg(json_build_object(
             'factor_id', f.factor_id, 'domain', f.domain, 'description', f.description,
             'salience', f.salience, 'source', f.source) ORDER BY f.factor_id), '[]'::json)
    FROM cases.triggers_and_risk_factors f WHERE f.patient_id = p.patient_id AND f.factor_type = 'trigger'),

  'risk_factors', (
    SELECT coalesce(json_agg(json_build_object(
             'factor_id', f.factor_id, 'domain', f.domain, 'description', f.description,
             'salience', f.salience, 'source', f.source) ORDER BY f.factor_id), '[]'::json)
    FROM cases.triggers_and_risk_factors f WHERE f.patient_id = p.patient_id AND f.factor_type = 'risk_factor'),

  'protective_factors', (
    SELECT coalesce(json_agg(json_build_object(
             'factor_id', f.factor_id, 'domain', f.domain, 'description', f.description,
             'salience', f.salience, 'source', f.source) ORDER BY f.factor_id), '[]'::json)
    FROM cases.triggers_and_risk_factors f WHERE f.patient_id = p.patient_id AND f.factor_type = 'protective_factor'),

  'previous_interventions', (
    SELECT coalesce(json_agg(json_build_object(
             'intervention_id', i.intervention_id, 'name', i.name, 'type', i.intervention_type,
             'setting', i.setting,
             'start', cases.format_partial_date(i.start_date, i.start_precision),
             'end', CASE WHEN i.status = 'ongoing' THEN NULL
                         ELSE cases.format_partial_date(i.end_date, i.end_precision) END,
             'status', i.status, 'sessions_offered', i.sessions_offered,
             'sessions_attended', i.sessions_attended, 'outcome', i.outcome_summary)
           ORDER BY i.start_date NULLS FIRST, i.intervention_id), '[]'::json)
    FROM cases.previous_interventions i WHERE i.patient_id = p.patient_id),

  'observed_behaviors', (
    SELECT coalesce(json_agg(json_build_object(
             'observed', cases.format_partial_date(o.observed_on, o.observed_precision),
             'label', o.label, 'value', o.value_text, 'source', o.source)
           ORDER BY o.observed_on NULLS FIRST, o.observation_id), '[]'::json)
    FROM cases.progress_observations o WHERE o.patient_id = p.patient_id AND o.observation_type = 'behavior'),

  'progress_measures', (
    SELECT coalesce(json_agg(json_build_object(
             'observed', cases.format_partial_date(o.observed_on, o.observed_precision),
             'label', o.label, 'value', o.value_text, 'value_numeric', o.value_numeric,
             'unit', o.unit, 'baseline', o.baseline_text, 'source', o.source)
           ORDER BY o.observed_on NULLS FIRST, o.observation_id), '[]'::json)
    FROM cases.progress_observations o WHERE o.patient_id = p.patient_id AND o.observation_type = 'measure'),

  'stage_history', (
    SELECT coalesce(json_agg(json_build_object(
             'observed', cases.format_partial_date(o.observed_on, o.observed_precision),
             'stage', o.value_text, 'source', o.source)
           ORDER BY o.observed_on NULLS FIRST, o.observation_id), '[]'::json)
    FROM cases.progress_observations o WHERE o.patient_id = p.patient_id AND o.observation_type = 'motivation_stage'),

  'co_occurring_symptoms', (
    SELECT coalesce(json_agg(json_build_object(
             'description', s.description, 'domain', s.domain, 'status', s.status,
             'noted_since', cases.format_partial_date(s.noted_since, s.noted_precision),
             'source', s.source, 'notes', s.notes) ORDER BY s.symptom_id), '[]'::json)
    FROM cases.co_occurring_symptoms s WHERE s.patient_id = p.patient_id)
) AS profile
FROM cases.patients p
WHERE p.patient_id = :'patient_id';
