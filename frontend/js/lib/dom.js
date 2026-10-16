// Small DOM helpers. Text is always inserted as text nodes (never innerHTML),
// because document chunks will be untrusted retrieved text in later batches.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, String(value));
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const child of [children].flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

export function formatTime(ts) {
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// Wraps occurrences of `terms` in <mark>, built from text nodes only.
export function highlight(text, terms = []) {
  const usable = terms.filter((t) => t && t.length >= 3);
  if (!usable.length) return [text];
  const escaped = usable.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const re = new RegExp(`\\b(${escaped.join('|')})\\w*`, 'gi');
  const parts = [];
  let last = 0;
  for (const m of text.matchAll(re)) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    parts.push(el('mark', {}, m[0]));
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

let idSeq = 0;
export const uid = (prefix = 'id') => `${prefix}-${Date.now().toString(36)}-${(++idSeq).toString(36)}`;

export function correlationId() {
  const bytes = crypto.getRandomValues(new Uint8Array(4));
  return 'cid-' + [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
}

// Tiny observable store with selector-based subscriptions.
export function createStore(initial) {
  let state = initial;
  const listeners = new Set();
  return {
    get: () => state,
    set(patch) {
      const next = typeof patch === 'function' ? patch(state) : patch;
      state = { ...state, ...next };
      for (const fn of listeners) fn(state);
    },
    // Calls `fn` when any value returned by `selector` changes (shallow compare).
    watch(selector, fn) {
      let prev = selector(state);
      fn(state, prev);
      const listener = (s) => {
        const next = selector(s);
        const changed = next.length !== prev.length || next.some((v, i) => !Object.is(v, prev[i]));
        if (changed) {
          prev = next;
          fn(s);
        }
      };
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
