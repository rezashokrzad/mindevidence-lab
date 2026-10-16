import { el } from '../lib/dom.js';

// Shows a stored chunk (GET /api/chunks/{id}) with its document, section and page provenance.
export function initCitationDialog() {
  const dialog = document.getElementById('citation-dialog');
  const body = document.getElementById('citation-dialog-body');
  const title = document.getElementById('citation-dialog-title');
  document.getElementById('citation-close').addEventListener('click', () => dialog.close());
  // Close when clicking the backdrop.
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });

  function showLoading(citationId) {
    title.textContent = `Citation ${citationId}`;
    body.replaceChildren(el('p', { class: 'loading-line', role: 'status' },
      el('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Loading chunk…'));
    if (!dialog.open) dialog.showModal();
  }

  function showError(message) {
    body.replaceChildren(el('div', { class: 'state state-error', role: 'alert' },
      el('p', { class: 'state-title' }, 'Chunk could not be loaded'), el('p', {}, message)));
  }

  function showChunk({ chunk, evidence }) {
    const doc = chunk.document;
    const pages = chunk.page_start === chunk.page_end ? `${chunk.page_start}` : `${chunk.page_start}–${chunk.page_end}`;
    const printed = chunk.page_label_start
      ? ` (printed ${chunk.page_label_start === chunk.page_label_end || !chunk.page_label_end ? chunk.page_label_start : `${chunk.page_label_start}–${chunk.page_label_end}`})`
      : '';
    title.textContent = `${evidence?.citationId ? `${evidence.citationId} · ` : ''}${doc.short_title}`;
    const matched = evidence?.matchedPassage;
    body.replaceChildren(
      el('dl', { class: 'dialog-meta' },
        row('Document', doc.title),
        row('Citation', doc.citation),
        doc.doi ? row('DOI', doc.doi) : null,
        doc.license ? row('Licence', doc.license) : null,
        row('Section', chunk.section_path.length ? chunk.section_path.join(' › ') : 'No section heading'),
        row('PDF pages', `${pages}${printed}`),
        row('Chunk ID', el('code', {}, chunk.chunk_id)),
        evidence?.similarity != null ? row('Similarity', `${evidence.similarity.toFixed(3)} (cosine; not a probability)`) : null,
        row('Content hash', el('code', {}, chunk.content_hash.slice(0, 16) + '…')),
      ),
      el('blockquote', { class: 'excerpt' }, matched ? marked(chunk.text, matched) : chunk.text),
      matched ? el('p', { class: 'muted small' }, 'Highlighted: the part of this chunk that matched the question best.') : null,
      el('p', { class: 'muted small' },
        'This is the original text extracted from the document, stored as one chunk. ',
        chunk.previous_chunk_id ? el('code', {}, chunk.previous_chunk_id) : null,
        chunk.previous_chunk_id && chunk.next_chunk_id ? ' ← neighbours → ' : null,
        chunk.next_chunk_id ? el('code', {}, chunk.next_chunk_id) : null),
    );
    dialog.querySelector('#citation-close').focus();
  }

  return { showLoading, showError, showChunk };
}

// Text with the matched passage wrapped in <mark> (built from text nodes only).
function marked(text, passage) {
  const i = text.indexOf(passage);
  if (i < 0) return text;
  return [text.slice(0, i), el('mark', {}, passage), text.slice(i + passage.length)];
}

function row(label, value) {
  return el('div', {}, el('dt', {}, label), el('dd', {}, value));
}
