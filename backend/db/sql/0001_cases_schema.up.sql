-- 0001 · Schemas and synthetic patient-case tables
--
-- Every row in the `cases` schema describes a FICTIONAL teaching case.
-- No real person, contact detail, or clinical record is stored here.
--
-- Data is kept in separate schemas by kind:
--   cases   synthetic patient cases (this migration)
--   corpus  research documents and chunks (Batch 05)
--   auth    users and sessions (Batch 04)
--   audit   activity and audit events (Batch 09)
--
-- Conventions
--   * Categorical columns are text + CHECK (easier to evolve than ENUM types).
--   * Partial dates are stored as (date, precision). A month-precision value is
--     stored as the first day of the month; a year-precision value as 1 January.
--     precision = 'unknown' <=> the date column is NULL.
--   * NULL in a free-text or numeric column means "not recorded in the case file".
--     Categorical columns use the explicit value 'unknown' instead of NULL.

CREATE SCHEMA IF NOT EXISTS cases;
CREATE SCHEMA IF NOT EXISTS corpus;
CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS audit;

COMMENT ON SCHEMA cases  IS 'Synthetic teaching cases P-001..P-010. Fictional; never real patient data.';
COMMENT ON SCHEMA corpus IS 'Authorized research documents and chunks (Batch 05). Separate from patient cases.';
COMMENT ON SCHEMA auth   IS 'Users and sessions (Batch 04). Separate from patient cases.';
COMMENT ON SCHEMA audit  IS 'Activity and audit events (Batch 09). Separate from patient cases.';

-- ---------- shared types and helpers ----------

CREATE DOMAIN cases.nonempty_text AS text
  CHECK (VALUE IS NULL OR btrim(VALUE) <> '');

CREATE DOMAIN cases.date_precision AS text
  CHECK (VALUE IN ('day', 'month', 'year', 'unknown'));

CREATE DOMAIN cases.info_source AS text
  CHECK (VALUE IN ('self_report', 'clinician_observation', 'family_report',
                   'screening_tool', 'lab_test', 'records', 'unknown'));

CREATE DOMAIN cases.use_category AS text
  CHECK (VALUE IN ('alcohol', 'cannabis', 'opioids', 'stimulants', 'behavioral',
                   'tobacco', 'sedatives', 'other'));

-- True when (d, p) is a well-formed partial date.
CREATE FUNCTION cases.valid_partial_date(d date, p text)
RETURNS boolean
LANGUAGE sql IMMUTABLE
AS $$
  SELECT CASE p
    WHEN 'unknown' THEN d IS NULL
    WHEN 'day'     THEN d IS NOT NULL
    WHEN 'month'   THEN d IS NOT NULL AND extract(day FROM d) = 1
    WHEN 'year'    THEN d IS NOT NULL AND extract(day FROM d) = 1 AND extract(month FROM d) = 1
    ELSE false
  END
$$;

-- Human-readable partial date: '2024', '2024-03', '2024-03-15', or 'date not recorded'.
CREATE FUNCTION cases.format_partial_date(d date, p text)
RETURNS text
LANGUAGE sql IMMUTABLE
AS $$
  SELECT CASE p
    WHEN 'day'   THEN to_char(d, 'YYYY-MM-DD')
    WHEN 'month' THEN to_char(d, 'YYYY-MM')
    WHEN 'year'  THEN to_char(d, 'YYYY')
    ELSE 'date not recorded'
  END
$$;

CREATE FUNCTION cases.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END
$$;

-- ---------- patients ----------

