import * as api from './services/api.js';
import { createStore, el } from './lib/dom.js';
import { initPatientExplorer } from './ui/patientExplorer.js';
import { initPatientDetail } from './ui/patientDetail.js';
import { initChat } from './ui/chat.js';
import { initEvidenceInspector } from './ui/evidenceInspector.js';
import { initActivity } from './ui/activity.js';
import { initDialogs } from './ui/dialogs.js';
import { initCitationDialog } from './ui/citationDialog.js';
import { initAuthUi, authErrorMessage } from './ui/auth.js';
import { onUnauthorized } from './services/http.js';

const store = createStore({
  patients: [],
  patientsStatus: 'idle', // idle | signed_out | loading | ready | error
  patientsError: '',
  search: '',
  category: 'all',
  selectedId: null,
  detail: { status: 'idle' },
  mode: 'rag', // gk | rag | both | auto
  conversations: {}, // patientId -> messages[]
  sessions: {}, // patientId -> backend chat session id
  lastAnswer: {}, // patientId -> { status, mode, question, answer?, error?, partial? }
  pending: null, // { patientId } while a question is in flight
  activeCitation: null, // { cid, chunkId }
  feedback: {}, // correlationId -> { status: idle|choosing|sending|sent, rating, error }
  activity: { status: 'idle', data: null, error: '' },
  activityTab: 'timeline',
  activityFilter: 'all',
  email: { status: 'idle', data: null, error: '', message: '', messageKind: '' },
  auth: { status: 'checking', user: null, signInAvailable: true }, // checking | signed_in | signed_out | unavailable
  authNotice: null,
});

const citationDialog = initCitationDialog();
let dialogsReady = null; // created after `actions` exists

// ---------- helpers ----------

function updateConversation(patientId, fn) {
  store.set((s) => ({ conversations: { ...s.conversations, [patientId]: fn(s.conversations[patientId] ?? []) } }));
}
function updateMessage(patientId, messageId, patch) {
  updateConversation(patientId, (msgs) => msgs.map((m) => (m.id === messageId ? { ...m, ...patch } : m)));
}
function setLastAnswer(patientId, value) {
  store.set((s) => ({ lastAnswer: { ...s.lastAnswer, [patientId]: value } }));
}
function findAnswer(cid) {
  for (const msgs of Object.values(store.get().conversations)) {
    const m = msgs.find((x) => x.answer?.correlationId === cid);
    if (m) return m.answer;
  }
  return null;
}
function describeError(err) {
  return err.requestId ? `${err.message} (request ${err.requestId})` : err.message;
}
function setFeedback(cid, value) {
  store.set((s) => ({ feedback: { ...s.feedback, [cid]: value } }));
}

function resetToSignedOut() {
  api.clearCache();
  store.set({
    auth: { ...store.get().auth, status: 'signed_out', user: null },
    patients: [], patientsStatus: 'signed_out', patientsError: '',
    selectedId: null, detail: { status: 'idle' },
    conversations: {}, sessions: {}, lastAnswer: {}, activeCitation: null, feedback: {},
    activity: { status: 'idle', data: null, error: '' },
    email: { status: 'idle', data: null, error: '', message: '', messageKind: '' },
  });
}

// Backend redirects back with ?signin=success, ?auth_error=<code>, ?signout=success, ?email=enabled, ?email_error=<code>.
const EMAIL_ERRORS = {
  access_denied: 'Gmail permission was not granted, so email reports stay off.',
  not_configured: 'Email reports are not configured on this server.',
  account_mismatch: 'The Google account you chose is not the one you signed in with.',
  invalid_state: 'The email-report authorization expired. Please try again.',
};
function consumeRedirectParams() {
  const url = new URL(window.location.href);
  const error = url.searchParams.get('auth_error');
  const emailError = url.searchParams.get('email_error');
  let openEmail = false;
  if (error) {
    store.set({ authNotice: { kind: 'error', text: authErrorMessage(error) } });
  } else if (url.searchParams.get('signout') === 'success') {
    store.set({ authNotice: { kind: 'info', text: 'You have signed out.' } });
  }
  if (url.searchParams.get('email') === 'enabled') {
    store.set({ email: { ...store.get().email, message: 'Email reports are now on.', messageKind: 'ok' } });
    openEmail = true;
  } else if (emailError) {
    store.set({ email: { ...store.get().email, message: EMAIL_ERRORS[emailError] ?? 'Email reports could not be enabled.', messageKind: 'error' } });
    openEmail = true;
  }
  const keys = ['auth_error', 'signin', 'signout', 'email', 'email_error'];
  if (keys.some((k) => url.searchParams.has(k))) {
    keys.forEach((k) => url.searchParams.delete(k));
    window.history.replaceState(null, '', url.pathname + url.search + url.hash);
  }
  return openEmail;
}

