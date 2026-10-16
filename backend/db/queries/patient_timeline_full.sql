-- Merged timeline for one synthetic patient: history events, motivation-stage
-- readings, and progress measures in one chronological list.
-- Rows with an unknown date sort last. Within the same date, events come before
-- observations and keep their sequence order.
--
-- psql:  psql -h localhost -U mindevidence -d mindevidence -v patient_id=P-008 -f db/queries/patient_timeline_full.sql

SELECT cases.format_partial_date(t.d, t.precision) AS "when",
       t.kind,
       t.label,
       t.detail
FROM (
  SELECT e.event_date AS d, e.date_precision AS precision, 1 AS kind_order, e.sequence_no AS item_order,
         'event' AS kind, e.event_type AS label, e.description AS detail
  FROM cases.patient_history_events e
  WHERE e.patient_id = :'patient_id'

  UNION ALL
  SELECT o.observed_on, o.observed_precision, 2, o.observation_id,
         'observation:' || o.observation_type, o.label,
         o.value_text || coalesce(' (baseline ' || o.baseline_text || ')', '')
  FROM cases.progress_observations o
  WHERE o.patient_id = :'patient_id' AND o.observation_type IN ('motivation_stage', 'measure')
) t
ORDER BY t.d NULLS LAST, t.kind_order, t.item_order;
