# MindEvidence Lab: Frontend

This is the frontend for MindEvidence Lab, an educational RAG research assistant for psychology students who study addiction treatment. It is built with vanilla HTML, CSS, and JavaScript ES modules, with no frameworks and no build step.

> **Status (Batches 07-09):** you sign in with Google. Patient cases, retrieved passages and **answers all come from the backend** (pgvector retrieval, LangGraph workflow, DeepSeek). Modes: General Knowledge, RAG, Both, Auto. Answers show claims grouped by basis (research evidence, patient record, general knowledge, could not be supported) with clickable citations, plus feedback buttons and a per-answer trace. The **Activity** panel shows your own audit events, per-answer retrieval and node timings, and model settings. **Email reports** are optional. If any backend call fails, the error is shown and nothing is substituted. All patients are synthetic. This is a teaching tool with no clinical validation.
>
> If the API is unreachable, the patient explorer shows the error and a Retry button. Mock data is **never** shown in its place. The old mock-answer engine was removed.

## Run locally

1. Start the API on port 8000 (see `backend/README.md`, steps 1–4). The header shows **API connected** when it is reachable.
2. ES modules don't load from `file://`, so serve the frontend over HTTP. From the repository root:

```bash
python -m http.server 5173 --directory frontend
```

3. Open **http://localhost:5173** (use `localhost`, not `127.0.0.1`, so the session cookie set by `localhost:8000` is sent) and click **Sign in with Google**. Google must be configured first; see `docs/google-oauth-setup.md`.

To point at a different API, set `window.MINDEVIDENCE_API_BASE` before `js/main.js` loads. The default is in `js/config.js`.

## Structure

```
frontend/
├── index.html                 Page skeleton and static controls
├── css/styles.css             Design tokens (light + dark), layout, components
└── js/
    ├── main.js                Store, actions, sign-in state, wiring, boot, API health indicator
    ├── config.js              API base URL and timeout
    ├── lib/dom.js             el() helper (text nodes only), store, ids, highlighting
    ├── data/
    │   ├── mockData.js        MOCK: general-knowledge notes and study angles for the placeholder answer text
    │   └── suggestedQuestions.js  Starter questions per case (UI content)
    ├── services/
    │   ├── api.js             The only module the UI calls for data (real patients, mock answers)
    │   ├── http.js            fetch wrapper: cookie credentials, CSRF header on POST, 401 hook, error envelope → ApiError
    │   ├── adapters.js        API responses → UI shapes ("Not recorded" for nulls)
    │   └── mockEngine.js      MOCK keyword "retrieval" and answer composition
    └── ui/
        ├── auth.js            Header sign-in/user chip/sign-out and sign-in result banner
        ├── patientExplorer.js Search, category filter, list, arrow-key navigation
        ├── patientDetail.js   Synthetic case record
        ├── chat.js            Conversation, mode switch, composer, citations
        ├── evidenceInspector.js Retrieved chunks and evidence states
        ├── timeline.js        Activity/audit events
        └── citationDialog.js  Document excerpt dialog
```

### Data is separate from presentation

UI modules never import data modules. They call these async functions in `services/api.js`:

| Function | Source |
| --- | --- |
| `getHealth()` | **API** `GET /api/health` |
| `getAuthSession()`, `signInUrl()`, `signOut()` | **API** `/api/auth/session`, `/api/auth/login`, `POST /api/auth/logout` |
| `listPatients()` | **API** `GET /api/patients` (all pages) |
| `getPatient(id)` | **API** `GET /api/patients/{id}` + `/history` |
| `searchEvidence(query)` | **API** `POST /api/retrieval/search` |
| `getChunk(chunkId)` | **API** `GET /api/chunks/{id}` |
| `askQuestion({ patientId, question, mode, correlationId, onEvent, onRetrieved })` | retrieval is **real** (pgvector); the answer text is mock until Batch 07 |

See `docs/frontend-integration-plan.md` for the field mapping and the remaining steps.

All dynamic text goes into the DOM as text nodes (`el()` in `lib/dom.js`), never through `innerHTML`. Retrieved document text will be untrusted in later batches.

## Mock behaviour

- **Retrieval** scores keyword overlap between the question and each chunk, shapes the result like a cosine similarity, and uses top-k = 3 and a threshold of 0.55. A chunk gets a small boost when it matches the patient's category. This is a placeholder for pgvector search (Batch 06).
- **Answers** always have three labeled parts. *Patient facts* come from the synthetic record. *Retrieved research evidence* lists cited chunks. *Model interpretation* is generated, may be wrong, and is not clinical advice.
- **Citations** (`E1`, `E2`, …) only ever point to chunks retrieved for that answer. If nothing passes the threshold, the answer says no evidence was found and cites nothing.
- **Behavioral cases** (P-008 gambling and P-009 gaming) are only partly covered by the corpus. They show the no-evidence state, or a warning that the retrieved evidence isn't specific to the case.

