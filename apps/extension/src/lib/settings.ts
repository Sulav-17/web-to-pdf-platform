/** User options, persisted with chrome.storage.sync. */

export const FONT_SIZES = ['small', 'medium', 'large'] as const;
export type FontSize = (typeof FONT_SIZES)[number];

export const MARGINS = ['narrow', 'normal', 'wide'] as const;
export type MarginName = (typeof MARGINS)[number];

export type FilenameSource = 'page' | 'pack';

/**
 * How the secondary "save this page" action runs.
 * `local` is the default free mode: fully offline, no account, no network call.
 * `api` opts into the higher-fidelity server render and costs a credit.
 */
export type SinglePageMode = 'local' | 'api';

export interface Settings {
  /** Exactly three steps, per spec. */
  fontSize: FontSize;
  margin: MarginName;
  includeImages: boolean;
  filenameSource: FilenameSource;
  singlePageMode: SinglePageMode;
  /**
   * Base URL of the CleanPDF API. Empty by default so the shipped build makes
   * no assumption about where the API lives (and never assumes localhost).
   * API mode stays disabled until the user provides one.
   */
  apiBaseUrl: string;
}

export const DEFAULT_SETTINGS: Settings = {
  fontSize: 'medium',
  margin: 'normal',
  includeImages: true,
  filenameSource: 'page',
  singlePageMode: 'local',
  apiBaseUrl: '',
};

/** Printed body font size for each step. */
export const FONT_SIZE_PT: Record<FontSize, number> = {
  small: 10,
  medium: 12,
  large: 14,
};

/** Page margin in millimetres for each step. */
export const MARGIN_MM: Record<MarginName, number> = {
  narrow: 8,
  normal: 12,
  wide: 20,
};

function coerce(raw: Partial<Settings> | undefined): Settings {
  const value = raw ?? {};
  return {
    fontSize: FONT_SIZES.includes(value.fontSize as FontSize)
      ? (value.fontSize as FontSize)
      : DEFAULT_SETTINGS.fontSize,
    margin: MARGINS.includes(value.margin as MarginName)
      ? (value.margin as MarginName)
      : DEFAULT_SETTINGS.margin,
    includeImages:
      typeof value.includeImages === 'boolean'
        ? value.includeImages
        : DEFAULT_SETTINGS.includeImages,
    filenameSource:
      value.filenameSource === 'pack' || value.filenameSource === 'page'
        ? value.filenameSource
        : DEFAULT_SETTINGS.filenameSource,
    singlePageMode:
      value.singlePageMode === 'api' || value.singlePageMode === 'local'
        ? value.singlePageMode
        : DEFAULT_SETTINGS.singlePageMode,
    apiBaseUrl:
      typeof value.apiBaseUrl === 'string' ? value.apiBaseUrl.trim() : DEFAULT_SETTINGS.apiBaseUrl,
  };
}

const KEY = 'settings';

export async function loadSettings(): Promise<Settings> {
  const stored = await chrome.storage.sync.get(KEY);
  return coerce(stored[KEY] as Partial<Settings> | undefined);
}

export async function saveSettings(patch: Partial<Settings>): Promise<Settings> {
  const next = coerce({ ...(await loadSettings()), ...patch });
  await chrome.storage.sync.set({ [KEY]: next });
  return next;
}

/** Exported for tests: normalises any stored shape into valid settings. */
export const normaliseSettings = coerce;

/** Turn a title into a safe download filename stem. */
export function toFilename(title: string, fallback = 'cleanpdf'): string {
  const stem = title
    .normalize('NFKD')
    .replace(/[^\w\s.-]+/g, '')
    .replace(/\s+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^[-.]+|[-.]+$/g, '')
    .slice(0, 80);
  return `${stem || fallback}.pdf`;
}