CREATE TABLE cases.patients (
  patient_id          text PRIMARY KEY
                      CONSTRAINT patients_id_format CHECK (patient_id ~ '^P-[0-9]{3}$'),
  is_synthetic        boolean NOT NULL DEFAULT true
                      CONSTRAINT patients_is_synthetic CHECK (is_synthetic),
  synthetic_notice    cases.nonempty_text NOT NULL
                      DEFAULT 'Synthetic teaching case. Fictional person; not a real patient or clinical record.',
  case_as_of          date NOT NULL,
  age_years           smallint NOT NULL CONSTRAINT patients_adult CHECK (age_years BETWEEN 18 AND 100),
  gender              text NOT NULL
                      CHECK (gender IN ('female', 'male', 'non_binary', 'not_recorded')),
  primary_category    text NOT NULL
                      CHECK (primary_category IN ('alcohol', 'cannabis', 'opioids', 'stimulants', 'behavioral')),
  primary_concern_label cases.nonempty_text NOT NULL,
  presenting_concern  cases.nonempty_text NOT NULL,
  brief_history       cases.nonempty_text NOT NULL,
  treatment_summary   cases.nonempty_text NOT NULL,
  occupation          cases.nonempty_text,
  living_situation    cases.nonempty_text NOT NULL,
  social_context      cases.nonempty_text NOT NULL,
  motivation_stage    text NOT NULL
                      CHECK (motivation_stage IN ('precontemplation', 'contemplation', 'preparation',
                                                  'action', 'maintenance', 'unknown')),
  motivation_notes    cases.nonempty_text,
  progress_summary    cases.nonempty_text NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE cases.patients IS
  'One row per synthetic teaching case. primary_category describes the case; it does not determine treatment suitability.';

CREATE INDEX patients_primary_category_idx ON cases.patients (primary_category);

-- ---------- substance_or_behavior_use ----------

CREATE TABLE cases.substance_or_behavior_use (
  use_id              integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  patient_id          text NOT NULL REFERENCES cases.patients (patient_id) ON DELETE CASCADE,
  is_synthetic        boolean NOT NULL DEFAULT true CHECK (is_synthetic),
  target              cases.nonempty_text NOT NULL,
  category            cases.use_category NOT NULL,
  is_primary          boolean NOT NULL,
  first_use_age       smallint CHECK (first_use_age BETWEEN 5 AND 100),
  regular_use_age     smallint CHECK (regular_use_age BETWEEN 5 AND 100),
  pattern_description cases.nonempty_text NOT NULL,
  frequency_at_intake cases.nonempty_text,
  quantity_at_intake  cases.nonempty_text,
  route               text NOT NULL
                      CHECK (route IN ('oral', 'smoked_or_vaped', 'intranasal', 'injected', 'sublingual',
                                       'mixed', 'not_applicable', 'unknown')),
  current_status      text NOT NULL
                      CHECK (current_status IN ('active', 'reduced', 'abstinent', 'stable_on_treatment', 'unknown')),
  notes               cases.nonempty_text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT use_unique_target UNIQUE (patient_id, target),
  CONSTRAINT use_patient_use_key UNIQUE (patient_id, use_id),
  CONSTRAINT use_onset_order CHECK (first_use_age IS NULL OR regular_use_age IS NULL OR first_use_age <= regular_use_age),
  CONSTRAINT use_route_matches_category CHECK ((category = 'behavioral') = (route = 'not_applicable'))
);

COMMENT ON TABLE cases.substance_or_behavior_use IS
  'Substances or behaviours relevant to a synthetic case. Exactly one row per patient is primary.';

CREATE UNIQUE INDEX use_one_primary_per_patient ON cases.substance_or_behavior_use (patient_id) WHERE is_primary;
CREATE INDEX use_category_idx ON cases.substance_or_behavior_use (category);

-- ---------- previous_interventions ----------

CREATE TABLE cases.previous_interventions (
  intervention_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  patient_id          text NOT NULL REFERENCES cases.patients (patient_id) ON DELETE CASCADE,
  is_synthetic        boolean NOT NULL DEFAULT true CHECK (is_synthetic),
  name                cases.nonempty_text NOT NULL,
  intervention_type   text NOT NULL
                      CHECK (intervention_type IN ('brief_intervention', 'psychosocial', 'pharmacological',
                             'withdrawal_management', 'residential_or_inpatient', 'harm_reduction',
                             'family_based', 'self_exclusion_or_blocking', 'monitoring', 'self_directed',
                             'mutual_help', 'other')),
  setting             text NOT NULL
                      CHECK (setting IN ('primary_care', 'emergency_department', 'outpatient', 'inpatient',
                             'community', 'university', 'home', 'online_or_phone', 'self', 'other', 'unknown')),
  start_date          date,
  start_precision     cases.date_precision NOT NULL,
  end_date            date,
  end_precision       cases.date_precision,
  status              text NOT NULL CHECK (status IN ('completed', 'ongoing', 'discontinued', 'unknown')),
  sessions_offered    smallint CHECK (sessions_offered >= 0),
  sessions_attended   smallint CHECK (sessions_attended >= 0),
  outcome_summary     cases.nonempty_text NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT interventions_patient_intervention_key UNIQUE (patient_id, intervention_id),
  CONSTRAINT interventions_start_valid CHECK (cases.valid_partial_date(start_date, start_precision)),
  -- Ongoing: no end at all (precision NULL = not applicable).
  -- Otherwise: an end precision is required; 'unknown' means it ended on an unrecorded date.
  CONSTRAINT interventions_end_valid CHECK (
    (status = 'ongoing' AND end_date IS NULL AND end_precision IS NULL)
    OR (status <> 'ongoing' AND end_precision IS NOT NULL AND cases.valid_partial_date(end_date, end_precision))
  ),
  CONSTRAINT interventions_end_after_start CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date),
  CONSTRAINT interventions_sessions_order CHECK (
    sessions_attended IS NULL OR sessions_offered IS NULL OR sessions_attended <= sessions_offered)
);

