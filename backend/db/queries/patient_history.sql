-- Chronological history of one synthetic patient.
-- Order comes from sequence_no (authoritative even when dates are partial or unknown).
-- Linked uses and interventions are resolved by name.
--
-- psql:  psql -h localhost -U mindevidence -d mindevidence -v patient_id=P-001 -f db/queries/patient_history.sql

SELECT e.sequence_no,
       cases.format_partial_date(e.event_date, e.date_precision) AS "when",
       e.event_type,
       e.description,
       u.target                                                  AS related_use,
       i.name                                                    AS related_intervention,
       i.status                                                  AS intervention_status,
       e.source
FROM cases.patient_history_events e
LEFT JOIN cases.substance_or_behavior_use u ON u.use_id = e.related_use_id
LEFT JOIN cases.previous_interventions  i ON i.intervention_id = e.related_intervention_id
WHERE e.patient_id = :'patient_id'
ORDER BY e.sequence_no;
