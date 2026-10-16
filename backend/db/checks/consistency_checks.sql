-- Case-consistency checks for the synthetic dataset.
--
-- Every query returns the rows that VIOLATE a rule, so an empty result means the
-- rule holds. The test suite runs each block (tests/test_case_consistency.py);
-- you can also paste any block into psql.
--
-- Block format:  -- name: <check_name>  followed by one SELECT ending in ';'.
-- Rules already enforced by table constraints (ID format, adult age, date
-- precision, one primary use, same-patient links, ...) are not repeated here.

-- name: expected_patient_ids
-- Exactly P-001..P-010 must exist; no missing and no extra IDs.
SELECT coalesce(e.patient_id, p.patient_id) AS patient_id,
       CASE WHEN p.patient_id IS NULL THEN 'missing' ELSE 'unexpected' END AS problem
FROM (SELECT format('P-%s', lpad(n::text, 3, '0')) AS patient_id FROM generate_series(1, 10) n) e
FULL JOIN cases.patients p ON p.patient_id = e.patient_id
WHERE e.patient_id IS NULL OR p.patient_id IS NULL;

-- name: synthetic_labels
-- Every row in every case table is flagged synthetic; every patient carries the notice.
SELECT 'patients' AS table_name, patient_id FROM cases.patients
 WHERE NOT is_synthetic OR synthetic_notice NOT ILIKE '%synthetic%'
UNION ALL SELECT 'substance_or_behavior_use', patient_id FROM cases.substance_or_behavior_use WHERE NOT is_synthetic
UNION ALL SELECT 'previous_interventions', patient_id FROM cases.previous_interventions WHERE NOT is_synthetic
UNION ALL SELECT 'patient_history_events', patient_id FROM cases.patient_history_events WHERE NOT is_synthetic
UNION ALL SELECT 'triggers_and_risk_factors', patient_id FROM cases.triggers_and_risk_factors WHERE NOT is_synthetic
UNION ALL SELECT 'progress_observations', patient_id FROM cases.progress_observations WHERE NOT is_synthetic
UNION ALL SELECT 'co_occurring_symptoms', patient_id FROM cases.co_occurring_symptoms WHERE NOT is_synthetic;

-- name: primary_use_matches_category
-- Each patient has a primary use whose category equals patients.primary_category.
SELECT p.patient_id, p.primary_category, u.category AS primary_use_category
FROM cases.patients p
LEFT JOIN cases.substance_or_behavior_use u ON u.patient_id = p.patient_id AND u.is_primary
WHERE u.use_id IS NULL OR u.category <> p.primary_category;

-- name: minimum_case_content
-- Each case is rich enough to compare: timeline, triggers, protective factors,
-- interventions, behaviours, measures, and a motivation-stage reading.
SELECT p.patient_id, c.*
FROM cases.patients p
CROSS JOIN LATERAL (
  SELECT
    (SELECT count(*) FROM cases.patient_history_events e WHERE e.patient_id = p.patient_id) AS events,
    (SELECT count(*) FROM cases.triggers_and_risk_factors f WHERE f.patient_id = p.patient_id AND f.factor_type = 'trigger') AS triggers,
    (SELECT count(*) FROM cases.triggers_and_risk_factors f WHERE f.patient_id = p.patient_id AND f.factor_type = 'protective_factor') AS protective,
    (SELECT count(*) FROM cases.previous_interventions i WHERE i.patient_id = p.patient_id) AS interventions,
    (SELECT count(*) FROM cases.progress_observations o WHERE o.patient_id = p.patient_id AND o.observation_type = 'behavior') AS behaviors,
    (SELECT count(*) FROM cases.progress_observations o WHERE o.patient_id = p.patient_id AND o.observation_type = 'measure') AS measures,
    (SELECT count(*) FROM cases.progress_observations o WHERE o.patient_id = p.patient_id AND o.observation_type = 'motivation_stage') AS stages
) c
WHERE c.events < 5 OR c.triggers < 3 OR c.protective < 1 OR c.interventions < 2
   OR c.behaviors < 1 OR c.measures < 1 OR c.stages < 1;

