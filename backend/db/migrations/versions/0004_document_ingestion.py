"""Research-document ingestion: documents, chunks, ingestion runs.

Revision ID: 0004_document_ingestion
Revises: 0003_auth_users
Create Date: 2026-10-14
"""
from db.migrations.sql_file import run_sql_file

revision = "0004_document_ingestion"
down_revision = "0003_auth_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0004_document_ingestion.up.sql")


def downgrade() -> None:
    run_sql_file("0004_document_ingestion.down.sql")
