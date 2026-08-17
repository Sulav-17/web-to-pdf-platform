import { describe, expect, it } from 'vitest';
import {
  DEFAULT_SETTINGS,
  FONT_SIZE_PT,
  FONT_SIZES,
  MARGIN_MM,
  MARGINS,
  normaliseSettings,
  toFilename,
} from '../src/lib/settings';

describe('settings shape', () => {
  it('exposes exactly three font-size steps', () => {
    expect(FONT_SIZES).toHaveLength(3);
    expect([...FONT_SIZES]).toEqual(['small', 'medium', 'large']);
    for (const size of FONT_SIZES) expect(FONT_SIZE_PT[size]).toBeGreaterThan(0);
  });

  it('maps every margin step to millimetres', () => {
    for (const margin of MARGINS) expect(MARGIN_MM[margin]).toBeGreaterThan(0);
  });

  it('includes images by default', () => {
    expect(DEFAULT_SETTINGS.includeImages).toBe(true);
  });

  it('defaults to local (offline) single-page mode', () => {
    expect(DEFAULT_SETTINGS.singlePageMode).toBe('local');
  });

  it('ships no API base URL, so the build assumes no host', () => {
    expect(DEFAULT_SETTINGS.apiBaseUrl).toBe('');
  });
});

describe('normaliseSettings', () => {
  it('falls back to defaults for missing values', () => {
    expect(normaliseSettings(undefined)).toEqual(DEFAULT_SETTINGS);
  });

  it('rejects unknown enum values', () => {
    const result = normaliseSettings({
      fontSize: 'gigantic' as never,
      margin: 'none' as never,
      filenameSource: 'nope' as never,
    });
    expect(result.fontSize).toBe(DEFAULT_SETTINGS.fontSize);
    expect(result.margin).toBe(DEFAULT_SETTINGS.margin);
    expect(result.filenameSource).toBe(DEFAULT_SETTINGS.filenameSource);
  });

  it('preserves valid values (survives a reload round trip)', () => {
    const stored = {
      fontSize: 'large' as const,
      margin: 'wide' as const,
      includeImages: false,
      filenameSource: 'pack' as const,
      singlePageMode: 'api' as const,
      apiBaseUrl: 'https://api.example.com  ',
    };
    expect(normaliseSettings(stored)).toEqual({
      ...stored,
      apiBaseUrl: 'https://api.example.com',
    });
  });
});

describe('toFilename', () => {
  it('slugifies a title', () => {
    expect(toFilename('My Great Article!')).toBe('My-Great-Article.pdf');
  });

  it('falls back when a title yields nothing usable', () => {
    expect(toFilename('///', 'cleanpdf')).toBe('cleanpdf.pdf');
  });

  it('caps the length', () => {
    expect(toFilename('a'.repeat(200)).length).toBeLessThanOrEqual(84);
  });

  it('strips path separators', () => {
    const name = toFilename('../../etc/passwd');
    expect(name).not.toContain('/');
    expect(name).not.toContain('..');
  });
});
