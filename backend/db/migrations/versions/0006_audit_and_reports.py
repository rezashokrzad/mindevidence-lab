"""Audit trail, feedback, and email-report consent.

Revision ID: 0006_audit_and_reports
Revises: 0005_embeddings
Create Date: 2026-10-14
"""
from db.migrations.sql_file import run_sql_file

revision = "0006_audit_and_reports"
down_revision = "0005_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0006_audit_and_reports.up.sql")


def downgrade() -> None:
    run_sql_file("0006_audit_and_reports.down.sql")
