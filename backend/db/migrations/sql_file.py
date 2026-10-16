"""Run a plain .sql migration file through the migration's connection.

Statements are sent in one call so dollar-quoted function bodies stay intact.
psycopg 3 accepts multiple statements when no parameters are passed.
"""
from pathlib import Path

from alembic import op

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def run_sql_file(name: str) -> None:
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    if op.get_context().as_sql:  # offline mode: emit the script
        op.execute(sql)
        return
    raw = op.get_bind().connection.driver_connection
    with raw.cursor() as cur:
        cur.execute(sql)