const apiStatus = document.getElementById('api-status');
const apiStatusText = document.getElementById('api-status-text');
async function checkApiHealth() {
  apiStatus.dataset.state = 'checking';
  apiStatusText.textContent = 'Checking API…';
  try {
    const h = await api.getHealth();
    apiStatus.dataset.state = 'ok';
    apiStatusText.textContent = 'API connected';
    apiStatus.title = `Database ${h.database} · schema ${h.schema_version} · sign-in: ${h.sign_in}`;
  } catch (err) {
    apiStatus.dataset.state = 'down';
    apiStatusText.textContent = err.status === 503 ? 'API: database down' : 'API unavailable';
    apiStatus.title = err.message;
  }
}

// ---------- actions ----------

const actions = {
  async refreshAuth() {
    store.set({ auth: { ...store.get().auth, status: 'checking' } });
    checkApiHealth();
    try {
      const s = await api.getAuthSession();
      if (s.authenticated) {
        store.set({ auth: { status: 'signed_in', user: s.user, signInAvailable: s.sign_in_available } });
        actions.loadPatients();
        actions.refreshActivity();
      } else {
        store.set({ auth: { status: 'signed_out', user: null, signInAvailable: s.sign_in_available } });
        resetToSignedOut();
      }
    } catch (err) {
      store.set({ auth: { status: 'unavailable', user: null, signInAvailable: false }, patientsStatus: 'error', patientsError: describeError(err) });
    }
  },

  signIn() {
    window.location.assign(api.signInUrl(window.location.origin + window.location.pathname));
  },

  async signOut() {
    try {
      await api.signOut();
    } catch (err) {
      actions.toast(`Sign-out failed: ${err.message}`);
      return;
    }
    window.location.replace(window.location.pathname + '?signout=success');
  },

  async loadPatients() {
    store.set({ patientsStatus: 'loading', patientsError: '' });
    checkApiHealth();
    try {
      const patients = await api.listPatients();
      store.set({ patients, patientsStatus: 'ready' });
    } catch (err) {
      store.set({ patientsStatus: 'error', patientsError: describeError(err) });
    }
  },

  async selectPatient(id, { force = false } = {}) {
    const s = store.get();
    if (s.selectedId === id && s.detail.status === 'ready' && !force) return;
    store.set({ selectedId: id, detail: { status: 'loading', id }, activeCitation: null });
    api.recordUiEvent({ event_type: 'patient.selected', patient_id: id });
    try {
      const data = await api.getPatient(id, { refresh: force });
      if (store.get().selectedId === id) store.set({ detail: { status: 'ready', id, data } });
    } catch (err) {
      if (store.get().selectedId === id) store.set({ detail: { status: 'error', id, error: describeError(err) } });
    }
  },

  setMode(mode) {
    if (store.get().mode === mode) return;
    store.set({ mode });
    api.recordUiEvent({ event_type: 'mode.changed', mode });
  },

  // Returns true when the question was accepted.
  ask(question, modeOverride) {
    const s = store.get();
    const patientId = s.selectedId;
    if (!patientId) { actions.toast('Select a patient first.'); return false; }
    if (s.pending) { actions.toast('Please wait for the current answer to finish.'); return false; }
    const mode = modeOverride ?? s.mode;
    const assistantId = `msg-${Date.now().toString(36)}-a`;

    updateConversation(patientId, (msgs) => [
      ...msgs,
      { id: `msg-${Date.now().toString(36)}-u`, role: 'user', text: question, mode, ts: Date.now() },
      { id: assistantId, role: 'assistant', status: 'pending', phase: 'Working…', mode, question },
    ]);
    store.set({ pending: { patientId }, activeCitation: null });
    setLastAnswer(patientId, { status: 'pending', mode, question });

    (async () => {
      try {
        let sessionId = store.get().sessions[patientId];
        if (!sessionId) {
          try {
            sessionId = (await api.createSession(patientId, mode)).session_id ?? undefined;
            if (sessionId) store.set((st) => ({ sessions: { ...st.sessions, [patientId]: sessionId } }));
          } catch { sessionId = undefined; } // answers still work without a stored session
        }
        updateMessage(patientId, assistantId, { phase: mode === 'gk' ? 'Generating answer…' : 'Searching the corpus…' });
        const answer = await api.askQuestion({
          patientId, question, mode, sessionId,
          onRetrieved: (partial) => {
            updateMessage(patientId, assistantId, { phase: 'Generating answer…' });
            setLastAnswer(patientId, { status: 'pending', mode, question, partial });
          },
        });
        updateMessage(patientId, assistantId, { status: 'done', answer, cid: answer.correlationId });
        setLastAnswer(patientId, { status: 'done', mode, question, answer });
      } catch (err) {
        const message = describeError(err);
        updateMessage(patientId, assistantId, { status: 'error', error: message });
        setLastAnswer(patientId, { status: 'error', mode, question, error: message });
      } finally {
        store.set({ pending: null });
        actions.refreshActivity();
      }
    })();
    return true;
  },

  async openCitation(cid, chunkId) {
    const evidence = findAnswer(cid)?.evidence.find((e) => e.chunkId === chunkId);
    if (!evidence) return;
    store.set({ activeCitation: { cid, chunkId } });
    await actions.openChunk(chunkId, evidence.citationId, evidence, cid);
  },

  async openChunk(chunkId, citationId, evidence, cid = null) {
    if (cid) api.recordUiEvent({ event_type: 'citation.opened', correlation_id: cid, chunk_id: chunkId });
    citationDialog.showLoading(citationId);
    try {
      citationDialog.showChunk({ chunk: await api.getChunk(chunkId), evidence });
    } catch (err) {
      citationDialog.showError(describeError(err));
    }
  },

  openFact(id, fact) {
    dialogsReady.showFact(id, fact);
  },

  async openTrace(cid) {
    dialogsReady.traceLoading(cid);
    try {
      dialogsReady.showTrace(await api.getTrace(cid));
    } catch (err) {
      dialogsReady.traceError(describeError(err));
    }
  },

  // ---- feedback
  chooseNotHelpful(cid) {
    setFeedback(cid, { status: 'choosing', rating: 'not_helpful' });
  },
  async sendFeedback(cid, rating, reason) {
    setFeedback(cid, { status: 'sending', rating });
    try {
      await api.submitFeedback({ correlationId: cid, rating, reason });
      setFeedback(cid, { status: 'sent', rating });
      actions.refreshActivity();
    } catch (err) {
      setFeedback(cid, { status: 'idle', rating, error: describeError(err) });
    }
  },

  // ---- activity
  async refreshActivity() {
    if (store.get().auth.status !== 'signed_in') return;
    store.set((s) => ({ activity: { ...s.activity, status: 'loading', error: '' } }));
    try {
      store.set({ activity: { status: 'ready', data: await api.getActivity(), error: '' } });
    } catch (err) {
      store.set((s) => ({ activity: { status: 'error', data: s.activity.data, error: describeError(err) } }));
    }
  },

  // ---- optional email reports
  async loadEmailStatus() {
    store.set((s) => ({ email: { ...s.email, status: 'loading', error: '' } }));
    try {
      const data = await api.getEmailStatus();
      store.set((s) => ({ email: { ...s.email, status: 'ready', data } }));
    } catch (err) {
      store.set((s) => ({ email: { ...s.email, status: 'error', error: describeError(err) } }));
    }
  },
  async openEmailReports() {
    dialogsReady.openEmail();
    await actions.loadEmailStatus();
  },
  async enableEmailReports() {
    store.set((s) => ({ email: { ...s.email, status: 'working', message: '' } }));
    try {
      const r = await api.enableEmailReports();
      window.location.assign(r.authorization_url);
    } catch (err) {
      store.set((s) => ({ email: { ...s.email, status: 'ready', message: describeError(err), messageKind: 'error' } }));
    }
  },
  async disableEmailReports() {
    store.set((s) => ({ email: { ...s.email, status: 'working', message: '' } }));
    try {
      await api.disableEmailReports();
      const data = await api.getEmailStatus();
      store.set((s) => ({ email: { ...s.email, status: 'ready', data, message: 'Email reports are off and the permission was revoked.', messageKind: 'ok' } }));
    } catch (err) {
      store.set((s) => ({ email: { ...s.email, status: 'ready', message: describeError(err), messageKind: 'error' } }));
    }
  },
  async sendEmailReport() {
    store.set((s) => ({ email: { ...s.email, status: 'working', message: '' } }));
    try {
      const r = await api.sendEmailReport();
      const data = await api.getEmailStatus();
      store.set((s) => ({ email: { ...s.email, status: 'ready', data, message: r.message ?? 'Summary sent to your Gmail address.', messageKind: 'ok' } }));
      actions.refreshActivity();
    } catch (err) {
      store.set((s) => ({ email: { ...s.email, status: 'ready', message: describeError(err), messageKind: 'error' } }));
    }
  },

  toast(message) {
    const t = el('div', { class: 'toast' }, message);
    document.getElementById('toast-region').append(t);
    setTimeout(() => t.remove(), 4000);
  },
};

