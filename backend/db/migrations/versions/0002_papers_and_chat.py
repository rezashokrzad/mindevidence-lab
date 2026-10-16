"""Research-paper catalogue and chat sessions/messages.

Revision ID: 0002_papers_and_chat
Revises: 0001_cases_schema
Create Date: 2026-10-14
"""
from db.migrations.sql_file import run_sql_file

revision = "0002_papers_and_chat"
down_revision = "0001_cases_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0002_papers_and_chat.up.sql")


def downgrade() -> None:
    run_sql_file("0002_papers_and_chat.down.sql")
