"""Allow mode 'auto' in chat sessions and messages.

Revision ID: 0007_auto_mode
Revises: 0006_audit_and_reports
Create Date: 2026-10-14
"""
from db.migrations.sql_file import run_sql_file

revision = "0007_auto_mode"
down_revision = "0006_audit_and_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0007_auto_mode.up.sql")


def downgrade() -> None:
    run_sql_file("0007_auto_mode.down.sql")