COMMENT ON TABLE cases.previous_interventions IS
  'Interventions in a synthetic case history, including ongoing ones. Outcomes are fictional case notes, not research findings.';

CREATE INDEX interventions_patient_start_idx ON cases.previous_interventions (patient_id, start_date);

-- ---------- patient_history_events ----------

CREATE TABLE cases.patient_history_events (
  event_id            integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  patient_id          text NOT NULL REFERENCES cases.patients (patient_id) ON DELETE CASCADE,
  is_synthetic        boolean NOT NULL DEFAULT true CHECK (is_synthetic),
  sequence_no         smallint NOT NULL CHECK (sequence_no > 0),
  event_date          date,
  date_precision      cases.date_precision NOT NULL,
  event_type          text NOT NULL
                      CHECK (event_type IN ('use_onset', 'use_escalation', 'life_event', 'health_event',
                             'occupational_or_academic', 'referral', 'treatment_start', 'treatment_end',
                             'lapse', 'relapse', 'milestone', 'other')),
  description         cases.nonempty_text NOT NULL,
  related_use_id      integer,
  related_intervention_id integer,
  source              cases.info_source NOT NULL DEFAULT 'records',
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT events_unique_sequence UNIQUE (patient_id, sequence_no),
  CONSTRAINT events_date_valid CHECK (cases.valid_partial_date(event_date, date_precision)),
  -- Composite keys guarantee a linked use/intervention belongs to the SAME patient.
  CONSTRAINT events_use_fk FOREIGN KEY (patient_id, related_use_id)
    REFERENCES cases.substance_or_behavior_use (patient_id, use_id),
  CONSTRAINT events_intervention_fk FOREIGN KEY (patient_id, related_intervention_id)
    REFERENCES cases.previous_interventions (patient_id, intervention_id),
  CONSTRAINT events_treatment_linked CHECK (
    event_type NOT IN ('treatment_start', 'treatment_end') OR related_intervention_id IS NOT NULL),
  CONSTRAINT events_use_linked CHECK (
    event_type NOT IN ('use_onset', 'use_escalation') OR related_use_id IS NOT NULL)
);

COMMENT ON TABLE cases.patient_history_events IS
  'Chronological timeline of a synthetic case. sequence_no is the authoritative order (dates may be partial or unknown).';

CREATE INDEX events_patient_date_idx ON cases.patient_history_events (patient_id, event_date);
CREATE INDEX events_related_use_idx ON cases.patient_history_events (related_use_id) WHERE related_use_id IS NOT NULL;
CREATE INDEX events_related_intervention_idx ON cases.patient_history_events (related_intervention_id)
  WHERE related_intervention_id IS NOT NULL;

-- ---------- triggers_and_risk_factors ----------

