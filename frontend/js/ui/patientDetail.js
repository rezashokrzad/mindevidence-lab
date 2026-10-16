import { el } from '../lib/dom.js';

export function initPatientDetail({ store, actions }) {
  const body = document.getElementById('detail-body');
  const title = document.getElementById('detail-title');
  const panel = document.getElementById('patient-detail');
  const toggle = document.getElementById('detail-toggle');

  toggle.addEventListener('click', () => {
    const collapsed = panel.classList.toggle('collapsed');
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.textContent = collapsed ? 'Expand' : 'Collapse';
  });

  store.watch((s) => [s.detail], (s) => render(s.detail));

  function render(detail) {
    body.replaceChildren();
    body.setAttribute('aria-busy', String(detail.status === 'loading'));

    if (detail.status === 'idle') {
      title.textContent = 'Patient detail';
      body.append(el('div', { class: 'state state-empty' },
        el('p', { class: 'state-title' }, 'No patient selected'),
        el('p', {}, 'Choose a synthetic case from the patient explorer to see its record.'),
      ));
      return;
    }
    if (detail.status === 'loading') {
      title.textContent = `Patient detail · ${detail.id}`;
      body.append(el('div', { class: 'skeleton-block', 'aria-hidden': 'true' }, el('span'), el('span'), el('span'), el('span')),
        el('p', { class: 'visually-hidden' }, `Loading ${detail.id}`));
      return;
    }
    if (detail.status === 'error') {
      body.append(el('div', { class: 'state state-error', role: 'alert' },
        el('p', { class: 'state-title' }, 'Case record could not be loaded'),
        el('p', {}, detail.error),
        el('button', { type: 'button', class: 'btn btn-secondary btn-sm', onclick: () => actions.selectPatient(detail.id, { force: true }) }, 'Retry'),
      ));
      return;
    }

    const p = detail.data;
    title.textContent = `Patient detail · ${p.id}`;

    body.append(
      el('div', { class: 'detail-header' },
        el('div', { class: 'detail-id-row' },
          el('span', { class: 'detail-id' }, p.id),
          el('span', { class: 'badge badge-synthetic' }, 'Synthetic case'),
          el('span', { class: `cat-tag cat-${p.category.toLowerCase()}` }, p.category),
        ),
        el('p', { class: 'detail-demo' }, `${p.age} years · ${p.gender} · ${p.primaryConcern}`),
        el('p', { class: 'detail-concern' }, p.presentingConcern),
      ),
      el('p', { class: 'provenance-label prov-fact' },
        `Patient facts · synthetic record from the database · case as of ${p.caseAsOf} · not a real person`),
      el('div', { class: 'detail-grid fact-region' },
        section('Background', el('dl', { class: 'kv-list' },
          kv('Occupation', p.occupation), kv('Living situation', p.livingSituation), kv('Social context', p.socialContext))),
        section('Substance / behavior use', el('ul', { class: 'plain-list use-list' }, p.uses.map((u) =>
          el('li', {},
            el('strong', {}, u.target), u.isPrimary ? el('span', { class: 'mini-tag' }, 'primary') : null,
            ` · ${u.status}`,
            el('span', { class: 'use-meta' },
              `${u.pattern}. Frequency at intake: ${u.frequency}. Quantity: ${u.quantity}. Route: ${u.route}. `
              + `First use age: ${u.firstUseAge}. Regular use age: ${u.regularUseAge}.`),
            u.notes ? el('span', { class: 'use-meta muted' }, u.notes) : null)))),
        section('Observed behaviors', el('ul', { class: 'plain-list' }, p.observedBehaviors.map((b) =>
          el('li', {}, b.text, el('span', { class: 'obs-meta' }, ` · ${b.date} · ${b.source}`))))),
        section('History', el('ol', { class: 'history-list' }, p.history.map((h) =>
          el('li', {}, el('span', { class: 'history-date' }, h.date), el('span', {}, h.event))))),
        section('Triggers', chips(p.triggers)),
        section('Risk factors', chips(p.riskFactors)),
        section('Protective factors', chips(p.protectiveFactors, 'chip-protective')),
        section('Co-occurring symptoms', [
          el('p', { class: 'muted small' }, 'Case descriptions, not diagnoses.'),
          el('ul', { class: 'plain-list' }, p.symptoms.map((s) => el('li', {},
            s.description, ' ', el('span', { class: `status-tag status-${s.status}` }, s.status),
            el('span', { class: 'obs-meta' }, ` · ${s.domain} · since ${s.since}`),
            s.notes ? el('span', { class: 'use-meta muted' }, s.notes) : null))),
        ]),
        section('Previous interventions', el('table', { class: 'data-table' },
          el('thead', {}, el('tr', {}, el('th', { scope: 'col' }, 'Intervention'), el('th', { scope: 'col' }, 'Period'),
            el('th', { scope: 'col' }, 'Status'), el('th', { scope: 'col' }, 'Outcome'))),
          el('tbody', {}, p.previousInterventions.map((iv) =>
            el('tr', {}, el('td', {}, iv.name), el('td', { class: 'nowrap' }, iv.period), el('td', {}, iv.status),
              el('td', {}, iv.outcome)))),
        ), 'wide'),
        section('Progress', [
          el('p', {}, el('span', { class: 'stage-pill' }, `Stage: ${p.progress.stage}`),
            p.stageHistory.length > 1
              ? el('span', { class: 'obs-meta' }, ' Stage history: '
                + p.stageHistory.map((s) => `${s.stage} (${s.date})`).join(' → '))
              : null),
          p.motivationNotes ? el('p', { class: 'muted' }, p.motivationNotes) : null,
          el('p', {}, p.progress.summary),
          el('dl', { class: 'indicator-list' }, p.progress.indicators.map((ind) =>
            el('div', {}, el('dt', {}, ind.label), el('dd', {}, ind.value)))),
        ], 'wide'),
      ),
    );
  }

  function section(heading, content, size) {
    return el('section', { class: `detail-section${size === 'wide' ? ' wide' : ''}` },
      el('h3', {}, heading), content);
  }

  function kv(label, value) {
    return el('div', {}, el('dt', {}, label), el('dd', { class: value === 'Not recorded' ? 'muted' : null }, value));
  }

  function chips(items, extraClass = '') {
    if (!items.length) return el('p', { class: 'muted small' }, 'None recorded.');
    return el('ul', { class: 'chip-list' }, items.map((t) => el('li', { class: `chip ${extraClass}` }, t)));
  }
}
