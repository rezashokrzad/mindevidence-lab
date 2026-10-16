"""Checks on the seed JSON itself. No database required."""
import json
import re

from db.seed import EXPECTED_IDS, PartialDate, SeedError, validate

import pytest

CONTACT_PATTERNS = [
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),  # email
    re.compile(r"\+?\d[\d ().-]{8,}\d"),                               # phone-like
    re.compile(r"https?://|www\.", re.I),                               # URL
]


def all_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from all_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_strings(v)


def test_seed_file_validates(seed_data):
    assert validate(seed_data) == []


def test_exactly_ten_stable_ids(seed_data):
    assert [p["patient_id"] for p in seed_data["patients"]] == EXPECTED_IDS


def test_dataset_is_labelled_synthetic(seed_data):
    notice = seed_data["meta"]["notice"].lower()
    assert "fictional" in notice and "not real people" in notice


def test_all_five_categories_covered(seed_data):
    categories = {p["primary_category"] for p in seed_data["patients"]}
    assert categories == {"alcohol", "cannabis", "opioids", "stimulants", "behavioral"}


def test_same_category_cases_differ(seed_data):
    """Cases sharing a category must differ in stage or primary-use status,
    so category alone never describes a case."""
    by_cat = {}
    for p in seed_data["patients"]:
        primary = next(u for u in p["uses"] if u["is_primary"])
        by_cat.setdefault(p["primary_category"], []).append(
            (p["motivation_stage"], primary["current_status"], primary["pattern_description"]))
    for cat, profiles in by_cat.items():
        assert len(set(profiles)) == len(profiles), f"{cat} cases are indistinguishable"


def test_explicit_unknowns_present(seed_data):
    """The dataset deliberately records missing values (not invented)."""
    unknown_routes = [u for p in seed_data["patients"] for u in p["uses"] if u["route"] == "unknown"]
    null_occupation = [p for p in seed_data["patients"] if p.get("occupation") is None]
    undated_symptoms = [s for p in seed_data["patients"] for s in p["symptoms"] if s.get("since") is None]
    unknown_status = [s for p in seed_data["patients"] for s in p["symptoms"] if s["status"] == "unknown"]
    assert unknown_routes and null_occupation and undated_symptoms and unknown_status


def test_no_contact_details_or_links(seed_data):
    for s in all_strings(seed_data["patients"]):
        for pattern in CONTACT_PATTERNS:
            assert not pattern.search(s), f"looks like contact info or a link: {s!r}"


def test_no_research_claims_in_case_text(seed_data):
    """Case text describes a fictional person; it must not read as a research finding."""
    banned = re.compile(r"\b(studies|study shows|evidence shows|research shows|trial|meta-analysis|proven)\b", re.I)
    for s in all_strings(seed_data["patients"]):
        assert not banned.search(s), f"reads like a research claim: {s!r}"


@pytest.mark.parametrize("raw, precision", [
    ("2024", "year"), ("2024-03", "month"), ("2024-03-15", "day"), (None, "unknown"), ("unknown", "unknown"),
])
def test_partial_date_parsing(raw, precision):
    assert PartialDate.parse(raw).precision == precision


def test_partial_date_rejects_garbage():
    with pytest.raises(SeedError):
        PartialDate.parse("March 2024")


def test_validator_catches_broken_references(seed_data):
    broken = json.loads(json.dumps(seed_data))
    broken["patients"][0]["events"][1]["intervention"] = "does-not-exist"
    broken["patients"][1]["events"][0]["date"] = "2030-01"
    problems = validate(broken)
    assert any("unknown intervention" in p for p in problems)
    assert any("after case_as_of" in p for p in problems)

