// Data layer. The UI talks only to these async functions; every one is a real backend call.
//
// Nothing here ever substitutes invented data: when the API fails, the error is thrown and shown.
// Secrets never appear in this file: the browser holds only an HttpOnly session cookie (set by the backend)
// and an in-memory CSRF token.

import { apiGet, apiPost, ApiError, setCsrfToken } from './http.js';
import { API_BASE_URL } from '../config.js';
import { toPatientDetail, toPatientSummary } from './adapters.js';
import { CATEGORIES } from '../data/categories.js';

export { ApiError };

// Searching can include a first model load; answers include a model call plus retries.
const SLOW_MS = 180000;
const clone = (value) => structuredClone(value);

export async function listCategories() {
  return [...CATEGORIES];
}

export function modeLabel(mode) {
  return { gk: 'General Knowledge', rag: 'RAG', both: 'Both', auto: 'Auto' }[mode] ?? mode;
}

// ---------- health and authentication ----------

export const getHealth = () => apiGet('/api/health');

// GET /api/auth/session: who is signed in (always 200). Stores the CSRF token for POSTs.
export async function getAuthSession() {
  const s = await apiGet('/api/auth/session');
  setCsrfToken(s.csrf_token);
  return s;
}

// The browser navigates here; the backend redirects to Google and back to returnTo.
export function signInUrl(returnTo) {
  const url = new URL(`${API_BASE_URL}/api/auth/login`);
  url.searchParams.set('return_to', returnTo);
  return url.toString();
}

export async function signOut() {
  await apiPost('/api/auth/logout');
  setCsrfToken(null);
  clearCache();
}

export function clearCache() {
  detailCache.clear();
}

// ---------- patients (synthetic cases) ----------

export async function listPatients() {
  const items = [];
  const limit = 100;
  for (let offset = 0; ; offset += limit) {
    const page = await apiGet('/api/patients', { limit, offset });
    items.push(...page.items);
    if (page.items.length === 0 || items.length >= page.total) break;
  }
  return items.map(toPatientSummary);
}

const detailCache = new Map();

export async function getPatient(id, { refresh = false } = {}) {
  if (!refresh && detailCache.has(id)) return clone(detailCache.get(id));
  const [detail, history] = await Promise.all([
    apiGet(`/api/patients/${encodeURIComponent(id)}`),
    apiGet(`/api/patients/${encodeURIComponent(id)}/history`, { limit: 100 }),
  ]);
  const patient = toPatientDetail(detail, history.items);
  detailCache.set(id, patient);
  return clone(patient);
}

// ---------- research evidence ----------

export const getChunk = (chunkId) => apiGet(`/api/chunks/${encodeURIComponent(chunkId)}`);

// POST /api/retrieval/search: semantic search over the research corpus (no LLM)
export const searchEvidence = (query, { topK = 5, filters } = {}) =>
  apiPost('/api/retrieval/search', { query, top_k: topK, ...(filters ? { filters } : {}) }, { timeoutMs: SLOW_MS });

const pagesLabel = (a, b) => (a === b ? `p. ${a}` : `pp. ${a}–${b}`);
const clip = (t) => (t.length > 220 ? `${t.slice(0, 220)}…` : t);

// Search result -> the evidence shape the UI renders (citation ids E1, E2, ...)
function fromSearchResult(r, i) {
  return {
    citationId: `E${i + 1}`,
    chunkId: r.chunk_id,
    docId: r.document.document_id,
    docTitle: r.document.short_title,
    section: r.section_path.length ? r.section_path.join(' › ') : 'No section heading',
    pages: pagesLabel(r.page_start, r.page_end),
    pageLabel: r.page_label_start,
    location: r.location,
    similarity: r.similarity,
    distance: r.distance,
    excerpt: clip(r.matched_passage),
    text: r.text,
    matchedPassage: r.matched_passage,
    contentHash: r.content_hash,
  };
}

// Evidence item returned inside an answer (same fields, snake_case)
function fromEvidenceItem(e) {
  return {
    citationId: e.citation_id,
    chunkId: e.chunk_id,
    docId: e.document_id,
    docTitle: e.document_short_title,
    section: e.section,
    pages: pagesLabel(e.page_start, e.page_end),
    pageLabel: e.page_label,
    location: e.location,
    similarity: e.similarity,
    distance: e.distance,
    excerpt: clip(e.matched_passage),
    text: e.text,
    matchedPassage: e.matched_passage,
    contentHash: e.content_hash,
  };
}