### "Mock answers" selector (header)

This selector affects **only** the mock answer pipeline. It never affects patient data.

| Option | Effect |
| --- | --- |
| Normal | Default timings |
| Slow | Mock answer latencies ×4, so loading states are easy to see |
| No evidence found | Retrieval returns nothing for RAG and Both questions |
| Answer error | Generation fails with a simulated provider timeout |

To see the real patient-list error state, stop the API and reload. The explorer shows the network error and Retry, the header shows **API unavailable**, and the timeline logs the failure.

## Implemented interactions checklist

- [x] Dashboard layout: three columns on desktop, two on tablet, stacked on phone, no horizontal scroll at 375px
- [x] Google sign-in button (non-functional; shows a "Batch 04" notice and logs an event)
- [x] Patient explorer with all 10 synthetic patients **from PostgreSQL via the API**: ID, category, brief history, treatment summary
- [x] Search and category filter, with an empty state when nothing matches
- [x] Selecting a patient loads its record from the API: background, substance/behavior use, observed behaviors, history, triggers, risk and protective factors, co-occurring symptoms, previous interventions, progress with stage history
- [x] Google sign-in: signed-out state with a sign-in button, redirect via the backend, user chip (name, email, role) and **Sign out** when signed in, banners for sign-in errors and sign-out, and automatic return to signed-out state when a request answers 401 (session expired or revoked). Tokens and secrets never reach JavaScript; the session is an HttpOnly cookie, and the CSRF token is held in memory only
- [x] API health indicator in the header; real network/API errors with Retry and request IDs; no mock fallback for patients
- [x] Detail panel can be collapsed to give the conversation more room
- [x] Chat uses the selected patient as context, with a separate conversation per patient
- [x] Submitting a question (Send button, Enter key, or a suggested question) adds it to the conversation, followed by a progressive assistant message
- [x] Source-mode switch (General Knowledge / RAG / Both) plus an always-visible indicator in the header
- [x] Switching modes changes the evidence inspector: "Retrieval is off" in GK, and the last retrieval (or a re-run prompt) in RAG and Both
- [x] Evidence inspector shows each chunk's citation ID, document, section, page, chunk ID, score, and excerpt
- [x] Selecting a citation (in an answer or the inspector) highlights it and opens the document excerpt dialog
- [x] Activity timeline shows request, retrieval, generation, interaction, and error events with correlation IDs, plus a filter and Clear button
- [x] Loading states: patient list skeleton, detail skeleton, answer phases, inspector skeleton, dialog loading
- [x] Empty states: no patient, no messages, no matching patients, no evidence yet, no events
- [x] Error states: patient list and case record errors (real API errors) with Retry, answer error with "Retry question", inspector error
- [x] No-evidence state in both the answer and the inspector
- [x] Patient facts, retrieved evidence, and model interpretation each have their own color, label, and legend
- [x] All patient data is labeled synthetic (header notice, explorer badge, detail banner, chat context chip)
- [x] Keyboard: skip link, visible focus rings, arrow/Home/End navigation in the patient list, Enter to send and Shift+Enter for a newline, Esc closes the dialog, native radio group for modes
- [x] Respects `prefers-color-scheme` (dark) and `prefers-reduced-motion`

## Demo script (about 3 minutes)

1. Select **P-004** and click the suggestion about opioid agonist maintenance. Watch the timeline and the evidence cards fill in.
2. Click **E1** in the answer to open the excerpt dialog, then press Esc.
3. Switch to **General Knowledge**. The inspector shows "Retrieval is off" and the header indicator changes.
4. Ask a question in GK mode, switch back to **RAG**, and use **Re-run it in RAG mode** in the inspector.
5. Select **P-008** and ask "What does the corpus say about treatment for gambling problems?" to see the no-evidence state.
6. Set **Mock answers** to **Answer error**, ask anything, and use **Retry question** after switching back to Normal.
7. Stop the API and reload. The patient explorer shows the real connection error with Retry, and no mock patients appear. Restart the API and click **Retry**.

## Known limitations

- Mock retrieval is keyword matching, not semantic search, so wording matters.
- Conversations live only in memory and reset on reload. The sessions API exists, but the UI uses it once real answers arrive (Batch 07).
- The excerpt text and page numbers are placeholders until ingestion (Batch 05).
- Accessibility was checked by hand (keyboard, labels, live regions). No automated audit has been run.
