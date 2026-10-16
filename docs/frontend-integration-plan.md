# Frontend Integration Status

Since Batch 04, everything except `/api/health` and `/api/auth/*` requires Google sign-in. The frontend checks `GET /api/auth/session` on load and requests patient data only after a successful sign-in.

This document tracks which frontend data is **real** (served by the API from PostgreSQL) and which is still **mock**. Mock data is never used as a fallback for real data. If the API fails, the UI shows the error and a Retry button.

## Status after Batch 04

| Data | Source now | Real source | Batch |
| --- | --- | --- | --- |
| Patient list | **API** `GET /api/patients` (all pages) | done | 03 |
| Patient detail | **API** `GET /api/patients/{id}` + `GET /api/patients/{id}/history` | done | 03 |
| API health indicator | **API** `GET /api/health` | done | 03 |
| Sign-in state, sign-in, sign-out | **API** `/api/auth/*` (Google OIDC, HttpOnly session cookie) | done | 04 |
| Patient context for answers | **API** (the same cached record) | done | 03 |
| Retrieved evidence and citations (text, document, section, pages, similarity) | **API** `POST /api/retrieval/search`, `GET /api/chunks/{id}` | done (needs pgvector + embeddings) | 06 |
| Answer text | mock (`frontend/js/services/mockEngine.js`) | DeepSeek + LangGraph | 07–08 |
| Conversation persistence | in-memory | `POST/GET /api/sessions…` (API exists; UI wiring with answers) | 07 |
| Activity timeline | client-side events (now includes API calls and errors) | `audit` schema | 09 |
| Suggested questions | frontend UI content (`frontend/js/data/suggestedQuestions.js`) | stays frontend | — |

The "Mock answers" selector in the header only changes the mock answer pipeline (slow, no evidence, answer error). It cannot affect patient data.

## How it is wired

- `frontend/js/config.js` holds `API_BASE_URL` (default `http://localhost:8000`). Override it with `window.MINDEVIDENCE_API_BASE`.
- `frontend/js/services/http.js` contains `apiGet`/`apiPost`. They add a timeout and turn the error envelope into an `ApiError` carrying `code`, `status`, and `requestId`.
- `frontend/js/services/adapters.js` maps API responses to the shapes the UI renders. `null` values display as "Not recorded".
- `frontend/js/services/api.js` is still the only module the UI calls for data.

## Field mapping (API → UI)

| API (snake_case) | UI field | Note |
| --- | --- | --- |
| `patient_id` | `id` | |
| `gender` | `gender` | Labels: Female, Male, Non-binary, Gender not recorded |
| `primary_category` | `category` | Title-cased for the filter and the `cat-*` classes |
| `primary_concern_label`, `brief_history`, `treatment_summary` | `primaryConcern`, `briefHistory`, `treatmentSummary` | |
| `occupation`, `living_situation`, `social_context` | Background section (three labeled lines) | `occupation: null` → "Not recorded" |
| `uses[]` | Substance / behavior use section | Explicit unknowns shown |
| `observed_behaviors[]` | `observedBehaviors[] {text, date, source}` | |
| history `items[]` | `history[] {date, event}` | `date.value` or "Date not recorded" |
| `triggers[]`, `risk_factors[]`, `protective_factors[]` | Three chip groups | |
| `previous_interventions[]` | `{name, period, status, outcome}` | `period` = start – end / "present" / "end not recorded" |
| `co_occurring_symptoms[]` | Symptoms section with status tags | Labeled "case descriptions, not diagnoses" |
| `motivation`, `stage_history[]`, `progress_summary`, `progress_measures[]` | Progress section | Measures show "(baseline X)" |

## Next steps

- **Batch 07:** replace `askQuestion` with the answer endpoint. Create a session per conversation (`POST /api/sessions`), then load history from `GET /api/sessions/{id}/messages`. Delete `mockEngine.js`.
