"""Schemas and synthetic patient-case tables.

Revision ID: 0001_cases_schema
Revises:
Create Date: 2026-10-14
"""
from db.migrations.sql_file import run_sql_file

revision = "0001_cases_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0001_cases_schema.up.sql")


def downgrade() -> None:
    run_sql_file("0001_cases_schema.down.sql")
