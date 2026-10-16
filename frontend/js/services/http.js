// Minimal fetch wrapper for the MindEvidence Lab API.
// Every failure becomes an ApiError carrying the server's error envelope fields,
// so the UI can show what actually went wrong. Nothing is ever substituted.
//
// Authentication: the session lives in an HttpOnly cookie that JavaScript cannot read;
// `credentials: 'include'` sends it. POST requests carry the CSRF token obtained from
// GET /api/auth/session (kept in memory only, never in storage).

import { API_BASE_URL, API_TIMEOUT_MS } from '../config.js';

export class ApiError extends Error {
  constructor(message, { code = 'error', status = 0, requestId = null, details = [] } = {}) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.details = details;
  }
}

let csrfToken = null;
let unauthorizedHandler = () => {};

export function setCsrfToken(token) {
  csrfToken = token || null;
}

// Called whenever a request answers 401 (session missing, expired, or revoked).
export function onUnauthorized(fn) {
  unauthorizedHandler = fn;
}

export async function apiGet(path, params = {}) {
  return request('GET', path, { params });
}

export async function apiPost(path, body, { timeoutMs } = {}) {
  return request('POST', path, { body, timeoutMs });
}

async function request(method, path, { params = {}, body, timeoutMs = API_TIMEOUT_MS } = {}) {
  const url = new URL(API_BASE_URL + path);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v));
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response;
  try {
    response = await fetch(url, {
      method,
      headers: {
        Accept: 'application/json',
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...(method !== 'GET' && csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      credentials: 'include',
    });
  } catch (err) {
    const timedOut = err.name === 'AbortError';
    throw new ApiError(
      timedOut ? `The API at ${API_BASE_URL} did not respond within ${timeoutMs / 1000} s.`
        : `Cannot reach the API at ${API_BASE_URL}. Is the backend running?`,
      { code: timedOut ? 'timeout' : 'network_error' },
    );
  } finally {
    clearTimeout(timer);
  }

  const requestId = response.headers.get('X-Request-ID');
  if (response.status === 204) return null;
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // non-JSON body; handled below
  }

  if (!response.ok) {
    const e = payload?.error;
    if (response.status === 401) unauthorizedHandler();
    throw new ApiError(e?.message ?? `Request failed with HTTP ${response.status}.`, {
      code: e?.code ?? 'http_error', status: response.status, requestId: e?.request_id ?? requestId,
      details: e?.details ?? [],
    });
  }
  if (payload === null) {
    throw new ApiError('The API returned an unreadable response.', { code: 'bad_response', status: response.status, requestId });
  }
  return payload;
}
