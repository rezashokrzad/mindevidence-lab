"""Semantic retrieval: pgvector embeddings.

Revision ID: 0005_embeddings
Revises: 0004_document_ingestion
Create Date: 2026-10-14
"""
from db.migrations.sql_file import run_sql_file

revision = "0005_embeddings"
down_revision = "0004_document_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0005_embeddings.up.sql")


def downgrade() -> None:
    run_sql_file("0005_embeddings.down.sql")
