import { marked } from 'marked';
import DOMPurify from 'dompurify';

// The single renderer for evidence text. It has no network or navigation side
// effects. A caller may explicitly route a link through an authorized SDK flow.
export function renderMarkdown(root, markdown, { onLink } = {}) {
  const clean = DOMPurify.sanitize(marked.parse(String(markdown ?? ''), { async: false, gfm: true }), {
    RETURN_DOM_FRAGMENT: true,
    ALLOWED_TAGS: ['p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'em', 'del', 'blockquote', 'pre', 'code', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'a'],
    ALLOWED_ATTR: ['href', 'title', 'colspan', 'rowspan', 'start'],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
  });
  for (const anchor of clean.querySelectorAll('a')) {
    const href = anchor.getAttribute('href') || '';
    const safeReference = /^(?:[a-z][a-z0-9_]*_[0-9a-f]{12})(?:\/[^\s]*)?(?:#L\d+(?:-L\d+)?)?$/.test(href);
    const safeWeb = /^https?:\/\//i.test(href);
    if (onLink && (safeReference || safeWeb)) {
      const button = root.ownerDocument.createElement('button');
      button.type = 'button'; button.className = 'portalButton projectionEvidenceLink';
      button.textContent = anchor.textContent;
      button.title = href;
      button.addEventListener('click', () => onLink(href));
      anchor.replaceWith(button);
    } else {
      // No href remains in the sandbox. Even ordinary links otherwise navigate
      // its document and lose both local context and the SDK channel.
      const span = root.ownerDocument.createElement('span');
      span.textContent = anchor.textContent;
      if (href) span.title = href;
      anchor.replaceWith(span);
    }
  }
  root.replaceChildren(clean);
}
