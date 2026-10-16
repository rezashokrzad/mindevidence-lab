"""Google sign-in: users, login sessions, chat-session ownership.

Revision ID: 0003_auth_users
Revises: 0002_papers_and_chat
Create Date: 2026-10-14
"""
from db.migrations.sql_file import run_sql_file

revision = "0003_auth_users"
down_revision = "0002_papers_and_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0003_auth_users.up.sql")


def downgrade() -> None:
    run_sql_file("0003_auth_users.down.sql")
