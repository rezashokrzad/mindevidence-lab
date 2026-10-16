import { el } from '../lib/dom.js';
import { modeLabel } from '../services/api.js';

// The inspector shows the text actually retrieved from pgvector. Results appear as soon as
// retrieval finishes, before the answer exists (last.partial), and stay once the answer is done.
export function initEvidenceInspector({ store, actions }) {
  const body = document.getElementById('evidence-body');

  store.watch(
    (s) => [s.mode, s.selectedId, s.lastAnswer[s.selectedId], s.activeCitation],
    (s) => render(s),
  );

  function render(s) {
    body.replaceChildren();
    const last = s.lastAnswer[s.selectedId];
    body.setAttribute('aria-busy', String(last?.status === 'pending' && !last?.partial && s.mode !== 'gk'));

    if (!s.selectedId) {
      return body.append(state('empty', 'No patient selected', 'Select a case and ask a question in RAG or Both mode to see retrieved chunks here.'));
    }

    if (s.mode === 'gk') {
      const items = [state('off', 'Retrieval is off',
        'General Knowledge mode does not search the corpus, so there is no evidence to inspect and answers have no citations.')];
      const shown = last?.answer ?? last?.partial;
      if (shown?.evidence?.length) {
        items.push(el('p', { class: 'muted small' }, `The last retrieval returned ${shown.evidence.length} chunk(s). Switch back to RAG or Both to view them.`));
      }
      return body.append(...items);
    }

    if (!last) {
      return body.append(state('empty', 'No evidence yet', `Ask a question about ${s.selectedId} to retrieve passages from the research documents.`));
    }

    if (last.status === 'pending' && !last.partial) {
      if (last.mode === 'gk') {
        return body.append(state('empty', 'No retrieval for this question', 'The question in progress uses General Knowledge mode.'));
      }
      return body.append(
        el('p', { class: 'loading-line', role: 'status' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Searching the corpus…'),
        ...Array.from({ length: 3 }, () => el('div', { class: 'skeleton-card', 'aria-hidden': 'true' }, el('span'), el('span'), el('span'))),
      );
    }

    if (last.status === 'error') {
      return body.append(state('error', 'Evidence unavailable', `${last.error} The request failed before an answer was produced; retry it from the conversation.`));
    }

    const result = last.status === 'pending' ? last.partial : last.answer;
    if (last.status === 'done' && !last.answer.retrieval.performed) {
      const a = last.answer;
      const why = a.mode === 'gk' ? 'General Knowledge mode does not search the research documents.'
        : (a.retrieval.decisionReason ?? (a.outcome.kind !== 'answered' ? 'This request ended before any search was needed.' : 'No search was needed.'));
      const box = state('empty', 'No research documents were searched for the last question', why);
      if (s.mode !== 'gk' && a.mode === 'gk') {
        box.append(el('button', {
          type: 'button', class: 'btn btn-secondary btn-sm',
          onclick: () => actions.ask(last.question, s.mode),
        }, `Re-run it in ${modeLabel(s.mode)} mode`));
      }
      return body.append(box);
    }

    const evidence = result.evidence;
    const r = result.retrieval;
    const question = last.question;

    if (!evidence.length) {
      return body.append(
        state('no-evidence', 'No evidence found',
          `The search returned no chunks for this question${r?.filters && Object.keys(r.filters).length ? ' with the active filters' : ''}. The answer says so instead of citing anything.`),
        settingsStrip(r),
      );
    }

    body.append(
      last.status === 'pending'
        ? el('p', { class: 'loading-line', role: 'status' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Retrieved. Generating the answer…')
        : null,
      el('p', { class: 'evidence-summary' },
        `${evidence.length} chunk(s) retrieved from the research documents for `, el('q', {}, question)),
      settingsStrip(r),
      el('ol', { class: 'evidence-list' }, evidence.map((e) => card(last, e, s.activeCitation))),
      el('p', { class: 'muted small mock-note' }, r?.note ?? 'Similarity scores rank passages. They are not probabilities of correctness.'),
    );

    const active = body.querySelector('.evidence-card.active');
    if (active) active.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function settingsStrip(r) {
    if (!r) return null;
    const rows = [
      r.source === 'model_tool' ? ['Searched by', 'the model, through the capped search tool'] : null,
      r.model ? ['Model', `${r.model} · ${r.modelVersion}`] : null,
      r.model ? ['Search', `${r.searchType} ${r.metric} · ${r.dimension} dims · top-k ${r.topK}`] : null,
      r.model ? ['Min. similarity', r.minSimilarity ?? 'none'] : null,
      r.model ? ['Filters', Object.keys(r.filters ?? {}).length ? JSON.stringify(r.filters) : 'none'] : null,
      r.model ? ['Ties', r.tieBreak] : null,
      r.coverage && r.coverage !== 'not_applicable' ? ['Coverage', `${r.coverage}${r.coverageNote ? ` · ${r.coverageNote}` : ''}`] : null,
    ].filter(Boolean);
    return el('details', { class: 'retrieval-settings' },
      el('summary', {}, 'Retrieval settings'),
      el('dl', { class: 'ec-meta' }, rows.map(([k, v]) => el('div', {}, el('dt', {}, k), el('dd', {}, String(v))))));
  }

  function card(last, e, activeCitation) {
    const cid = last.answer?.correlationId ?? null;
    const active = activeCitation?.chunkId === e.chunkId && (cid === null || activeCitation?.cid === cid);
    return el('li', { class: `evidence-card${active ? ' active' : ''}` },
      el('div', { class: 'ec-top' },
        el('span', { class: 'cite static' }, e.citationId),
        el('span', { class: 'ec-doc' }, e.docTitle),
        el('span', { class: 'ec-score', title: 'Cosine similarity of the query and the best-matching part of this chunk. Not a probability of correctness.' },
          e.similarity.toFixed(3)),
      ),
      el('dl', { class: 'ec-meta' },
        el('div', {}, el('dt', {}, 'Section'), el('dd', {}, e.section)),
        el('div', {}, el('dt', {}, 'Pages'), el('dd', {}, `${e.pages} (PDF)${e.pageLabel ? ` · printed ${e.pageLabel}` : ''}`)),
        el('div', {}, el('dt', {}, 'Chunk'), el('dd', {}, el('code', {}, e.chunkId))),
        el('div', {}, el('dt', {}, 'Distance'), el('dd', {}, e.distance.toFixed(3))),
      ),
      el('p', { class: 'ec-excerpt' }, e.excerpt),
      cid
        ? el('button', { type: 'button', class: 'btn btn-secondary btn-sm',
          onclick: () => actions.openCitation(cid, e.chunkId) }, `Open chunk ${e.citationId}`)
        : el('button', { type: 'button', class: 'btn btn-secondary btn-sm',
          onclick: () => actions.openChunk(e.chunkId, e.citationId, e) }, `Open chunk ${e.citationId}`),
    );
  }

  function state(kind, title, text) {
    return el('div', { class: `state state-${kind}`, role: kind === 'error' ? 'alert' : null },
      el('p', { class: 'state-title' }, title), el('p', {}, text));
  }
}