-- name: no_dates_after_case_as_of
-- Nothing in a case happens after the case's reference date.
SELECT p.patient_id, x.what, x.d
FROM cases.patients p
JOIN (
  SELECT patient_id, 'event #' || sequence_no AS what, event_date AS d FROM cases.patient_history_events
  UNION ALL SELECT patient_id, 'intervention start: ' || name, start_date FROM cases.previous_interventions
  UNION ALL SELECT patient_id, 'intervention end: ' || name, end_date FROM cases.previous_interventions
  UNION ALL SELECT patient_id, 'observation: ' || label, observed_on FROM cases.progress_observations
  UNION ALL SELECT patient_id, 'symptom: ' || description, noted_since FROM cases.co_occurring_symptoms
) x ON x.patient_id = p.patient_id
WHERE x.d > p.case_as_of;

-- name: event_sequence_is_chronological
-- sequence_no order never goes backwards in time. Two dated events are compared at the
-- coarser of their precisions (e.g. '2025' vs '2025-07' compares years only).
WITH dated AS (
  SELECT patient_id, sequence_no, event_date, date_precision,
         lag(sequence_no)    OVER w AS prev_seq,
         lag(event_date)     OVER w AS prev_date,
         lag(date_precision) OVER w AS prev_precision
  FROM cases.patient_history_events
  WHERE event_date IS NOT NULL
  WINDOW w AS (PARTITION BY patient_id ORDER BY sequence_no)
), cmp AS (
  SELECT *, CASE
              WHEN 'year'  IN (date_precision, prev_precision) THEN 'year'
              WHEN 'month' IN (date_precision, prev_precision) THEN 'month'
              ELSE 'day' END AS unit
  FROM dated WHERE prev_date IS NOT NULL
)
SELECT patient_id, prev_seq, prev_date, sequence_no, event_date
FROM cmp
WHERE date_trunc(unit, prev_date) > date_trunc(unit, event_date);

-- name: every_intervention_starts_on_the_timeline
-- Each intervention has exactly one treatment_start event with the same start date and precision.
SELECT i.patient_id, i.intervention_id, i.name, count(e.event_id) AS start_events
FROM cases.previous_interventions i
LEFT JOIN cases.patient_history_events e
  ON e.related_intervention_id = i.intervention_id AND e.event_type = 'treatment_start'
 AND e.event_date IS NOT DISTINCT FROM i.start_date AND e.date_precision = i.start_precision
GROUP BY i.patient_id, i.intervention_id, i.name
HAVING count(e.event_id) <> 1;

-- name: treatment_end_events_match_interventions
-- A treatment_end event must belong to an ended intervention and match its end date.
SELECT e.patient_id, e.sequence_no, i.name, i.status, i.end_date, e.event_date
FROM cases.patient_history_events e
JOIN cases.previous_interventions i ON i.intervention_id = e.related_intervention_id
WHERE e.event_type = 'treatment_end'
  AND (i.status = 'ongoing'
       OR e.event_date IS DISTINCT FROM i.end_date
       OR e.date_precision IS DISTINCT FROM i.end_precision);

-- name: relapses_and_lapses_name_a_use
-- A relapse or lapse must say what was returned to.
SELECT patient_id, sequence_no, description
FROM cases.patient_history_events
WHERE event_type IN ('relapse', 'lapse') AND related_use_id IS NULL;

-- name: relapse_follows_a_treatment_or_milestone
-- A relapse is a return after a change attempt, so some treatment_start must come earlier.
SELECT e.patient_id, e.sequence_no, e.description
FROM cases.patient_history_events e
WHERE e.event_type = 'relapse'
  AND NOT EXISTS (
    SELECT 1 FROM cases.patient_history_events s
    WHERE s.patient_id = e.patient_id AND s.sequence_no < e.sequence_no AND s.event_type = 'treatment_start');

-- name: current_stage_matches_latest_observation
-- patients.motivation_stage equals the most recent motivation_stage observation,
-- and no patient has two stage readings on the same date.
WITH stages AS (
  SELECT patient_id, observed_on, value_text,
         row_number() OVER (PARTITION BY patient_id ORDER BY observed_on DESC NULLS LAST) AS rn,
         count(*) OVER (PARTITION BY patient_id, observed_on) AS same_day
  FROM cases.progress_observations
  WHERE observation_type = 'motivation_stage'
)
SELECT p.patient_id, p.motivation_stage, s.value_text AS latest_observed, s.same_day
FROM cases.patients p
LEFT JOIN stages s ON s.patient_id = p.patient_id AND s.rn = 1
WHERE s.value_text IS DISTINCT FROM p.motivation_stage OR s.same_day > 1;

