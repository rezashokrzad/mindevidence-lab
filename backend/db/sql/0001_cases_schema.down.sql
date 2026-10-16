-- 0001 down · Remove synthetic patient-case schema.
-- corpus/auth/audit are dropped only if empty (RESTRICT), so a later batch's
-- tables are never removed by accident; those batches downgrade first.

DROP SCHEMA IF EXISTS cases CASCADE;
DROP SCHEMA IF EXISTS audit RESTRICT;
DROP SCHEMA IF EXISTS auth RESTRICT;
DROP SCHEMA IF EXISTS corpus RESTRICT;
