-- One comparison row per synthetic case. It is useful for classroom discussion of how cases in the
-- SAME category differ (stage, relapse history, supports), and of why category alone
-- does not decide treatment suitability.
--
-- psql:  psql -h localhost -U mindevidence -d mindevidence -f db/queries/compare_cases.sql

SELECT p.patient_id,
       p.primary_category,
       pu.target                                         AS primary_target,
       p.age_years,
       p.motivation_stage,
       pu.current_status                                 AS primary_use_status,
       (SELECT count(*) FROM cases.patient_history_events e
         WHERE e.patient_id = p.patient_id AND e.event_type IN ('relapse', 'lapse'))       AS relapses_or_lapses,
       (SELECT count(*) FROM cases.previous_interventions i WHERE i.patient_id = p.patient_id) AS interventions,
       (SELECT count(*) FROM cases.previous_interventions i
         WHERE i.patient_id = p.patient_id AND i.status = 'ongoing')                      AS ongoing,
       (SELECT count(*) FROM cases.triggers_and_risk_factors f
         WHERE f.patient_id = p.patient_id AND f.factor_type = 'trigger')                 AS triggers,
       (SELECT count(*) FROM cases.triggers_and_risk_factors f
         WHERE f.patient_id = p.patient_id AND f.factor_type = 'protective_factor')       AS protective_factors,
       (SELECT string_agg(DISTINCT s.domain, ', ' ORDER BY s.domain) FROM cases.co_occurring_symptoms s
         WHERE s.patient_id = p.patient_id AND s.status IN ('current', 'improving'))       AS current_symptom_domains
FROM cases.patients p
JOIN cases.substance_or_behavior_use pu ON pu.patient_id = p.patient_id AND pu.is_primary
ORDER BY p.primary_category, p.patient_id;
