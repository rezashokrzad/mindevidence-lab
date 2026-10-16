import { el, formatTime } from '../lib/dom.js';
import { modeLabel } from '../services/api.js';

const MODE_HELP = {
  gk: 'General Knowledge: the model answers from general knowledge only. Nothing is retrieved and nothing is cited.',
  rag: 'RAG: answers use only passages retrieved from the research documents, with citations.',
  both: 'Both: answers use retrieved passages (cited), plus clearly separated general knowledge (uncited).',
  auto: 'Auto: the model may decide to search the research documents (at most twice). Evidence appears with the answer.',
};

const CLASS_LABEL = {
  patient_history_lookup: 'case record question',
  research_question: 'research question',
  comparison: 'case + research',
};

const FEEDBACK_REASONS = [
  ['inaccurate', 'Inaccurate'], ['missing_evidence', 'Missing evidence'], ['unclear', 'Unclear'],
  ['unsafe', 'Unsafe or inappropriate'], ['other', 'Other'],
];

export function initChat({ store, actions }) {
  const messages = document.getElementById('messages');
  const form = document.getElementById('chat-form');
  const input = document.getElementById('chat-input');
  const sendBtn = document.getElementById('send-btn');
  const context = document.getElementById('chat-context');
  const suggestions = document.getElementById('suggestions');
  const modeHelp = document.getElementById('mode-help');
  const radios = [...form.querySelectorAll('input[name="mode"]')];

  radios.forEach((r) => r.addEventListener('change', () => r.checked && actions.setMode(r.value)));

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) {
      input.focus();
      actions.toast('Type a question first.');
      return;
    }
    if (actions.ask(text)) input.value = '';
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  store.watch((s) => [s.mode], (s) => {
    radios.forEach((r) => { r.checked = r.value === s.mode; });
    modeHelp.textContent = MODE_HELP[s.mode];
  });

  store.watch((s) => [s.selectedId, s.pending, s.detail], (s) => {
    const busy = Boolean(s.pending);
    const ready = Boolean(s.selectedId);
    input.disabled = !ready;
    sendBtn.disabled = !ready || busy;
    sendBtn.textContent = busy ? 'Working…' : 'Send';
    input.placeholder = ready ? `Ask a question about ${s.selectedId}…` : 'Select a patient first';

    if (s.detail.status === 'ready') {
      const p = s.detail.data;
      context.textContent = `Context: ${p.id} · ${p.category} · synthetic`;
      context.classList.add('active');
    } else {
      context.textContent = s.selectedId ? `Context: ${s.selectedId}` : 'No patient selected';
      context.classList.toggle('active', Boolean(s.selectedId));
    }

    suggestions.replaceChildren();
    if (s.detail.status === 'ready') {
      suggestions.append(el('span', { class: 'suggestions-label' }, 'Try:'));
      for (const q of s.detail.data.suggestedQuestions) {
        suggestions.append(el('button', {
          type: 'button', class: 'suggestion', disabled: busy, onclick: () => actions.ask(q),
        }, q));
      }
    }
  });

  let lastRenderedPatient = null;
  store.watch(
    (s) => [s.selectedId, s.conversations[s.selectedId], s.activeCitation, s.feedback],
    (s) => {
      const nearBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
      const switched = lastRenderedPatient !== s.selectedId;
      lastRenderedPatient = s.selectedId;
      renderMessages(s);
      if (switched || nearBottom) messages.scrollTop = messages.scrollHeight;
    },
  );

  function renderMessages(s) {
    messages.replaceChildren();
    if (!s.selectedId) {
      messages.append(el('div', { class: 'state state-empty' },
        el('p', { class: 'state-title' }, 'No conversation yet'),
        el('p', {}, 'Select a synthetic patient. Questions are always asked with that case as context.'),
      ));
      return;
    }
    const convo = s.conversations[s.selectedId] ?? [];
    if (!convo.length) {
      messages.append(el('div', { class: 'state state-empty' },
        el('p', { class: 'state-title' }, `No questions about ${s.selectedId} yet`),
        el('p', {}, 'Type a question below or pick a suggested one.'),
      ));
      return;
    }
    for (const m of convo) messages.append(m.role === 'user' ? userMessage(m) : assistantMessage(m, s));
  }

  function userMessage(m) {
    return el('article', { class: 'msg msg-user', 'aria-label': 'Your question' },
      el('header', { class: 'msg-meta' },
        el('span', { class: 'msg-author' }, 'You'),
        el('span', { class: `mode-badge mode-${m.mode}` }, modeLabel(m.mode)),
        el('time', { datetime: new Date(m.ts).toISOString() }, formatTime(m.ts)),
      ),
      el('p', { class: 'msg-text' }, m.text),
    );
  }

  function assistantMessage(m, s) {
    const meta = el('header', { class: 'msg-meta' },
      el('span', { class: 'msg-author' }, 'Assistant'),
      el('span', { class: `mode-badge mode-${m.mode}` }, modeLabel(m.mode)),
      m.answer?.questionClass ? el('span', { class: 'class-chip' }, CLASS_LABEL[m.answer.questionClass] ?? m.answer.questionClass) : null,
      m.answer ? el('span', { class: 'latency' }, `${(m.answer.latencyMs / 1000).toFixed(1)} s`) : null,
      el('code', { class: 'cid', title: 'Correlation ID: links this answer to its audit events' }, m.cid),
    );

    if (m.status === 'pending') {
      return el('article', { class: 'msg msg-assistant', 'aria-busy': 'true' }, meta,
        el('p', { class: 'msg-pending', role: 'status' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), m.phase || 'Working…'));
    }
    if (m.status === 'error') {
      return el('article', { class: 'msg msg-assistant msg-error' }, meta,
        el('div', { class: 'state state-error', role: 'alert' },
          el('p', { class: 'state-title' }, 'No answer was generated'),
          el('p', {}, m.error),
          el('p', { class: 'small' }, 'Nothing was invented to fill the gap. The error above comes from the server.'),
          el('button', { type: 'button', class: 'btn btn-secondary btn-sm', onclick: () => actions.ask(m.question, m.mode) }, 'Retry question'),
        ));
    }

    const a = m.answer;
    return el('article', { class: 'msg msg-assistant', 'aria-label': 'Assistant answer' }, meta,
      a.outcome.kind === 'answered' ? answerBody(m, a) : noticeBody(a),
      a.safety.clinical_disclaimer ? el('p', { class: 'disclaimer' }, a.safety.clinical_disclaimer) : null,
      footer(m, a, s),
    );
  }

  // ---- outcomes that are not answers: urgent, refused, unsupported, no evidence
  function noticeBody(a) {
    const kind = a.outcome.kind;
    if (kind === 'urgent') {
      return el('section', { class: 'answer-block block-urgent', role: 'alert' },
        el('h4', {}, 'Urgent: please read'),
        el('p', {}, a.safety.escalation_message),
      );
    }
    const titles = { refused: 'Outside what this tool can do', unsupported: 'Question not supported', no_evidence: 'No evidence found' };
    return el('section', { class: `answer-block block-notice notice-${kind}` },
      el('h4', {}, titles[kind] ?? 'No answer'),
      el('p', {}, a.outcome.message),
      kind === 'no_evidence' ? retrievalLine(a) : null,
    );
  }

  // ---- a real answer: claims grouped by what they rest on
  function answerBody(m, a) {
    return [
      claimSection(m, a, 'evidence', 'block-evidence', 'Research evidence', 'from the research documents · click E-ids to open the passage'),
      claimSection(m, a, 'patient_record', 'block-fact', 'Patient record', 'fictional case file · not research'),
      claimSection(m, a, 'general_knowledge', 'block-interp', 'General knowledge', 'not from the documents · uncited'),
      claimSection(m, a, 'unsupported', 'block-unsupported', 'Could not be supported', 'stated without support from the sources, so do not rely on it'),
      a.hasConflicts ? el('p', { class: 'conflict-note', role: 'note' }, '⚠ Some retrieved passages disagree. Claims marked "conflicting evidence" list the passages to compare.') : null,
      a.limitations.length ? el('section', { class: 'answer-block block-limits' },
        el('h4', {}, 'Limitations', el('small', {}, 'what the sources do not cover')),
        el('ul', { class: 'plain-list' }, a.limitations.map((l) => el('li', {}, l)))) : null,
      a.issues.length ? el('details', { class: 'backend-checks' },
        el('summary', {}, `Backend checks changed this answer (${a.issues.length})`),
        el('ul', { class: 'plain-list' }, a.issues.map((i) => el('li', {}, i.message)))) : null,
      retrievalLine(a),
    ];
  }

  function claimSection(m, a, basis, cls, title, hint) {
    const claims = a.claims.filter((c) => c.basis === basis);
    if (!claims.length) return null;
    return el('section', { class: `answer-block ${cls}` },
      el('h4', {}, title, el('small', {}, hint)),
      el('ul', { class: 'claim-list' }, claims.map((c) => el('li', { class: 'claim' },
        el('span', {}, c.text),
        ' ', c.citations.map((id) => citeButton(m, a, id)),
        c.conflicts_with.length ? el('span', { class: 'conflict-tag' }, ' conflicting evidence: also compare ',
          c.conflicts_with.map((id) => citeButton(m, a, id))) : null,
        c.flags.includes('invalid_citation_removed') ? el('span', { class: 'flag-note' }, ' (an invalid citation was removed)') : null,
      ))),
    );
  }

  function retrievalLine(a) {
    const r = a.retrieval;
    let text;
    if (r.performed) {
      const how = r.source === 'model_tool' ? 'The model searched the research documents' : 'The research documents were searched';
      text = `${how}: ${a.evidence.length} passage(s) shown to the model` +
        (r.topSimilarity != null ? `, top similarity ${r.topSimilarity.toFixed(2)}` : '') +
        (r.coverage && r.coverage !== 'not_applicable' ? `, coverage ${r.coverage}` : '') + '.';
      if (r.coverageNote) text += ` ${r.coverageNote}`;
    } else {
      text = `No research search was run: ${r.decisionReason ?? 'not needed for this mode or question'}.`;
      if (r.deferredToModel) text = 'Auto mode: the model chose not to search the research documents.';
    }
    return el('p', { class: 'retrieval-line' }, el('strong', {}, 'Retrieval. '), text);
  }

  function citeButton(m, a, id) {
    if (id.startsWith('E')) {
      const c = a.citations[id];
      const ev = a.evidence.find((e) => e.citationId === id);
      const chunkId = c?.chunk_id ?? ev?.chunkId;
      if (!chunkId) return el('span', { class: 'cite static' }, id);
      const active = store.get().activeCitation?.cid === m.cid && store.get().activeCitation?.chunkId === chunkId;
      return el('button', {
        type: 'button', class: `cite${active ? ' active' : ''}`,
        'aria-label': `Citation ${id}: open passage from ${c?.document_title ?? ev?.docTitle}, ${c?.location ?? ev?.location}`,
        onclick: () => actions.openCitation(m.cid, chunkId),
      }, id);
    }
    const fact = a.patientFacts[id];
    return el('button', {
      type: 'button', class: 'cite cite-fact',
      'aria-label': `Patient record ${id}: ${fact?.label ?? 'fact'}`,
      onclick: () => actions.openFact(id, fact),
    }, id);
  }

  // ---- trace and feedback
  function footer(m, a, s) {
    const fb = s.feedback[m.cid] ?? { status: 'idle' };
    const row = el('div', { class: 'msg-actions' },
      el('button', { type: 'button', class: 'btn btn-ghost btn-sm', onclick: () => actions.openTrace(m.cid) }, 'View trace'));
    if (fb.status === 'sent') {
      row.append(el('span', { class: 'feedback-thanks', role: 'status' }, `Thanks: marked ${fb.rating === 'helpful' ? 'helpful' : 'not helpful'}`));
    } else {
      row.append(
        el('span', { class: 'feedback-label' }, 'Was this helpful?'),
        el('button', { type: 'button', class: 'btn btn-secondary btn-sm', disabled: fb.status === 'sending',
          onclick: () => actions.sendFeedback(m.cid, 'helpful') }, 'Helpful'),
        el('button', { type: 'button', class: `btn btn-secondary btn-sm${fb.status === 'choosing' ? ' active' : ''}`,
          'aria-expanded': String(fb.status === 'choosing'), onclick: () => actions.chooseNotHelpful(m.cid) }, 'Not helpful'),
      );
      if (fb.status === 'choosing' || fb.status === 'sending') {
        const select = el('select', { class: 'select-sm', 'aria-label': 'Why was it not helpful?' },
          FEEDBACK_REASONS.map(([v, label]) => el('option', { value: v }, label)));
        row.append(select, el('button', { type: 'button', class: 'btn btn-primary btn-sm', disabled: fb.status === 'sending',
          onclick: () => actions.sendFeedback(m.cid, 'not_helpful', select.value) }, 'Send'));
      }
      if (fb.error) row.append(el('span', { class: 'feedback-error', role: 'alert' }, fb.error));
    }
    return row;
  }
}
