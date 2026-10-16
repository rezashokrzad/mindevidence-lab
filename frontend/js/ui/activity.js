import { el, formatTime } from '../lib/dom.js';

// Activity dashboard: everything shown here comes from the backend audit trail (your own events only),
// since you signed in on this device.

const TABS = [['timeline', 'Timeline'], ['answers', 'Answers'], ['model', 'Model & errors']];

const FILTERS = [
  ['all', 'All events'], ['auth', 'Sign-in'], ['question', 'Questions'], ['retrieval', 'Retrieval'], ['llm', 'Model calls'],
  ['workflow', 'Workflow nodes'], ['citation', 'Citation checks'], ['answer', 'Answers'], ['feedback', 'Feedback'],
  ['email', 'Email reports'], ['errors', 'Errors only'],
];

const OUTCOME_LABEL = { answered: 'answered', refused: 'refused', unsupported: 'unsupported question', urgent: 'urgent escalation',
  no_evidence: 'no evidence', error: 'error' };

export function initActivity({ store, actions }) {
  const body = document.getElementById('activity-body');
  const tabs = document.getElementById('activity-tabs');
  document.getElementById('activity-refresh').addEventListener('click', () => actions.refreshActivity());

  tabs.replaceChildren(...TABS.map(([id, label]) => el('button', {
    type: 'button', role: 'tab', id: `tab-${id}`, 'aria-controls': 'activity-body', dataset: { tab: id },
    onclick: () => store.set({ activityTab: id }),
  }, label)));
  tabs.addEventListener('keydown', (e) => {
    const buttons = [...tabs.querySelectorAll('[role="tab"]')];
    const i = buttons.indexOf(document.activeElement);
    if (i < 0 || !['ArrowRight', 'ArrowLeft'].includes(e.key)) return;
    e.preventDefault();
    const next = buttons[(i + (e.key === 'ArrowRight' ? 1 : buttons.length - 1)) % buttons.length];
    next.focus();
    next.click();
  });

  store.watch((s) => [s.activity, s.activityTab, s.activityFilter, s.auth.status], (s) => render(s));

  function render(s) {
    for (const t of tabs.querySelectorAll('[role="tab"]')) {
      const on = t.dataset.tab === s.activityTab;
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
    }
    body.setAttribute('aria-labelledby', `tab-${s.activityTab}`);
    body.replaceChildren();
    body.setAttribute('aria-busy', String(s.activity.status === 'loading'));
    if (s.auth.status !== 'signed_in') {
      return body.append(state('empty', 'Sign in to see activity', 'Your session activity appears here after you sign in.'));
    }
    if (s.activity.status === 'idle' || (s.activity.status === 'loading' && !s.activity.data)) {
      return body.append(el('p', { class: 'loading-line', role: 'status' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Loading activity…'));
    }
    if (s.activity.status === 'error') {
      return body.append(el('div', { class: 'state state-error', role: 'alert' },
        el('p', { class: 'state-title' }, 'Activity could not be loaded'), el('p', {}, s.activity.error),
        el('button', { type: 'button', class: 'btn btn-secondary btn-sm', onclick: () => actions.refreshActivity() }, 'Retry')));
    }
    const d = s.activity.data;
    if (s.activityTab === 'timeline') body.append(...timeline(d, s));
    else if (s.activityTab === 'answers') body.append(...answers(d));
    else body.append(...modelAndErrors(d));
  }

  // ---------------------------------------------------------------- timeline
  function matches(e, filter) {
    if (filter === 'all') return true;
    if (filter === 'errors') return e.severity === 'error';
    return e.event_type.startsWith(filter);
  }

  function timeline(d, s) {
    const events = d.events.filter((e) => matches(e, s.activityFilter));
    const select = el('select', { class: 'select-sm', id: 'activity-filter', 'aria-label': 'Filter events',
      onchange: (e) => store.set({ activityFilter: e.target.value }) },
    FILTERS.map(([v, l]) => el('option', { value: v, selected: v === s.activityFilter }, l)));
    const list = events.length
      ? el('ol', { class: 'timeline-list', 'aria-label': 'Audit events, newest first' }, events.map(eventRow))
      : state('empty', 'No events', 'Nothing matches this filter yet. Ask a question to generate events.');
    return [el('div', { class: 'activity-tools' }, el('label', { for: 'activity-filter', class: 'visually-hidden' }, 'Filter events'),
      select, el('span', { class: 'muted small' }, `${events.length} of ${d.events.length} shown · since ${formatTime(d.since)}`)), list];
  }

  function eventRow(e) {
    const sev = e.severity === 'error' ? 'tl-error' : e.severity === 'warning' ? 'tl-warning' : `tl-${e.event_type.split('.')[0]}`;
    const meta = Object.entries(e.metadata ?? {}).filter(([k, v]) => typeof v !== 'object' && v !== null && k !== 'reason')
      .slice(0, 4).map(([k, v]) => `${k}: ${v}`).join(' · ');
    return el('li', { class: `tl-item ${sev}` },
      el('span', { class: 'tl-dot', 'aria-hidden': 'true' }),
      el('div', { class: 'tl-content' },
        el('div', { class: 'tl-row' },
          el('span', { class: 'tl-type' }, e.event_type),
          e.node ? el('span', { class: 'tl-msg' }, e.node) : null,
          e.status ? el('span', { class: `status-tag status-${e.status}` }, e.status) : null,
          e.latency_ms != null ? el('span', { class: 'tl-lat' }, `${e.latency_ms} ms`) : null),
        e.metadata?.reason ? el('p', { class: 'tl-detail' }, e.metadata.reason) : null,
        el('div', { class: 'tl-row tl-sub' },
          el('time', { datetime: e.occurred_at }, formatTime(e.occurred_at)),
          e.correlation_id ? el('code', { class: 'cid' }, e.correlation_id) : null,
          e.patient_id ? el('span', {}, e.patient_id) : null),
        meta ? el('p', { class: 'tl-detail' }, meta) : null));
  }

  // ---------------------------------------------------------------- answers
  function answers(d) {
    if (!d.answers.length) return [state('empty', 'No answers yet', 'Each answer you request appears here with its retrieval trace and timings.')];
    return [el('ul', { class: 'answer-activity' }, d.answers.map((a) => {
      const r = a.retrieval;
      return el('li', { class: `aa-card${a.outcome === 'error' ? ' aa-error' : ''}` },
        el('div', { class: 'aa-head' },
          el('strong', {}, OUTCOME_LABEL[a.outcome] ?? a.outcome ?? 'in progress'),
          el('span', { class: 'muted small' }, ` · ${a.mode ?? '?'} mode · ${a.question_class ?? 'unclassified'} · ${a.patient_id ?? 'no case'}`),
          el('span', { class: 'aa-lat', title: 'Time to answer' }, a.latency_ms != null ? `${(a.latency_ms / 1000).toFixed(1)} s` : '')),
        el('div', { class: 'tl-row tl-sub' }, el('time', { datetime: a.started_at }, formatTime(a.started_at)), el('code', { class: 'cid' }, a.correlation_id),
          a.feedback ? el('span', {}, `rated ${a.feedback.replace('_', ' ')}`) : null),
        el('p', { class: 'aa-line' }, el('strong', {}, 'Retrieval: '), r?.chunk_ids?.length
          ? `${r.result_count} chunk(s), top similarity ${r.top_similarity != null ? Number(r.top_similarity).toFixed(2) : 'n/a'}, ${r.settings?.search_type ?? 'search'} ${r.settings?.metric ?? ''} top-k ${r.settings?.top_k ?? '?'}`
          : (r?.performed === false ? 'not performed' : 'none')),
        r?.chunk_ids?.length ? el('p', { class: 'aa-chunks' }, r.chunk_ids.map((c) => el('code', {}, c))) : null,
        el('p', { class: 'aa-line' }, el('strong', {}, 'Claims: '), `${a.claims ?? 0} total, ${a.unsupported_claims ?? 0} unsupported`),
        el('ol', { class: 'node-list', 'aria-label': 'Workflow nodes' }, a.nodes.map((n) => el('li', { class: `node node-${n.status}`, title: n.reason ?? '' },
          el('span', { class: 'node-name' }, n.node), el('span', { class: 'node-status' }, n.status ?? ''), el('span', { class: 'node-ms' }, `${n.latency_ms ?? 0} ms`)))),
        a.model ? el('p', { class: 'aa-line' }, el('strong', {}, 'Model: '),
          `${a.model.response?.calls?.length ?? 0} call(s)${a.model.response?.repair_attempts ? `, ${a.model.response.repair_attempts} repair` : ''}`
          + ` · ${a.model.response?.calls?.reduce((n, c) => n + (c.completion_tokens ?? 0), 0) ?? 0} output tokens · ${a.model.status ?? ''}`) : null,
        a.errors.length ? el('ul', { class: 'plain-list aa-errors' }, a.errors.map((e) => el('li', {}, e))) : null,
        el('button', { type: 'button', class: 'btn btn-ghost btn-sm', onclick: () => actions.openTrace(a.correlation_id) }, 'Full trace'));
    }))];
  }

  // ---------------------------------------------------------------- model and errors
  function modelAndErrors(d) {
    const t = d.totals;
    const cfg = d.model_config_in_use;
    const stat = (label, value) => el('div', {}, el('dt', {}, label), el('dd', {}, String(value)));
    return [
      el('section', { class: 'activity-section' }, el('h3', {}, 'This session'),
        el('dl', { class: 'stat-grid' },
          stat('Answers', t.answers), stat('Answered', t.answered), stat('Refused / unsupported', t.refused_or_unsupported),
          stat('Searches', t.retrievals), stat('Unsupported claims', t.unsupported_claims), stat('Errors', t.errors),
          stat('Median latency', t.median_latency_ms != null ? `${(t.median_latency_ms / 1000).toFixed(1)} s` : 'n/a'),
          stat('Slowest', t.max_latency_ms != null ? `${(t.max_latency_ms / 1000).toFixed(1)} s` : 'n/a'))),
      el('section', { class: 'activity-section' }, el('h3', {}, 'Model configuration'),
        cfg ? el('dl', { class: 'ec-meta' }, [
          ['Provider', cfg.provider], ['Model', cfg.model], ['Endpoint host', cfg.endpoint_host],
          ['Reasoning mode', cfg.thinking], ['Temperature', cfg.temperature], ['Max output tokens', cfg.max_output_tokens],
          ['Timeouts', cfg.timeouts_seconds ? `connect ${cfg.timeouts_seconds.connect}s, read ${cfg.timeouts_seconds.read}s` : null],
          ['Retries', cfg.retry_policy ? `up to ${cfg.retry_policy.max_retries} on ${cfg.retry_policy.retry_on.join(', ')}` : null],
          ['Max search calls', cfg.max_tool_calls],
        ].filter(([, v]) => v != null && v !== '').map(([k, v]) => el('div', {}, el('dt', {}, k), el('dd', {}, String(v)))))
          : el('p', { class: 'muted' }, 'No model call yet in this session. The configuration appears after the first answer. The API key is never shown.')),
      el('section', { class: 'activity-section' }, el('h3', {}, 'Errors'),
        d.errors.length ? el('ul', { class: 'plain-list' }, d.errors.map((e) => el('li', {},
          el('code', {}, e.event_type), ` ${e.metadata?.error_code ?? e.metadata?.reason ?? e.status ?? ''} · `,
          el('time', { datetime: e.occurred_at }, formatTime(e.occurred_at)),
          e.correlation_id ? el('code', { class: 'cid' }, ` ${e.correlation_id}`) : null)))
          : el('p', { class: 'muted' }, 'No errors in this session.')),
    ];
  }

  function state(kind, title, text) {
    return el('div', { class: `state state-${kind}` }, el('p', { class: 'state-title' }, title), el('p', {}, text));
  }
}
