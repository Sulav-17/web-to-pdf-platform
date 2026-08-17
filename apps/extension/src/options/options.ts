/**
 * Options page.
 *
 * The API key input is write-only: the stored key is never read back into the
 * DOM. Once saved, the field is cleared and only a masked hint is shown.
 */

import { assertConfigured, describeError, getUsage } from '../lib/api';
import { clearApiKey, getApiKey, maskApiKey, setApiKey } from '../lib/secrets';
import {
  type FilenameSource,
  type FontSize,
  loadSettings,
  type MarginName,
  type SinglePageMode,
  saveSettings,
} from '../lib/settings';

const el = <T extends HTMLElement>(id: string): T => {
  const found = document.getElementById(id);
  if (!found) throw new Error(`missing element: ${id}`);
  return found as T;
};

const ui = {
  fontSize: el<HTMLSelectElement>('font-size'),
  margin: el<HTMLSelectElement>('margin'),
  includeImages: el<HTMLInputElement>('include-images'),
  filenameSource: el<HTMLSelectElement>('filename-source'),
  singlePageMode: el<HTMLSelectElement>('single-page-mode'),
  apiBaseUrl: el<HTMLInputElement>('api-base-url'),
  apiKey: el<HTMLInputElement>('api-key'),
  saveKey: el<HTMLButtonElement>('save-key'),
  testKey: el<HTMLButtonElement>('test-key'),
  clearKey: el<HTMLButtonElement>('clear-key'),
  keyState: el<HTMLParagraphElement>('key-state'),
  status: el<HTMLParagraphElement>('status'),
};

function setStatus(text: string, tone: 'ok' | 'err'): void {
  ui.status.hidden = false;
  ui.status.className = `status ${tone}`;
  ui.status.textContent = text;
}

async function refreshKeyState(): Promise<void> {
  const key = await getApiKey();
  // Only a mask is ever rendered — never the key itself.
  ui.keyState.textContent = key
    ? `A key is stored (${maskApiKey(key)}). It is kept in this browser's local extension storage and never synced.`
    : 'No key stored. Reading packs stay disabled until you add one.';
}

async function init(): Promise<void> {
  const settings = await loadSettings();
  ui.fontSize.value = settings.fontSize;
  ui.margin.value = settings.margin;
  ui.includeImages.checked = settings.includeImages;
  ui.filenameSource.value = settings.filenameSource;
  ui.singlePageMode.value = settings.singlePageMode;
  ui.apiBaseUrl.value = settings.apiBaseUrl;
  await refreshKeyState();

  const persist = async (patch: Parameters<typeof saveSettings>[0]): Promise<void> => {
    await saveSettings(patch);
    setStatus('Saved.', 'ok');
  };

  ui.fontSize.addEventListener(
    'change',
    () => void persist({ fontSize: ui.fontSize.value as FontSize }),
  );
  ui.margin.addEventListener(
    'change',
    () => void persist({ margin: ui.margin.value as MarginName }),
  );
  ui.includeImages.addEventListener(
    'change',
    () => void persist({ includeImages: ui.includeImages.checked }),
  );
  ui.filenameSource.addEventListener(
    'change',
    () => void persist({ filenameSource: ui.filenameSource.value as FilenameSource }),
  );
  ui.singlePageMode.addEventListener(
    'change',
    () => void persist({ singlePageMode: ui.singlePageMode.value as SinglePageMode }),
  );
  ui.apiBaseUrl.addEventListener(
    'change',
    () => void persist({ apiBaseUrl: ui.apiBaseUrl.value.trim() }),
  );

  ui.saveKey.addEventListener('click', async () => {
    const raw = ui.apiKey.value.trim();
    if (!raw) {
      setStatus('Paste a key first.', 'err');
      return;
    }
    try {
      await setApiKey(raw);
      ui.apiKey.value = ''; // never keep the secret in the DOM
      await refreshKeyState();
      setStatus('API key saved.', 'ok');
    } catch (error) {
      setStatus(describeError(error), 'err');
    }
  });

  ui.clearKey.addEventListener('click', async () => {
    await clearApiKey();
    ui.apiKey.value = '';
    await refreshKeyState();
    setStatus('API key removed.', 'ok');
  });

  ui.testKey.addEventListener('click', async () => {
    try {
      const config = assertConfigured(ui.apiBaseUrl.value.trim(), await getApiKey());
      const usage = await getUsage(config);
      setStatus(`Connected. ${usage.balance} credits remaining.`, 'ok');
    } catch (error) {
      setStatus(describeError(error), 'err');
    }
  });
}

void init();
