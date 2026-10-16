# MindEvidence Lab: Project Spec

## Purpose
An educational, RAG-based research assistant for psychology students who study addiction treatment. Students explore 10 synthetic patient cases and ask questions that are answered from a small corpus of authorized research documents, with traceable citations.
This is a teaching tool, not a clinical one: it does not diagnose, prescribe, or choose individual treatments.

## Users
- Students: sign in with Google, explore cases, ask questions, inspect evidence.
- Instructor (admin): manages the document corpus and reviews activity logs.

## Core features
- Patient explorer and detail view for P-001 to P-010, all clearly labeled synthetic.
- Chat with the selected patient as context.
- Answer modes: General Knowledge, RAG, Both (and an optional Auto mode).
- Evidence inspector: retrieved chunks with document, section, page, and citation ID.
- Activity timeline and audit trail for every question.
- Optional email summaries through Gmail, only with explicit consent.

## Architecture
- Frontend: vanilla HTML, CSS, and JavaScript (ES modules), no frameworks.
- Backend: Python, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic.
- Database: PostgreSQL with pgvector.
- Authentication: Google OAuth 2.0 / OpenID Connect with a backend session cookie.
- Retrieval: PDF ingestion (PyMuPDF), chunks of about 400-700 tokens, sentence-transformers/all-MiniLM-L6-v2 embeddings, cosine search.
- LLM: the DeepSeek API, called only from the backend, behind a provider interface.
- Orchestration: a LangGraph workflow with typed state.
- Observability: structured audit events in PostgreSQL, linked by correlation IDs.

## Data
- Patients: 10 fictional adult cases (alcohol, cannabis, opioids, stimulants, behavioral addictions) with consistent timelines. No real people or records.
- Research corpus: WHO mhGAP Guideline (2023), NIDA Principles of Drug Addiction Treatment, WHO mhGAP Intervention Guide v2.0, and a Cochrane review on alcohol use disorder (2025).
- Patient facts, research evidence, and model interpretations are always kept and shown separately.

## Ground rules
- Secrets live only in environment variables, never in frontend code or the repository.
- Citations always point to real retrieved chunks; missing evidence is stated, never invented.
- Retrieved document text is untrusted data and never acts as instructions.
- Test results are reported honestly; the project claims no production readiness or clinical validation.

## Build plan
The project is built in ten batches, each described in its own prompt:
01 Frontend with mock data · 02 PostgreSQL data model and seed · 03 FastAPI backend · 04 Google sign-in · 05 Document ingestion · 06 pgvector retrieval · 07 DeepSeek answers with citations · 08 LangGraph orchestration · 09 Observability, audit, and Gmail reports · 10 Integration and classroom demo.

## Repository layout
frontend/ · backend/ · data/papers/ · docs/ (this file is docs/SPEC.md)