-- name: onset_ages_match_timeline
-- Recorded onset ages agree (±1 year, birthdays unknown) with the dated onset events,
-- and never exceed the patient's current age.
WITH birth AS (
  SELECT patient_id, age_years, extract(year FROM case_as_of)::int - age_years AS approx_birth_year
  FROM cases.patients
), first_event AS (
  SELECT DISTINCT ON (related_use_id, event_type)
         related_use_id, event_type, extract(year FROM event_date)::int AS event_year
  FROM cases.patient_history_events
  WHERE event_type IN ('use_onset', 'use_escalation') AND event_date IS NOT NULL
  ORDER BY related_use_id, event_type, sequence_no
)
SELECT u.patient_id, u.target, u.first_use_age, u.regular_use_age, b.age_years,
       fo.event_year - b.approx_birth_year AS age_at_onset_event,
       fe.event_year - b.approx_birth_year AS age_at_first_escalation_event
FROM cases.substance_or_behavior_use u
JOIN birth b ON b.patient_id = u.patient_id
LEFT JOIN first_event fo ON fo.related_use_id = u.use_id AND fo.event_type = 'use_onset'
LEFT JOIN first_event fe ON fe.related_use_id = u.use_id AND fe.event_type = 'use_escalation'
WHERE u.first_use_age > b.age_years
   OR u.regular_use_age > b.age_years
   OR (u.first_use_age IS NOT NULL AND fo.event_year IS NOT NULL
       AND abs((fo.event_year - b.approx_birth_year) - u.first_use_age) > 1)
   OR (u.regular_use_age IS NOT NULL AND fe.event_year IS NOT NULL
       AND abs((fe.event_year - b.approx_birth_year) - u.regular_use_age) > 1);

-- name: use_status_has_treatment_context
-- An abstinent or reduced primary use needs at least one intervention or milestone to explain it;
-- a 'stable_on_treatment' use needs an ongoing intervention.
SELECT u.patient_id, u.target, u.current_status
FROM cases.substance_or_behavior_use u
WHERE u.is_primary AND (
  (u.current_status = 'stable_on_treatment' AND NOT EXISTS (
     SELECT 1 FROM cases.previous_interventions i WHERE i.patient_id = u.patient_id AND i.status = 'ongoing'))
  OR (u.current_status IN ('abstinent', 'reduced') AND NOT EXISTS (
     SELECT 1 FROM cases.previous_interventions i WHERE i.patient_id = u.patient_id)));

-- name: no_contact_details_or_links
-- Free text must not contain anything that looks like an email, phone number, or URL.
SELECT patient_id, col, val FROM (
  SELECT patient_id, 'patients' AS col, concat_ws(' | ', presenting_concern, brief_history, treatment_summary,
         occupation, living_situation, social_context, motivation_notes) AS val FROM cases.patients
  UNION ALL SELECT patient_id, 'events', description FROM cases.patient_history_events
  UNION ALL SELECT patient_id, 'interventions', concat_ws(' | ', name, outcome_summary) FROM cases.previous_interventions
  UNION ALL SELECT patient_id, 'factors', description FROM cases.triggers_and_risk_factors
  UNION ALL SELECT patient_id, 'observations', concat_ws(' | ', label, value_text, baseline_text) FROM cases.progress_observations
  UNION ALL SELECT patient_id, 'symptoms', concat_ws(' | ', description, notes) FROM cases.co_occurring_symptoms
  UNION ALL SELECT patient_id, 'uses', concat_ws(' | ', target, pattern_description, frequency_at_intake,
         quantity_at_intake, notes) FROM cases.substance_or_behavior_use
) t
WHERE val ~* '[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}'   -- email
   OR val ~  '\+?[0-9][0-9 ().-]{8,}[0-9]'             -- phone-like digit run
   OR val ~* '(https?://|www\.)';                      -- URL
