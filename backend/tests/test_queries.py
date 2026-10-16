"""Sample queries in db/queries return complete, ordered results for every case."""
import pytest
from sqlalchemy import text

from db.seed import EXPECTED_IDS
from tests.conftest import read_query


def _source(seed_data, patient_id):
    return next(p for p in seed_data["patients"] if p["patient_id"] == patient_id)


@pytest.mark.parametrize("patient_id", EXPECTED_IDS)
def test_profile_matches_seed(conn, seed_data, patient_id):
    src = _source(seed_data, patient_id)
    profile = conn.execute(text(read_query("patient_profile.sql")), {"patient_id": patient_id}).scalar_one()

    assert profile["is_synthetic"] is True
    assert "synthetic" in profile["synthetic_notice"].lower()
    assert profile["primary_category"] == src["primary_category"]
    assert len(profile["history"]) == len(src["events"])
    assert len(profile["previous_interventions"]) == len(src["interventions"])
    assert len(profile["uses"]) == len(src["uses"])
    assert profile["uses"][0]["is_primary"] is True
    factors = profile["triggers"] + profile["risk_factors"] + profile["protective_factors"]
    assert len(factors) == len(src["factors"])
    observations = profile["observed_behaviors"] + profile["progress_measures"] + profile["stage_history"]
    assert len(observations) == len(src["observations"])
    assert len(profile["co_occurring_symptoms"]) == len(src["symptoms"])
    assert profile["stage_history"][-1]["stage"] == profile["motivation"]["stage"]
    assert profile["progress_summary"] == src["progress_summary"]


def test_profile_for_unknown_patient_is_empty(conn):
    rows = conn.execute(text(read_query("patient_profile.sql")), {"patient_id": "P-999"}).all()
    assert rows == []


@pytest.mark.parametrize("patient_id", EXPECTED_IDS)
def test_history_is_in_sequence_and_matches_seed(conn, seed_data, patient_id):
    src = _source(seed_data, patient_id)
    rows = conn.execute(text(read_query("patient_history.sql")), {"patient_id": patient_id}).mappings().all()
    assert [r["sequence_no"] for r in rows] == list(range(1, len(src["events"]) + 1))
    assert [r["description"] for r in rows] == [e["description"] for e in src["events"]]
    assert [r["when"] for r in rows] == [e.get("date") or "date not recorded" for e in src["events"]]
    for r, e in zip(rows, src["events"]):
        assert (r["related_intervention"] is not None) == bool(e.get("intervention"))


@pytest.mark.parametrize("patient_id", EXPECTED_IDS)
def test_full_timeline_is_chronological(conn, patient_id):
    rows = conn.execute(text(read_query("patient_timeline_full.sql")), {"patient_id": patient_id}).mappings().all()
    dated = [r["when"] for r in rows if r["when"] != "date not recorded"]
    # Partial dates sort correctly as strings at their own precision ('2025' < '2025-07').
    assert dated == sorted(dated, key=lambda w: (w[:4], w))
    kinds = {r["kind"] for r in rows}
    assert {"event", "observation:motivation_stage", "observation:measure"} <= kinds


def test_compare_cases_covers_all_patients(conn):
    rows = conn.execute(text(read_query("compare_cases.sql"))).mappings().all()
    assert sorted(r["patient_id"] for r in rows) == EXPECTED_IDS
    alcohol = [r for r in rows if r["primary_category"] == "alcohol"]
    assert len(alcohol) == 3
    assert len({(r["motivation_stage"], r["relapses_or_lapses"], r["age_years"]) for r in alcohol}) == 3
