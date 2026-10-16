-- 0006 down
DROP TABLE IF EXISTS auth.email_report_consents;
DROP TABLE IF EXISTS audit.feedback;
DROP TRIGGER IF EXISTS events_append_only ON audit.events;
DROP TABLE IF EXISTS audit.events;
DROP FUNCTION IF EXISTS audit.forbid_change();
