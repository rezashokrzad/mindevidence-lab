"""Run every rule in db/checks/consistency_checks.sql; each must return no rows."""
import pytest
from sqlalchemy import text

from tests.conftest import load_checks

CHECKS = load_checks()


def test_checks_file_parsed():
    assert len(CHECKS) >= 10


@pytest.mark.parametrize("name, sql", CHECKS, ids=[n for n, _ in CHECKS])
def test_consistency_rule(conn, name, sql):
    violations = conn.execute(text(sql)).mappings().all()
    assert violations == [], f"{name} violated:\n" + "\n".join(str(dict(v)) for v in violations)


def test_checks_detect_planted_contradictions(conn):
    """Guard against checks that can never fail: break the data, expect violations."""
    checks = dict(CHECKS)
    sp = conn.begin_nested()
    conn.execute(text("UPDATE cases.patients SET motivation_stage = 'maintenance' WHERE patient_id = 'P-009'"))
    conn.execute(text(
        "UPDATE cases.patient_history_events SET event_date = '2010-01-01', date_precision = 'year' "
        "WHERE patient_id = 'P-001' AND sequence_no = 7"))
    conn.execute(text("UPDATE cases.substance_or_behavior_use SET first_use_age = 18 WHERE patient_id = 'P-003'"))
    assert conn.execute(text(checks["current_stage_matches_latest_observation"])).all()
    assert conn.execute(text(checks["event_sequence_is_chronological"])).all()
    assert conn.execute(text(checks["every_intervention_starts_on_the_timeline"])).all()
    assert conn.execute(text(checks["onset_ages_match_timeline"])).all()
    sp.rollback()