// dialogs need the finished actions object
dialogsReady = initDialogs({ actions });

store.watch((s) => [s.email], (s) => dialogsReady.renderEmail(s.email));
document.getElementById('email-open').addEventListener('click', () => actions.openEmailReports());
store.watch((s) => [s.auth.status], (s) => { document.getElementById('email-open').hidden = s.auth.status !== 'signed_in'; });

// ---------- header controls ----------

const modeValue = document.getElementById('mode-indicator-value');
const modeIndicator = document.getElementById('mode-indicator');
store.watch((s) => [s.mode], (s) => {
  modeValue.textContent = api.modeLabel(s.mode);
  modeIndicator.dataset.mode = s.mode;
});

// ---------- boot ----------

const categories = await api.listCategories();
initPatientExplorer({ store, actions, categories });
initPatientDetail({ store, actions });
initChat({ store, actions });
initEvidenceInspector({ store, actions });
initActivity({ store, actions });
initAuthUi({ store, actions });

onUnauthorized(() => {
  if (store.get().auth.status !== 'signed_in') return;
  resetToSignedOut();
  store.set({ authNotice: { kind: 'error', text: 'Your session has ended. Please sign in again.' } });
});

const openEmailAfterRedirect = consumeRedirectParams();
await actions.refreshAuth();
if (openEmailAfterRedirect && store.get().auth.status === 'signed_in') actions.openEmailReports();
