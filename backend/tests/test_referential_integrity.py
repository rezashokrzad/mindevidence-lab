"""Foreign keys, orphan checks, and constraints that block inconsistent data."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import scalar

CHILD_TABLES = [
    "substance_or_behavior_use", "previous_interventions", "patient_history_events",
    "triggers_and_risk_factors", "progress_observations", "co_occurring_symptoms",
]


def test_row_counts_match_seed_file(conn, seed_data):
    patients = seed_data["patients"]
    expected = {
        "patients": len(patients),
        "substance_or_behavior_use": sum(len(p["uses"]) for p in patients),
        "previous_interventions": sum(len(p["interventions"]) for p in patients),
        "patient_history_events": sum(len(p["events"]) for p in patients),
        "triggers_and_risk_factors": sum(len(p["factors"]) for p in patients),
        "progress_observations": sum(len(p["observations"]) for p in patients),
        "co_occurring_symptoms": sum(len(p["symptoms"]) for p in patients),
    }
    for table, n in expected.items():
        assert scalar(conn, f"SELECT count(*) FROM cases.{table}") == n, table


@pytest.mark.parametrize("table", CHILD_TABLES)
def test_child_table_has_patient_foreign_key(conn, table):
    n = scalar(conn, """
        SELECT count(*) FROM pg_constraint
        WHERE contype = 'f' AND conrelid = ('cases.' || :t)::regclass
          AND confrelid = 'cases.patients'::regclass""", t=table)
    assert n == 1


@pytest.mark.parametrize("table", CHILD_TABLES)
def test_no_orphan_rows(conn, table):
    assert scalar(conn, f"""
        SELECT count(*) FROM cases.{table} c
        LEFT JOIN cases.patients p ON p.patient_id = c.patient_id
        WHERE p.patient_id IS NULL""") == 0


def test_event_links_resolve_to_same_patient(conn):
    assert scalar(conn, """
        SELECT count(*) FROM cases.patient_history_events e
        LEFT JOIN cases.previous_interventions i ON i.intervention_id = e.related_intervention_id
        LEFT JOIN cases.substance_or_behavior_use u ON u.use_id = e.related_use_id
        WHERE (e.related_intervention_id IS NOT NULL AND i.patient_id IS DISTINCT FROM e.patient_id)
           OR (e.related_use_id IS NOT NULL AND u.patient_id IS DISTINCT FROM e.patient_id)""") == 0


def test_all_foreign_keys_validated(conn):
    assert scalar(conn, """
        SELECT count(*) FROM pg_constraint
        WHERE contype = 'f' AND connamespace = 'cases'::regnamespace AND NOT convalidated""") == 0


# ---------- the schema rejects inconsistent writes ----------

def _rejects(conn, sql, **params):
    sp = conn.begin_nested()
    with pytest.raises(DBAPIError):
        conn.execute(text(sql), params)
    sp.rollback()


def test_rejects_event_linked_to_another_patients_intervention(conn):
    other = scalar(conn, "SELECT min(intervention_id) FROM cases.previous_interventions WHERE patient_id = 'P-002'")
    _rejects(conn, """
        INSERT INTO cases.patient_history_events
          (patient_id, sequence_no, event_date, date_precision, event_type, description, related_intervention_id)
        VALUES ('P-001', 99, '2026-01-01', 'month', 'treatment_start', 'cross-patient link', :iid)""", iid=other)


def test_rejects_second_primary_use(conn):
    _rejects(conn, """
        INSERT INTO cases.substance_or_behavior_use
          (patient_id, target, category, is_primary, pattern_description, route, current_status)
        VALUES ('P-001', 'cannabis', 'cannabis', true, 'test', 'smoked_or_vaped', 'active')""")


def test_rejects_non_synthetic_row(conn):
    _rejects(conn, "UPDATE cases.patients SET is_synthetic = false WHERE patient_id = 'P-001'")


def test_rejects_minor(conn):
    _rejects(conn, "UPDATE cases.patients SET age_years = 17 WHERE patient_id = 'P-009'")


def test_rejects_malformed_partial_date(conn):
    _rejects(conn, """
        UPDATE cases.patient_history_events SET event_date = '2020-05-17', date_precision = 'month'
        WHERE patient_id = 'P-003' AND sequence_no = 1""")


def test_rejects_ongoing_intervention_with_end_date(conn):
    _rejects(conn, """
        UPDATE cases.previous_interventions SET end_date = '2026-09-01', end_precision = 'month'
        WHERE status = 'ongoing' AND patient_id = 'P-001'""")


def test_rejects_treatment_event_without_intervention(conn):
    _rejects(conn, """
        INSERT INTO cases.patient_history_events
          (patient_id, sequence_no, date_precision, event_type, description)
        VALUES ('P-001', 99, 'unknown', 'treatment_start', 'unlinked')""")


def test_rejects_behavioral_use_with_route(conn):
    _rejects(conn, "UPDATE cases.substance_or_behavior_use SET route = 'oral' WHERE category = 'behavioral'")


def test_rejects_blank_text(conn):
    _rejects(conn, "UPDATE cases.patients SET presenting_concern = '   ' WHERE patient_id = 'P-001'")


def test_deleting_a_patient_cascades_without_orphans(conn):
    sp = conn.begin_nested()
    conn.execute(text("DELETE FROM cases.patients WHERE patient_id = 'P-005'"))
    for table in CHILD_TABLES:
        assert scalar(conn, f"SELECT count(*) FROM cases.{table} WHERE patient_id = 'P-005'") == 0
    sp.rollback()


def test_updated_at_triggers_installed(conn):
    assert scalar(conn, """
        SELECT count(*) FROM pg_trigger
        WHERE NOT tgisinternal AND tgname LIKE '%\\_set\\_updated\\_at'
          AND tgrelid::regclass::text LIKE 'cases.%'""") == 7
