"""GET /api/patients, /api/patients/{id}, /api/patients/{id}/history."""
import pytest

from db.seed import EXPECTED_IDS


def assert_error(resp, status, code):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    assert body["error"]["request_id"] == resp.headers["X-Request-ID"]
    return body["error"]


# ---------- list ----------

def test_list_returns_all_ten_synthetic_patients(client):
    r = client.get("/api/patients", params={"limit": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 10 and body["limit"] == 100 and body["offset"] == 0
    assert [p["patient_id"] for p in body["items"]] == EXPECTED_IDS
    assert all(p["is_synthetic"] is True for p in body["items"])


def test_list_default_page_size(client):
    assert client.get("/api/patients").json()["limit"] == 20


def test_list_pagination(client):
    body = client.get("/api/patients", params={"limit": 3, "offset": 3}).json()
    assert [p["patient_id"] for p in body["items"]] == ["P-004", "P-005", "P-006"]
    assert body["total"] == 10


def test_list_offset_past_end_is_empty(client):
    body = client.get("/api/patients", params={"offset": 50}).json()
    assert body["items"] == [] and body["total"] == 10


def test_list_category_filter(client):
    body = client.get("/api/patients", params={"category": "behavioral"}).json()
    assert [p["patient_id"] for p in body["items"]] == ["P-008", "P-009"]
    assert body["total"] == 2


def test_list_text_search(client):
    body = client.get("/api/patients", params={"q": "betting"}).json()
    assert [p["patient_id"] for p in body["items"]] == ["P-008"]


def test_list_search_treats_wildcards_literally(client):
    assert client.get("/api/patients", params={"q": "%"}).json()["total"] == 0
    assert client.get("/api/patients", params={"q": "_"}).json()["total"] == 0


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "ten"},
    {"category": "nicotine"}, {"q": ""}, {"q": "x" * 101},
])
def test_list_rejects_invalid_query(client, params):
    err = assert_error(client.get("/api/patients", params=params), 422, "validation_error")
    assert err["details"] and err["details"][0]["loc"][0] == "query"


# ---------- detail ----------

@pytest.mark.parametrize("patient_id", EXPECTED_IDS)
def test_detail_matches_seed(client, seed_data, patient_id):
    src = next(p for p in seed_data["patients"] if p["patient_id"] == patient_id)
    r = client.get(f"/api/patients/{patient_id}")
    assert r.status_code == 200
    d = r.json()
    assert d["is_synthetic"] is True and "synthetic" in d["synthetic_notice"].lower()
    assert d["primary_category"] == src["primary_category"]
    assert d["age_years"] == src["age_years"]
    assert d["motivation"]["stage"] == src["motivation_stage"] == d["motivation_stage"]
    assert d["progress_summary"] == src["progress_summary"]
    assert len(d["uses"]) == len(src["uses"]) and d["uses"][0]["is_primary"]
    assert len(d["previous_interventions"]) == len(src["interventions"])
    n_factors = len(d["triggers"]) + len(d["risk_factors"]) + len(d["protective_factors"])
    assert n_factors == len(src["factors"])
    n_obs = len(d["observed_behaviors"]) + len(d["progress_measures"]) + len(d["stage_history"])
    assert n_obs == len(src["observations"])
    assert len(d["co_occurring_symptoms"]) == len(src["symptoms"])


def test_detail_partial_dates_and_unknowns(client):
    d = client.get("/api/patients/P-005").json()
    assert d["occupation"] is None  # explicitly not recorded
    meth1 = next(i for i in d["previous_interventions"] if i["name"].startswith("Methadone maintenance (multiple"))
    assert meth1["start"] == {"value": "2004", "precision": "year"}
    assert meth1["end"] == {"value": "2015", "precision": "year"}
    ongoing = next(i for i in d["previous_interventions"] if i["status"] == "ongoing")
    assert ongoing["end"] is None
    low_mood = next(s for s in d["co_occurring_symptoms"] if s["description"] == "Low mood")
    assert low_mood["noted_since"] == {"value": None, "precision": "unknown"}


@pytest.mark.parametrize("bad_id", ["P-1", "P-0011", "p-001", "abc", "P-00A", "P001"])
def test_detail_malformed_id_is_422(client, bad_id):
    err = assert_error(client.get(f"/api/patients/{bad_id}"), 422, "validation_error")
    assert err["details"][0]["loc"] == ["path", "patient_id"]


def test_detail_unknown_id_is_404(client):
    err = assert_error(client.get("/api/patients/P-999"), 404, "patient_not_found")
    assert "P-999" in err["message"]


# ---------- history ----------

def test_history_in_sequence_with_links(client, seed_data):
    src = seed_data["patients"][3]  # P-004
    body = client.get("/api/patients/P-004/history", params={"limit": 100}).json()
    assert body["patient_id"] == "P-004" and body["total"] == len(src["events"])
    assert [e["sequence_no"] for e in body["items"]] == list(range(1, len(src["events"]) + 1))
    assert [e["description"] for e in body["items"]] == [e["description"] for e in src["events"]]
    detox = body["items"][3]
    assert detox["event_type"] == "treatment_start"
    assert detox["related_intervention"]["name"] == "Inpatient detoxification"
    assert detox["date"] == {"value": "2021-08", "precision": "month"}
    assert body["items"][2]["related_use"]["target"] == "heroin"


def test_history_pagination(client):
    body = client.get("/api/patients/P-001/history", params={"limit": 2, "offset": 2}).json()
    assert [e["sequence_no"] for e in body["items"]] == [3, 4]
    assert body["total"] == 7


def test_history_unknown_patient_is_404(client):
    assert_error(client.get("/api/patients/P-999/history"), 404, "patient_not_found")


def test_history_malformed_id_is_422(client):
    assert_error(client.get("/api/patients/P-1/history"), 422, "validation_error")
