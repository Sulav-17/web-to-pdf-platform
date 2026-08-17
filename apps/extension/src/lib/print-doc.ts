/**
 * Builds the printable document used by local mode.
 *
 * Local mode never touches the network: the cleaned article is rendered into an
 * extension-controlled page with a print stylesheet, and the browser print
 * dialog produces the PDF. Content stays on the device.
 */

import { FONT_SIZE_PT, MARGIN_MM, type Settings } from './settings';

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function printStylesheet(settings: Settings): string {
  const fontPt = FONT_SIZE_PT[settings.fontSize];
  const marginMm = MARGIN_MM[settings.margin];
  return `
  @page { size: A4; margin: ${marginMm}mm; }
  :root { color-scheme: light; }
  html, body { background: #ffffff; color: #111827; }
  body {
    font-family: Georgia, "Times New Roman", serif;
    font-size: ${fontPt}pt;
    line-height: 1.55;
    margin: 0 auto;
    max-width: 46em;
    padding: ${marginMm}mm;
  }
  h1 { font-size: ${(fontPt * 1.9).toFixed(1)}pt; line-height: 1.2; margin: 0 0 0.4em; }
  h2 { font-size: ${(fontPt * 1.45).toFixed(1)}pt; margin: 1.4em 0 0.4em; }
  h3 { font-size: ${(fontPt * 1.2).toFixed(1)}pt; margin: 1.2em 0 0.35em; }
  p, li { orphans: 3; widows: 3; }
  h1, h2, h3, h4 { break-after: avoid-page; page-break-after: avoid; }
  figure, img, table, pre, blockquote { break-inside: avoid-page; page-break-inside: avoid; }
  img { max-width: 100%; height: auto; }
  pre {
    white-space: pre-wrap;
    word-wrap: break-word;
    background: #f8fafc;
    padding: 0.6em 0.8em;
    border-radius: 4px;
    font-size: ${(fontPt * 0.85).toFixed(1)}pt;
  }
  blockquote {
    margin: 1em 0;
    padding-left: 1em;
    border-left: 3px solid #cbd5e1;
    color: #334155;
  }
  a { color: inherit; text-decoration: underline; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #cbd5e1; padding: 0.35em 0.5em; text-align: left; }
  .cleanpdf-source {
    margin: 0 0 1.6em;
    padding-bottom: 0.8em;
    border-bottom: 1px solid #e2e8f0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: ${(fontPt * 0.75).toFixed(1)}pt;
    color: #64748b;
    word-break: break-all;
  }
  @media print { .cleanpdf-noprint { display: none !important; } }
`;
}

export interface PrintDocInput {
  title: string;
  /** Already-sanitised article HTML. */
  html: string;
  sourceUrl?: string;
  settings: Settings;
}

/**
 * Returns the *body* markup and stylesheet for the print page. The print page
 * assigns these through the DOM rather than document.write, so no inline
 * script is ever introduced and the extension CSP stays at script-src 'self'.
 */
export function buildPrintDocument(input: PrintDocInput): { css: string; body: string } {
  const source = input.sourceUrl
    ? `<div class="cleanpdf-source">${escapeHtml(input.sourceUrl)}</div>`
    : '';
  const body = `
    <article>
      <h1>${escapeHtml(input.title)}</h1>
      ${source}
      ${input.html}
    </article>
  `;
  return { css: printStylesheet(input.settings), body };
}
