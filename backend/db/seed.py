"""Load the synthetic teaching cases into the `cases` schema.

Research documents are NOT seeded here: they come from data/corpus_manifest.json
through the ingestion pipeline (python -m app.ingestion run).

    python -m db.seed                      # seed DATABASE_URL
    python -m db.seed --url-env TEST_DATABASE_URL
    python -m db.seed --validate-only      # check the JSON file, no database

Reproducible: every run truncates the case tables, restarts identities, and
inserts the same rows in the same order in one transaction.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SEED_FILE = Path(__file__).resolve().parent / "seed_data" / "synthetic_cases.json"
EXPECTED_IDS = [f"P-{n:03d}" for n in range(1, 11)]

CASE_TABLES = [
    "patient_history_events",  # references uses + interventions
    "progress_observations",
    "co_occurring_symptoms",
    "triggers_and_risk_factors",
    "previous_interventions",
    "substance_or_behavior_use",
    "patients",
]


class SeedError(ValueError):
    pass


# ---------- partial dates ----------

_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


@dataclass(frozen=True)
class PartialDate:
    value: date | None
    precision: str  # day | month | year | unknown

    @classmethod
    def parse(cls, raw: str | None) -> "PartialDate":
        if raw is None or raw == "unknown":
            return cls(None, "unknown")
        m = _DATE_RE.match(raw)
        if not m:
            raise SeedError(f"bad date {raw!r}; use YYYY, YYYY-MM or YYYY-MM-DD")
        year, month, day = m.groups()
        if day:
            return cls(date(int(year), int(month), int(day)), "day")
        if month:
            return cls(date(int(year), int(month), 1), "month")
        return cls(date(int(year), 1, 1), "year")


# ---------- validation (no database needed) ----------

def load_seed(path: Path = SEED_FILE) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def validate(data: dict) -> list[str]:
    """Return a list of problems. Structural checks only; the database
    constraints and db/checks/consistency_checks.sql cover the rest."""
    problems: list[str] = []
    as_of = date.fromisoformat(data["meta"]["case_as_of"])
    ids = [p["patient_id"] for p in data["patients"]]
    if ids != EXPECTED_IDS:
        problems.append(f"patient ids must be exactly {EXPECTED_IDS} in order, got {ids}")

    for p in data["patients"]:
        pid = p["patient_id"]
        uses = {u["key"]: u for u in p["uses"]}
        interventions = {i["key"]: i for i in p["interventions"]}
        if len(uses) != len(p["uses"]):
            problems.append(f"{pid}: duplicate use keys")
        if len(interventions) != len(p["interventions"]):
            problems.append(f"{pid}: duplicate intervention keys")
        primaries = [u for u in p["uses"] if u["is_primary"]]
        if len(primaries) != 1:
            problems.append(f"{pid}: expected exactly one primary use, found {len(primaries)}")
        elif primaries[0]["category"] != p["primary_category"]:
            problems.append(f"{pid}: primary use category {primaries[0]['category']} != {p['primary_category']}")

        def check_date(raw, where):
            try:
                d = PartialDate.parse(raw)
            except SeedError as exc:
                problems.append(f"{pid} {where}: {exc}")
                return None
            if d.value and d.value > as_of:
                problems.append(f"{pid} {where}: {raw} is after case_as_of {as_of}")
            return d

        for ev in p["events"]:
            check_date(ev.get("date"), f"event {ev['description'][:40]!r}")
            if ev.get("use") and ev["use"] not in uses:
                problems.append(f"{pid}: event references unknown use {ev['use']!r}")
            if ev.get("intervention") and ev["intervention"] not in interventions:
                problems.append(f"{pid}: event references unknown intervention {ev['intervention']!r}")
        for iv in p["interventions"]:
            check_date(iv.get("start"), f"intervention {iv['key']} start")
            if iv.get("end") is not None:
                check_date(iv["end"], f"intervention {iv['key']} end")
        for ob in p["observations"]:
            check_date(ob.get("date"), f"observation {ob['label']!r}")
        for sy in p.get("symptoms", []):
            check_date(sy.get("since"), f"symptom {sy['description']!r}")
    return problems


# ---------- database load ----------

def _patient_rows(data: dict):
    as_of = data["meta"]["case_as_of"]
    for p in data["patients"]:
        yield {
            "patient_id": p["patient_id"],
            "case_as_of": as_of,
            "age_years": p["age_years"],
            "gender": p["gender"],
            "primary_category": p["primary_category"],
            "primary_concern_label": p["primary_concern_label"],
            "presenting_concern": p["presenting_concern"],
            "brief_history": p["brief_history"],
            "treatment_summary": p["treatment_summary"],
            "occupation": p.get("occupation"),
            "living_situation": p["living_situation"],
            "social_context": p["social_context"],
            "motivation_stage": p["motivation_stage"],
            "motivation_notes": p.get("motivation_notes"),
            "progress_summary": p["progress_summary"],
        }


def seed(url_env: str = "DATABASE_URL") -> dict[str, int]:
    from sqlalchemy import create_engine, text

    from app.config import database_url

    data = load_seed()
    problems = validate(data)
    if problems:
        raise SeedError("seed file is invalid:\n  " + "\n  ".join(problems))

    engine = create_engine(database_url(url_env))
    counts = {t: 0 for t in CASE_TABLES}
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(f"cases.{t}" for t in CASE_TABLES) + " RESTART IDENTITY"))

        def insert(table: str, row: dict, returning: str | None = None):
            cols = ", ".join(row)
            params = ", ".join(f":{c}" for c in row)
            sql = f"INSERT INTO cases.{table} ({cols}) VALUES ({params})"
            if returning:
                sql += f" RETURNING {returning}"
            result = conn.execute(text(sql), row)
            counts[table] += 1
            return result.scalar_one() if returning else None

        for row in _patient_rows(data):
            insert("patients", row)

        for p in data["patients"]:
            pid = p["patient_id"]
            use_ids, intervention_ids = {}, {}

            for u in p["uses"]:
                use_ids[u["key"]] = insert("substance_or_behavior_use", {
                    "patient_id": pid,
                    "target": u["target"],
                    "category": u["category"],
                    "is_primary": u["is_primary"],
                    "first_use_age": u.get("first_use_age"),
                    "regular_use_age": u.get("regular_use_age"),
                    "pattern_description": u["pattern_description"],
                    "frequency_at_intake": u.get("frequency_at_intake"),
                    "quantity_at_intake": u.get("quantity_at_intake"),
                    "route": u["route"],
                    "current_status": u["current_status"],
                    "notes": u.get("notes"),
                }, returning="use_id")

            for iv in p["interventions"]:
                start = PartialDate.parse(iv.get("start"))
                ongoing = iv["status"] == "ongoing"
                end = None if ongoing else PartialDate.parse(iv.get("end"))
                intervention_ids[iv["key"]] = insert("previous_interventions", {
                    "patient_id": pid,
                    "name": iv["name"],
                    "intervention_type": iv["intervention_type"],
                    "setting": iv["setting"],
                    "start_date": start.value,
                    "start_precision": start.precision,
                    "end_date": end.value if end else None,
                    "end_precision": end.precision if end else None,
                    "status": iv["status"],
                    "sessions_offered": iv.get("sessions_offered"),
                    "sessions_attended": iv.get("sessions_attended"),
                    "outcome_summary": iv["outcome_summary"],
                }, returning="intervention_id")

            for seq, ev in enumerate(p["events"], start=1):
                d = PartialDate.parse(ev.get("date"))
                insert("patient_history_events", {
                    "patient_id": pid,
                    "sequence_no": seq,
                    "event_date": d.value,
                    "date_precision": d.precision,
                    "event_type": ev["type"],
                    "description": ev["description"],
                    "related_use_id": use_ids.get(ev.get("use")),
                    "related_intervention_id": intervention_ids.get(ev.get("intervention")),
                    "source": ev.get("source", "records"),
                })

            for f in p["factors"]:
                insert("triggers_and_risk_factors", {
                    "patient_id": pid,
                    "factor_type": f["type"],
                    "domain": f["domain"],
                    "description": f["description"],
                    "salience": f["salience"],
                    "source": f["source"],
                })

            for ob in p["observations"]:
                d = PartialDate.parse(ob.get("date"))
                insert("progress_observations", {
                    "patient_id": pid,
                    "observed_on": d.value,
                    "observed_precision": d.precision,
                    "observation_type": ob["type"],
                    "label": ob["label"],
                    "value_text": ob["value"],
                    "value_numeric": ob.get("numeric"),
                    "unit": ob.get("unit"),
                    "baseline_text": ob.get("baseline"),
                    "source": ob["source"],
                })

            for sy in p.get("symptoms", []):
                d = PartialDate.parse(sy.get("since"))
                insert("co_occurring_symptoms", {
                    "patient_id": pid,
                    "description": sy["description"],
                    "domain": sy["domain"],
                    "status": sy["status"],
                    "noted_since": d.value,
                    "noted_precision": d.precision,
                    "source": sy["source"],
                    "notes": sy.get("notes"),
                })

    engine.dispose()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url-env", default="DATABASE_URL", help="environment variable holding the database URL")
    parser.add_argument("--validate-only", action="store_true", help="validate the seed file without a database")
    args = parser.parse_args(argv)

    if args.validate_only:
        problems = validate(load_seed())
        for p in problems:
            print("✗", p)
        print("seed file OK" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0

    counts = seed(args.url_env)
    for table, n in counts.items():
        print(f"  cases.{table:<28} {n:>4} rows")
    print("Seeded 10 synthetic cases.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
