import { describe, expect, it } from 'vitest';
import { cleanTitle, sanitizeHtml } from '../src/lib/sanitize';

const withImages = { includeImages: true };
const withoutImages = { includeImages: false };

describe('sanitizeHtml', () => {
  it('keeps ordinary article markup', () => {
    const html = sanitizeHtml('<p>Hello <strong>world</strong></p>', withImages);
    expect(html).toContain('<p>Hello <strong>world</strong></p>');
  });

  it('removes script elements entirely', () => {
    const html = sanitizeHtml('<p>ok</p><script>alert(1)</script>', withImages);
    expect(html).not.toContain('script');
    expect(html).toContain('ok');
  });

  it('removes inline event handlers', () => {
    const html = sanitizeHtml('<div onclick="steal()">text</div>', withImages);
    expect(html).not.toContain('onclick');
    expect(html).toContain('text');
  });

  it.each([
    ['onerror', '<img src="x" onerror="hack()">'],
    ['onload', '<div onload="hack()">a</div>'],
    ['onmouseover', '<span onmouseover="hack()">a</span>'],
  ])('strips %s handlers', (attr, markup) => {
    expect(sanitizeHtml(markup, withImages)).not.toContain(attr);
  });

  it('drops javascript: and data: URLs', () => {
    const html = sanitizeHtml(
      '<a href="javascript:alert(1)">x</a><a href="data:text/html,evil">y</a>',
      withImages,
    );
    expect(html).not.toContain('javascript:');
    expect(html).not.toContain('data:text/html');
  });

  it('keeps safe http and relative links', () => {
    const html = sanitizeHtml('<a href="https://example.com/a">x</a>', withImages);
    expect(html).toContain('https://example.com/a');
  });

  it.each(['iframe', 'object', 'embed', 'form', 'input', 'style', 'noscript'])(
    'removes unsafe <%s>',
    (tag) => {
      const html = sanitizeHtml(`<p>keep</p><${tag}></${tag}>`, withImages);
      expect(html).not.toContain(`<${tag}`);
      expect(html).toContain('keep');
    },
  );

  it('removes page chrome like nav and footer', () => {
    const html = sanitizeHtml('<nav>menu</nav><p>body</p><footer>legal</footer>', withImages);
    expect(html).not.toContain('menu');
    expect(html).not.toContain('legal');
    expect(html).toContain('body');
  });

  it('strips inline styles', () => {
    const html = sanitizeHtml('<p style="display:none">hidden</p>', withImages);
    expect(html).not.toContain('style=');
    expect(html).toContain('hidden');
  });

  it('keeps images when the option is on', () => {
    const html = sanitizeHtml('<img src="https://example.com/a.png">', withImages);
    expect(html).toContain('img');
  });

  it('removes images when the option is off', () => {
    const html = sanitizeHtml('<p>text</p><img src="https://example.com/a.png">', withoutImages);
    expect(html).not.toContain('<img');
    expect(html).toContain('text');
  });

  it('removes figures when images are off', () => {
    const html = sanitizeHtml(
      '<figure><img src="a.png"><figcaption>c</figcaption></figure><p>t</p>',
      withoutImages,
    );
    expect(html).not.toContain('figure');
    expect(html).toContain('t');
  });
});

describe('cleanTitle', () => {
  it('collapses whitespace and trims', () => {
    expect(cleanTitle('  A   long \n title ')).toBe('A long title');
  });

  it('caps very long titles', () => {
    expect(cleanTitle('x'.repeat(500))).toHaveLength(300);
  });
});
