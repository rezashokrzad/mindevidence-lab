"""Schema layout and migration round-trip."""
from sqlalchemy import create_engine, text

from tests.conftest import alembic_config

CASE_TABLES = {
    "patients", "substance_or_behavior_use", "previous_interventions", "patient_history_events",
    "triggers_and_risk_factors", "progress_observations", "co_occurring_symptoms",
}


def test_tables_live_in_their_own_schemas(conn, vector_available):
    rows = conn.execute(text("""
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_schema IN ('cases', 'corpus', 'auth', 'audit', 'chat', 'public')
          AND table_type = 'BASE TABLE' AND table_name <> 'alembic_version'
    """)).all()
    expected = ({("corpus", "embedding_models"), ("corpus", "chunk_embeddings"), ("corpus", "embedding_failures"),
                 ("audit", "events"), ("audit", "feedback"), ("auth", "email_report_consents")}
                if vector_available else set()) | {("cases", t) for t in CASE_TABLES} | {
        ("corpus", "documents"), ("corpus", "chunks"), ("corpus", "ingestion_runs"),
        ("chat", "sessions"), ("chat", "messages"),
        ("auth", "users"), ("auth", "login_sessions")}
    assert {(s, t) for s, t in rows} == expected


def test_separate_schemas_exist_for_other_data(conn):
    schemas = set(conn.execute(text("SELECT schema_name FROM information_schema.schemata")).scalars())
    assert {"cases", "corpus", "auth", "audit", "chat"} <= schemas


def test_head_revision_applied(conn, head_revision):
    assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == head_revision


def test_synthetic_cases_have_no_cross_schema_foreign_keys(conn):
    """The cases schema stays isolated so it can be re-seeded without touching user data."""
    rows = conn.execute(text("""
        SELECT c.conname FROM pg_constraint c
        JOIN pg_class src ON src.oid = c.conrelid
        JOIN pg_class dst ON dst.oid = c.confrelid
        WHERE c.contype = 'f' AND src.relnamespace <> dst.relnamespace
          AND 'cases'::regnamespace IN (src.relnamespace, dst.relnamespace)
    """)).all()
    assert rows == []


def test_chat_sessions_owned_by_users(conn):
    fk = conn.execute(text("""
        SELECT confrelid::regclass::text, confdeltype FROM pg_constraint
        WHERE contype = 'f' AND conrelid = 'chat.sessions'::regclass""")).all()
    assert fk == [("auth.users", "c")]  # ON DELETE CASCADE


def test_every_case_table_has_synthetic_flag_and_timestamps(conn):
    for table in CASE_TABLES:
        cols = set(conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'cases' AND table_name = :t"""), {"t": table}).scalars())
        assert {"is_synthetic", "created_at", "updated_at"} <= cols, table


def test_downgrade_and_upgrade_round_trip(engine, db_url, head_revision):
    """A clean rebuild works from a populated database, then re-seeds."""
    from alembic import command

    from db.seed import seed

    cfg = alembic_config(db_url)
    command.downgrade(cfg, "base")
    probe = create_engine(db_url)
    with probe.connect() as c:
        assert c.execute(text("SELECT to_regclass('cases.patients')")).scalar() is None
    probe.dispose()
    command.upgrade(cfg, head_revision)
    counts = seed("TEST_DATABASE_URL")
    assert counts["patients"] == 10


def test_seed_is_reproducible(engine):
    """Seeding twice yields byte-identical case content (ids included)."""
    from db.seed import seed

    snapshot_sql = text("""
        SELECT md5(string_agg(t::text, '|' ORDER BY t::text)) FROM (
          SELECT row(patient_id, age_years, presenting_concern, motivation_stage)::text AS t FROM cases.patients
          UNION ALL SELECT row(event_id, patient_id, sequence_no, event_date, event_type, description,
                               related_use_id, related_intervention_id)::text FROM cases.patient_history_events
          UNION ALL SELECT row(intervention_id, patient_id, name, start_date, end_date, status)::text
                    FROM cases.previous_interventions
          UNION ALL SELECT row(observation_id, patient_id, observed_on, label, value_text)::text
                    FROM cases.progress_observations
        ) s""")
    with engine.connect() as c:
        first = c.execute(snapshot_sql).scalar_one()
    seed("TEST_DATABASE_URL")
    with engine.connect() as c:
        second = c.execute(snapshot_sql).scalar_one()
    assert first == second
