import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const manifest = JSON.parse(
  readFileSync(resolve(__dirname, '../public/manifest.json'), 'utf8'),
) as Record<string, unknown>;

const APPROVED = ['activeTab', 'storage', 'scripting', 'tabs'];

describe('manifest', () => {
  it('is Manifest V3', () => {
    expect(manifest.manifest_version).toBe(3);
  });

  it('requests exactly the four approved permissions', () => {
    expect([...(manifest.permissions as string[])].sort()).toEqual([...APPROVED].sort());
  });

  it('requests no host permissions', () => {
    expect(manifest.host_permissions).toBeUndefined();
  });

  it('requests no optional permissions', () => {
    expect(manifest.optional_permissions).toBeUndefined();
    expect(manifest.optional_host_permissions).toBeUndefined();
  });

  it.each(['offscreen', 'downloads', 'history', 'cookies', 'webRequest', '<all_urls>'])(
    'does not request %s',
    (permission) => {
      expect(manifest.permissions as string[]).not.toContain(permission);
    },
  );

  it('locks extension pages to self-hosted scripts', () => {
    const csp = manifest.content_security_policy as { extension_pages: string };
    expect(csp.extension_pages).toContain("script-src 'self'");
  });

  it('declares no externally connectable or remote code surface', () => {
    expect(manifest.externally_connectable).toBeUndefined();
    expect(manifest.content_scripts).toBeUndefined();
    expect(manifest.web_accessible_resources).toEqual([]);
  });

  it('opens with the store-listing description', () => {
    expect(manifest.description).toBe(
      'Turn your open tabs into one clean, bookmarked PDF — perfect for weekly readings.',
    );
  });
});