function retrievalFromSettings(s, extra = {}) {
  return {
    topK: s.top_k, minSimilarity: s.min_similarity ?? null, model: s.embedding_model, modelVersion: s.embedding_model_version,
    dimension: s.embedding_dimension, metric: s.metric, searchType: s.search_type, filters: s.filters ?? {},
    tieBreak: s.tie_break, ...extra,
  };
}

// ---------- chat sessions and answers ----------

// POST /api/sessions: one chat session per patient conversation (the exchange is stored server-side)
export const createSession = (patientId, mode) =>
  apiPost('/api/sessions', { patient_id: patientId, mode });

// Answer pipeline (LangGraph workflow + DeepSeek, all on the backend).
// onRetrieved receives the retrieved evidence as soon as search finishes, BEFORE the answer is generated.
export async function askQuestion({ patientId, question, mode, sessionId, onRetrieved = () => {} }) {
  if (mode === 'rag' || mode === 'both') {
    // The same deterministic search the backend repeats for the answer; shown first so students see the sources.
    const search = await searchEvidence(question, { topK: 5 });
    onRetrieved({
      evidence: search.results.map(fromSearchResult),
      retrieval: retrievalFromSettings(search.settings, { note: search.note, performed: true, source: 'backend' }),
    });
  }
  const r = await apiPost('/api/answers', {
    patient_id: patientId, question, mode, ...(sessionId ? { session_id: sessionId } : {}),
  }, { timeoutMs: SLOW_MS });
  return toUiAnswer(r);
}

function toUiAnswer(r) {
  const evidence = r.evidence.map(fromEvidenceItem);
  const settings = r.retrieval.settings ?? {};
  return {
    correlationId: r.correlation_id,
    mode: r.mode,
    patientId: r.patient_id,
    questionClass: r.question_class,
    classReason: r.class_reason,
    outcome: r.outcome,
    claims: r.answer?.claims ?? [],
    limitations: r.answer?.limitations ?? [],
    citations: Object.fromEntries((r.answer?.citations ?? []).map((c) => [c.citation_id, c])),
    unsupportedCount: r.answer?.unsupported_claim_count ?? 0,
    hasConflicts: r.answer?.has_conflicting_evidence ?? false,
    issues: r.issues,
    evidence,
    patientFacts: Object.fromEntries(r.patient_facts.map((f) => [f.citation_id, f])),
    retrieval: {
      ...(settings.embedding_model ? retrievalFromSettings(settings) : { topK: settings.top_k }),
      performed: r.retrieval.performed, source: r.retrieval.source, noEvidence: evidence.length === 0,
      coverage: r.retrieval.coverage, coverageNote: r.retrieval.coverage_note, decisionReason: r.retrieval.decision_reason,
      deferredToModel: r.retrieval.deferred_to_model, toolEvents: r.retrieval.tool_events, note: r.retrieval.note,
      topSimilarity: r.retrieval.top_similarity,
    },
    safety: r.safety,
    llm: r.llm,
    trace: r.trace,
    latencyMs: r.latency_ms,
    sourceNote: r.source_note,
  };
}

export const getTrace = (correlationId) => apiGet(`/api/answers/${encodeURIComponent(correlationId)}/trace`);

export const submitFeedback = ({ correlationId, rating, reason, comment }) =>
  apiPost('/api/feedback', {
    correlation_id: correlationId, rating, ...(reason ? { reason } : {}), ...(comment ? { comment } : {}),
  });

// ---------- audit / activity ----------

export const getActivity = () => apiGet('/api/audit/activity');

// Best effort: an audit write failure must never get in the student's way.
export async function recordUiEvent(event) {
  try {
    await apiPost('/api/audit/ui-events', event);
  } catch {
    // intentionally ignored; the backend logs failed audit writes itself
  }
}

// ---------- optional email reports ----------

export const getEmailStatus = () => apiGet('/api/reports/email/status');
export const enableEmailReports = () => apiPost('/api/reports/email/enable');
export const disableEmailReports = () => apiPost('/api/reports/email/disable');
export const sendEmailReport = () => apiPost('/api/reports/email/send');
