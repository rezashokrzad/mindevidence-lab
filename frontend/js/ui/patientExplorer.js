import { el } from '../lib/dom.js';

export function initPatientExplorer({ store, actions, categories }) {
  const list = document.getElementById('patient-list');
  const count = document.getElementById('patient-count');
  const search = document.getElementById('patient-search');
  const category = document.getElementById('category-filter');

  for (const c of categories) category.append(el('option', { value: c }, c));

  search.addEventListener('input', () => store.set({ search: search.value }));
  category.addEventListener('change', () => store.set({ category: category.value }));

  // Arrow-key navigation between patient buttons.
  list.addEventListener('keydown', (e) => {
    const buttons = [...list.querySelectorAll('.patient-item')];
    const i = buttons.indexOf(document.activeElement);
    if (i === -1) return;
    let next = null;
    if (e.key === 'ArrowDown') next = buttons[Math.min(i + 1, buttons.length - 1)];
    else if (e.key === 'ArrowUp') next = buttons[Math.max(i - 1, 0)];
    else if (e.key === 'Home') next = buttons[0];
    else if (e.key === 'End') next = buttons.at(-1);
    if (next) {
      e.preventDefault();
      next.focus();
    }
  });

  store.watch(
    (s) => [s.patients, s.patientsStatus, s.selectedId, s.search, s.category, s.auth],
    (s) => render(s),
  );

  function render(s) {
    const focusedId = list.contains(document.activeElement) ? document.activeElement.dataset.id : null;
    list.replaceChildren();
    list.setAttribute('aria-busy', String(s.patientsStatus === 'loading'));

    if (s.patientsStatus === 'signed_out') {
      count.textContent = '';
      list.append(el('li', { class: 'state state-empty' },
        el('p', { class: 'state-title' }, 'Sign in to view the synthetic cases'),
        el('p', {}, 'MindEvidence Lab uses Google sign-in. Only your name, email address, and Google account ID are requested.'),
        el('button', { type: 'button', class: 'btn btn-primary btn-sm', onclick: actions.signIn,
          disabled: !s.auth.signInAvailable }, 'Sign in with Google'),
        s.auth.signInAvailable ? null
          : el('p', { class: 'small' }, 'Sign-in is not configured on the server yet.'),
      ));
      return;
    }
    if (s.patientsStatus === 'idle' || s.patientsStatus === 'loading') {
      count.textContent = s.patientsStatus === 'idle' ? 'Checking sign-in…' : 'Loading patients…';
      for (let i = 0; i < 5; i++) list.append(el('li', { class: 'skeleton-item', 'aria-hidden': 'true' }, el('span'), el('span'), el('span')));
      return;
    }
    if (s.patientsStatus === 'error') {
      count.textContent = '';
      list.append(el('li', { class: 'state state-error', role: 'alert' },
        el('p', { class: 'state-title' }, 'Patients could not be loaded'),
        el('p', {}, s.patientsError),
        el('p', { class: 'small' }, 'No mock data is shown in its place. Start the API (see backend/README.md), then retry.'),
        el('button', { type: 'button', class: 'btn btn-secondary btn-sm', onclick: actions.refreshAuth }, 'Retry'),
      ));
      return;
    }

    const q = s.search.trim().toLowerCase();
    const visible = s.patients.filter((p) =>
      (s.category === 'all' || p.category === s.category) &&
      (!q || [p.id, p.category, p.primaryConcern, p.briefHistory, p.treatmentSummary].join(' ').toLowerCase().includes(q)));

    count.textContent = `${visible.length} of ${s.patients.length} synthetic patients`;

    if (!visible.length) {
      list.append(el('li', { class: 'state state-empty' },
        el('p', { class: 'state-title' }, 'No patients match'),
        el('p', {}, 'Clear the search or choose another category.'),
      ));
      return;
    }

    for (const p of visible) {
      const selected = p.id === s.selectedId;
      const btn = el('button', {
        type: 'button',
        class: 'patient-item',
        'aria-current': selected ? 'true' : null,
        'aria-label': `${p.id}, ${p.category}, ${p.age}, ${p.gender}. Synthetic case.${selected ? ' Selected.' : ''}`,
        dataset: { id: p.id },
        onclick: () => actions.selectPatient(p.id),
      },
      el('span', { class: 'pi-top' },
        el('span', { class: 'pi-id' }, p.id),
        el('span', { class: `cat-tag cat-${p.category.toLowerCase()}` }, p.category),
        el('span', { class: 'pi-demo' }, `${p.age} · ${p.gender}`),
      ),
      el('span', { class: 'pi-concern' }, p.primaryConcern),
      el('span', { class: 'pi-history' }, p.briefHistory),
      el('span', { class: 'pi-treatment' }, el('span', { class: 'pi-label' }, 'Treatment: '), p.treatmentSummary),
      );
      list.append(el('li', {}, btn));
    }

    if (focusedId) list.querySelector(`[data-id="${focusedId}"]`)?.focus();
  }
}