CREATE TABLE cases.triggers_and_risk_factors (
  factor_id           integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  patient_id          text NOT NULL REFERENCES cases.patients (patient_id) ON DELETE CASCADE,
  is_synthetic        boolean NOT NULL DEFAULT true CHECK (is_synthetic),
  factor_type         text NOT NULL CHECK (factor_type IN ('trigger', 'risk_factor', 'protective_factor')),
  domain              text NOT NULL
                      CHECK (domain IN ('emotional', 'social', 'environmental', 'physical',
                             'occupational_or_academic', 'financial', 'family', 'other')),
  description         cases.nonempty_text NOT NULL,
  salience            text NOT NULL CHECK (salience IN ('low', 'moderate', 'high', 'unknown')),
  source              cases.info_source NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT factors_unique UNIQUE (patient_id, factor_type, description)
);

COMMENT ON TABLE cases.triggers_and_risk_factors IS
  'Triggers, risk factors, and protective factors recorded for a synthetic case.';

CREATE INDEX factors_patient_type_idx ON cases.triggers_and_risk_factors (patient_id, factor_type);

-- ---------- progress_observations ----------

CREATE TABLE cases.progress_observations (
  observation_id      integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  patient_id          text NOT NULL REFERENCES cases.patients (patient_id) ON DELETE CASCADE,
  is_synthetic        boolean NOT NULL DEFAULT true CHECK (is_synthetic),
  observed_on         date,
  observed_precision  cases.date_precision NOT NULL,
  observation_type    text NOT NULL CHECK (observation_type IN ('behavior', 'measure', 'motivation_stage', 'note')),
  label               cases.nonempty_text NOT NULL,
  value_text          cases.nonempty_text NOT NULL,
  value_numeric       numeric,
  unit                cases.nonempty_text,
  baseline_text       cases.nonempty_text,
  source              cases.info_source NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT observations_date_valid CHECK (cases.valid_partial_date(observed_on, observed_precision)),
  CONSTRAINT observations_stage_value CHECK (
    observation_type <> 'motivation_stage'
    OR value_text IN ('precontemplation', 'contemplation', 'preparation', 'action', 'maintenance', 'unknown'))
);

COMMENT ON TABLE cases.progress_observations IS
  'Observed behaviours, progress measures, and motivation-stage readings for a synthetic case.';

CREATE INDEX observations_patient_date_idx ON cases.progress_observations (patient_id, observed_on);
CREATE INDEX observations_patient_type_idx ON cases.progress_observations (patient_id, observation_type);

-- ---------- co_occurring_symptoms ----------

CREATE TABLE cases.co_occurring_symptoms (
  symptom_id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  patient_id          text NOT NULL REFERENCES cases.patients (patient_id) ON DELETE CASCADE,
  is_synthetic        boolean NOT NULL DEFAULT true CHECK (is_synthetic),
  description         cases.nonempty_text NOT NULL,
  domain              text NOT NULL
                      CHECK (domain IN ('mood', 'anxiety', 'sleep', 'trauma_related', 'psychotic',
                             'cognitive', 'physical_health', 'other')),
  status              text NOT NULL CHECK (status IN ('current', 'improving', 'past', 'unknown')),
  noted_since         date,
  noted_precision     cases.date_precision NOT NULL,
  source              cases.info_source NOT NULL,
  notes               cases.nonempty_text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT symptoms_unique UNIQUE (patient_id, description),
  CONSTRAINT symptoms_date_valid CHECK (cases.valid_partial_date(noted_since, noted_precision))
);

COMMENT ON TABLE cases.co_occurring_symptoms IS
  'Co-occurring symptoms described in a synthetic case. These are case descriptions, not diagnoses.';

CREATE INDEX symptoms_patient_idx ON cases.co_occurring_symptoms (patient_id);

-- ---------- updated_at triggers ----------

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['patients', 'substance_or_behavior_use', 'previous_interventions',
                           'patient_history_events', 'triggers_and_risk_factors',
                           'progress_observations', 'co_occurring_symptoms']
  LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE ON cases.%I
                    FOR EACH ROW EXECUTE FUNCTION cases.set_updated_at()', t || '_set_updated_at', t);
  END LOOP;
END
$$;
