import { describe, expect, it } from 'vitest';
import { buildPrintDocument, escapeHtml, printStylesheet } from '../src/lib/print-doc';
import { DEFAULT_SETTINGS, FONT_SIZE_PT, MARGIN_MM, type Settings } from '../src/lib/settings';

const settings: Settings = { ...DEFAULT_SETTINGS };

describe('escapeHtml', () => {
  it('escapes every dangerous character', () => {
    expect(escapeHtml('<script>"x"&\'y\'</script>')).toBe(
      '&lt;script&gt;&quot;x&quot;&amp;&#39;y&#39;&lt;/script&gt;',
    );
  });
});

describe('printStylesheet', () => {
  it('applies the selected font-size step', () => {
    for (const size of ['small', 'medium', 'large'] as const) {
      const css = printStylesheet({ ...settings, fontSize: size });
      expect(css).toContain(`font-size: ${FONT_SIZE_PT[size]}pt`);
    }
  });

  it('applies the selected margin step to @page', () => {
    for (const margin of ['narrow', 'normal', 'wide'] as const) {
      const css = printStylesheet({ ...settings, margin });
      expect(css).toContain(`@page { size: A4; margin: ${MARGIN_MM[margin]}mm; }`);
    }
  });

  it('hides extension chrome when printing', () => {
    expect(printStylesheet(settings)).toContain('.cleanpdf-noprint');
  });

  it('avoids splitting headings and figures across pages', () => {
    const css = printStylesheet(settings);
    expect(css).toContain('page-break-after: avoid');
    expect(css).toContain('page-break-inside: avoid');
  });
});

describe('buildPrintDocument', () => {
  it('escapes the title but keeps sanitised body HTML', () => {
    const { body } = buildPrintDocument({
      title: '<script>alert(1)</script>',
      html: '<p>Article body</p>',
      settings,
    });
    expect(body).not.toContain('<script>');
    expect(body).toContain('&lt;script&gt;');
    expect(body).toContain('<p>Article body</p>');
  });

  it('escapes the source URL', () => {
    const { body } = buildPrintDocument({
      title: 'T',
      html: '<p>x</p>',
      sourceUrl: 'https://e.test/?a="><script>',
      settings,
    });
    expect(body).not.toContain('"><script>');
  });

  it('omits the source line when no URL is supplied', () => {
    const { body } = buildPrintDocument({ title: 'T', html: '<p>x</p>', settings });
    expect(body).not.toContain('cleanpdf-source');
  });
});
