import { el, formatTime } from '../lib/dom.js';

// Small dialogs: patient-record fact, execution trace, and optional email reports.

function makeDialog(id, titleId) {
  const dialog = document.getElementById(id);
  const title = document.getElementById(titleId);
  dialog.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => dialog.close()));
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
  return { dialog, title, body: dialog.querySelector('.dialog-content') };
}

export function initDialogs({ actions }) {
  const fact = makeDialog('fact-dialog', 'fact-dialog-title');
  const trace = makeDialog('trace-dialog', 'trace-dialog-title');
  const email = makeDialog('email-dialog', 'email-dialog-title');

  return {
    showFact(id, f) {
      fact.title.textContent = `Patient record ${id}`;
      fact.body.replaceChildren(
        el('p', { class: 'provenance-label prov-fact' }, 'Fictional case file · not published research'),
        el('dl', { class: 'dialog-meta' }, f ? [row('Field', f.label), row('Value', f.value)] : row('Fact', 'Not available')),
      );
      fact.dialog.showModal();
      fact.dialog.querySelector('[data-close]').focus();
    },

    traceLoading(cid) {
      trace.title.textContent = `Trace ${cid}`;
      trace.body.replaceChildren(el('p', { class: 'loading-line', role: 'status' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Loading trace…'));
      if (!trace.dialog.open) trace.dialog.showModal();
    },
    traceError(message) {
      trace.body.replaceChildren(el('div', { class: 'state state-error', role: 'alert' }, el('p', { class: 'state-title' }, 'Trace unavailable'), el('p', {}, message)));
    },
    showTrace(t) {
      trace.title.textContent = `Trace ${t.correlation_id}`;
      trace.body.replaceChildren(
        el('p', { class: 'muted small' }, 'Audit events for this question, in order. They hold ids, counts, settings and statuses, never question text or keys.'),
        el('ol', { class: 'timeline-list' }, t.events.map((e) => {
          const meta = Object.entries(e.metadata ?? {}).filter(([k, v]) => k !== 'reason' && v !== null && v !== '')
            .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ');
          return el('li', { class: `tl-item ${e.severity === 'error' ? 'tl-error' : e.severity === 'warning' ? 'tl-warning' : `tl-${e.event_type.split('.')[0]}`}` },
            el('span', { class: 'tl-dot', 'aria-hidden': 'true' }),
            el('div', { class: 'tl-content' },
              el('div', { class: 'tl-row' }, el('span', { class: 'tl-type' }, e.event_type), e.node ? el('span', { class: 'tl-msg' }, e.node) : null,
                e.status ? el('span', { class: `status-tag status-${e.status}` }, e.status) : null,
                e.latency_ms != null ? el('span', { class: 'tl-lat' }, `${e.latency_ms} ms`) : null),
              e.metadata?.reason ? el('p', { class: 'tl-detail' }, e.metadata.reason) : null,
              el('div', { class: 'tl-row tl-sub' }, el('time', { datetime: e.occurred_at }, formatTime(e.occurred_at))),
              meta ? el('p', { class: 'tl-detail trace-meta' }, meta) : null));
        })));
    },

    // ---- email reports
    openEmail() {
      email.dialog.showModal();
      email.dialog.querySelector('[data-close]').focus();
    },
    renderEmail(state) {
      if (!state.data && state.status !== 'error') {
        email.body.replaceChildren(el('p', { class: 'loading-line', role: 'status' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Loading…'));
        return;
      }
      if (state.status === 'error') {
        email.body.replaceChildren(el('div', { class: 'state state-error', role: 'alert' }, el('p', { class: 'state-title' }, 'Could not load email-report settings'),
          el('p', {}, state.error), el('button', { type: 'button', class: 'btn btn-secondary btn-sm', onclick: () => actions.loadEmailStatus() }, 'Retry')));
        return;
      }
      const s = state.data;
      const busy = state.status === 'working';
      const parts = [
        el('p', {}, 'Optional. Email reports send you a short activity summary (counts only) through your own Gmail account. ',
          'If you never enable them, nothing changes and Google is never asked for Gmail access.'),
        el('div', { class: 'two-col' },
          el('section', {}, el('h3', {}, 'What is sent'), el('ul', { class: 'plain-list' }, s.sent.map((x) => el('li', {}, x)))),
          el('section', {}, el('h3', {}, 'What is never sent'), el('ul', { class: 'plain-list' }, s.not_sent.map((x) => el('li', {}, x))))),
        el('p', { class: 'muted small' }, `Permission requested: send-only access to Gmail (gmail.send) plus identity, to confirm it is your account. `
          + `Recipient: ${s.recipient}. Limits: one report per ${Math.round(s.limits.min_interval_seconds / 60)} min, ${s.limits.max_per_day} per day.`),
      ];
      if (!s.available) {
        parts.push(el('div', { class: 'state state-off' }, el('p', { class: 'state-title' }, 'Not available on this server'), el('p', {}, s.unavailable_reason)));
      } else if (s.enabled) {
        parts.push(
          el('div', { class: 'state state-no-evidence' }, el('p', { class: 'state-title' }, 'Email reports are on'),
            el('p', {}, `Permission granted ${s.granted_at ? formatTime(s.granted_at) : ''}. `
              + (s.last_sent_at ? `Last sent ${formatTime(s.last_sent_at)}.` : 'Nothing sent yet.')
              + (s.consecutive_failures ? ` ${s.consecutive_failures} recent delivery failure(s).` : ''))),
          el('div', { class: 'dialog-actions' },
            el('button', { type: 'button', class: 'btn btn-primary', disabled: busy, onclick: () => actions.sendEmailReport() }, busy ? 'Working…' : 'Send me a summary now'),
            el('button', { type: 'button', class: 'btn btn-secondary', disabled: busy, onclick: () => actions.disableEmailReports() }, 'Turn off and revoke permission')));
      } else {
        const note = { revoked: 'Google no longer accepts the saved permission. You can enable email reports again.',
          error: 'The saved permission could not be used. You can enable email reports again.',
          disabled: 'Email reports are off.' }[s.status];
        if (note) parts.push(el('p', { class: 'muted' }, note));
        parts.push(el('div', { class: 'dialog-actions' },
          el('button', { type: 'button', class: 'btn btn-primary', disabled: busy, onclick: () => actions.enableEmailReports() }, 'Enable email reports (opens Google)')));
      }
      if (state.message) parts.push(el('p', { class: `email-message ${state.messageKind ?? ''}`, role: 'status' }, state.message));
      email.body.replaceChildren(...parts);
    },
  };
}

function row(label, value) {
  return el('div', {}, el('dt', {}, label), el('dd', {}, value));
}
