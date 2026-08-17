/**
 * Strip executable and unsafe content out of extracted article HTML.
 *
 * Runs both in the injected extractor (before anything leaves the page) and
 * again on the popup side before content is printed or sent to the API.
 */

/** Elements removed outright, with their subtree. */
const FORBIDDEN_TAGS = new Set([
  'SCRIPT',
  'STYLE',
  'LINK',
  'META',
  'BASE',
  'IFRAME',
  'FRAME',
  'FRAMESET',
  'OBJECT',
  'EMBED',
  'APPLET',
  'FORM',
  'INPUT',
  'BUTTON',
  'SELECT',
  'TEXTAREA',
  'OPTION',
  'NOSCRIPT',
  'TEMPLATE',
  'SLOT',
  'CANVAS',
  'AUDIO',
  'VIDEO',
  'SOURCE',
  'TRACK',
  'SVG',
  'MATH',
  'DIALOG',
  'PORTAL',
]);

/** Page chrome that Readability sometimes leaves behind. */
const CHROME_TAGS = new Set(['NAV', 'HEADER', 'FOOTER', 'ASIDE', 'MENU']);

const IMAGE_TAGS = new Set(['IMG', 'PICTURE', 'FIGURE', 'FIGCAPTION']);

const SAFE_URL = /^(https?:|mailto:|#|\/|\.\/|\.\.\/)/i;

function isUnsafeUrl(value: string): boolean {
  const trimmed = value.trim();
  if (trimmed === '') return true;
  // Block javascript:, data:, vbscript:, blob:, filesystem: and friends.
  return !SAFE_URL.test(trimmed);
}

function scrubAttributes(el: Element): void {
  for (const attr of Array.from(el.attributes)) {
    const name = attr.name.toLowerCase();
    // Inline event handlers of every kind.
    if (name.startsWith('on')) {
      el.removeAttribute(attr.name);
      continue;
    }
    // Inline styles can load remote resources and hide content.
    if (name === 'style') {
      el.removeAttribute(attr.name);
      continue;
    }
    if (name === 'srcdoc' || name === 'formaction' || name === 'ping') {
      el.removeAttribute(attr.name);
      continue;
    }
    if ((name === 'href' || name === 'src' || name === 'action') && isUnsafeUrl(attr.value)) {
      el.removeAttribute(attr.name);
    }
  }
}

export interface SanitizeOptions {
  includeImages: boolean;
}

/**
 * Sanitise a DOM subtree in place. Exported so the injected extractor can work
 * on a live document fragment without a second parse.
 */
export function sanitizeElement(root: Element, options: SanitizeOptions): void {
  const doomed: Element[] = [];
  const walk = (el: Element): void => {
    const tag = el.tagName.toUpperCase();
    if (FORBIDDEN_TAGS.has(tag) || CHROME_TAGS.has(tag)) {
      doomed.push(el);
      return;
    }
    if (!options.includeImages && IMAGE_TAGS.has(tag)) {
      doomed.push(el);
      return;
    }
    scrubAttributes(el);
    if (!options.includeImages) {
      el.removeAttribute('background');
    }
    for (const child of Array.from(el.children)) walk(child);
  };

  for (const child of Array.from(root.children)) walk(child);
  for (const el of doomed) el.remove();
}

/** Sanitise an HTML string. Returns cleaned inner HTML. */
export function sanitizeHtml(html: string, options: SanitizeOptions): string {
  const doc = new DOMParser().parseFromString(`<div id="root">${html}</div>`, 'text/html');
  const root = doc.getElementById('root');
  if (!root) return '';
  sanitizeElement(root, options);
  return root.innerHTML.trim();
}

/** Collapse a page title into something safe to render as text. */
export function cleanTitle(title: string): string {
  return title.replace(/\s+/g, ' ').trim().slice(0, 300);
}
