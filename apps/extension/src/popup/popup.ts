/**
 * Popup controller.
 *
 * Privacy rules enforced here:
 * - Only tabs the user explicitly ticks are ever extracted or transmitted.
 * - Nothing is logged: no URLs, titles, HTML or keys reach the console.
 * - Local mode performs no network request at all.
 */

import {
  ApiError,
  assertConfigured,
  createPack,
  createSingleJob,
  describeError,
  getUsage,
  pollJob,
} from '../lib/api';
import { extractFromTab, extractTabs, isEligibleTab, listEligibleTabs } from '../lib/extract';
import { moveItem } from '../lib/ordering';
import { buildPrintDocument } from '../lib/print-doc';
import { stashPrintJob } from '../lib/print-handoff';
import { getApiKey } from '../lib/secrets';
import { loadSettings, MARGIN_MM, type Settings, toFilename } from '../lib/settings';
import type { ItemAttempt, TabSummary } from '../lib/types';

type RowState = 'idle' | 'working' | 'ok' | 'error';

interface Row {
  tab: TabSummary;
  selected: boolean;
  state: RowState;
  note: string;
}

const el = <T extends HTMLElement>(id: string): T => {
  const found = document.getElementById(id);
  if (!found) throw new Error(`missing element: ${id}`);
  return found as T;
};

const ui = {
  credits: el<HTMLDivElement>('credits'),
  status: el<HTMLElement>('status'),
  list: el<HTMLUListElement>('tab-list'),
  selectAll: el<HTMLInputElement>('select-all'),
  selectedCount: el<HTMLSpanElement>('selected-count'),
  packTitle: el<HTMLInputElement>('pack-title'),
  optToc: el<HTMLInputElement>('opt-toc'),
  optCover: el<HTMLInputElement>('opt-cover'),
  buildPack: el<HTMLButtonElement>('build-pack'),
  savePage: el<HTMLButtonElement>('save-page'),
  openOptions: el<HTMLButtonElement>('open-options'),
  modeHint: el<HTMLSpanElement>('mode-hint'),
};

let rows: Row[] = [];
let settings: Settings;
let busy = false;

// ---------------------------------------------------------------- status ---

function setStatus(
  text: string,
  tone: 'info' | 'ok' | 'warn' | 'err',
  action?: { label: string; run: () => void },
): void {
  ui.status.hidden = false;
  ui.status.className = `status ${tone}`;
  ui.status.textContent = text;
  if (action) {
    const button = document.createElement('button');
    button.className = 'link';
    button.type = 'button';
    button.textContent = action.label;
    button.addEventListener('click', action.run);
    ui.status.append(document.createElement('br'), button);
  }
}

function clearStatus(): void {
  ui.status.hidden = true;
  ui.status.textContent = '';
}

function setBusy(value: boolean): void {
  busy = value;
  ui.buildPack.disabled = value;
  ui.savePage.disabled = value;
}

// ------------------------------------------------------------------ rows ---

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function selectedRows(): Row[] {
  return rows.filter((row) => row.selected);
}

function renderRows(): void {
  ui.list.replaceChildren();

  if (rows.length === 0) {
    const empty = document.createElement('li');
    empty.className = 'empty';
    empty.textContent = 'No eligible tabs in this window.';
    ui.list.append(empty);
  }

  for (const [index, row] of rows.entries()) {
    const item = document.createElement('li');
    item.className = 'tab-row';
    item.draggable = true;
    item.dataset.index = String(index);

    const grip = document.createElement('span');
    grip.className = 'grip';
    grip.textContent = '⠿';
    grip.title = 'Drag to reorder';

    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = row.selected;
    check.addEventListener('change', () => {
      row.selected = check.checked;
      syncSelectionUi();
    });

    const text = document.createElement('span');
    text.className = 'tab-text';
    const title = document.createElement('span');
    title.className = 'tab-title';
    title.textContent = row.tab.title; // textContent: never interpreted as HTML
    const host = document.createElement('span');
    host.className = 'tab-host';
    host.textContent = hostOf(row.tab.url);
    text.append(title, host);

    const state = document.createElement('span');
    state.className = `tab-state ${row.state === 'ok' ? 'ok' : row.state === 'error' ? 'err' : ''}`;
    state.textContent =
      row.state === 'working' ? '…' : row.state === 'ok' ? '✓' : row.state === 'error' ? '!' : '';
    if (row.note) state.title = row.note;

    item.append(grip, check, text, state);
    attachDragHandlers(item, index);
    ui.list.append(item);
  }

  syncSelectionUi();
}

function syncSelectionUi(): void {
  const count = selectedRows().length;
  ui.selectedCount.textContent = count === 0 ? '' : `${count} selected`;
  ui.selectAll.checked = count > 0 && count === rows.length;
  ui.selectAll.indeterminate = count > 0 && count < rows.length;
}

// --------------------------------------------------------------- drag/drop --

let dragFrom: number | null = null;

function attachDragHandlers(item: HTMLLIElement, index: number): void {
  item.addEventListener('dragstart', (event) => {
    dragFrom = index;
    item.classList.add('dragging');
    event.dataTransfer?.setData('text/plain', String(index));
    if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
  });

  item.addEventListener('dragend', () => {
    dragFrom = null;
    item.classList.remove('dragging');
    for (const node of ui.list.children) node.classList.remove('drop-target');
  });

  item.addEventListener('dragover', (event) => {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    item.classList.add('drop-target');
  });

  item.addEventListener('dragleave', () => item.classList.remove('drop-target'));

  item.addEventListener('drop', (event) => {
    event.preventDefault();
    item.classList.remove('drop-target');
    const from = dragFrom ?? Number.parseInt(event.dataTransfer?.getData('text/plain') ?? '', 10);
    if (Number.isNaN(from)) return;
    rows = moveItem(rows, from, index);
    renderRows();
  });
}

// --------------------------------------------------------------- credits ---

async function refreshCredits(): Promise<void> {
  const key = await getApiKey();
  if (!settings.apiBaseUrl || !key) {
    ui.credits.textContent = 'Local mode';
    ui.modeHint.textContent = 'Offline · no account needed';
    return;
  }
  try {
    const usage = await getUsage(assertConfigured(settings.apiBaseUrl, key));
    ui.credits.textContent = `${usage.balance} credits`;
    ui.credits.classList.toggle('low', usage.balance < 5);
    ui.modeHint.textContent = usage.plan_id ? `Plan: ${usage.plan_id}` : '';
  } catch (error) {
    ui.credits.textContent = 'Credits unavailable';
    ui.modeHint.textContent = error instanceof ApiError ? describeError(error) : '';
  }
}

// ------------------------------------------------------------ local print --

async function openPrintPage(
  title: string,
  html: string,
  sourceUrl: string,
  filename: string,
): Promise<void> {
  // Validates the document builds before handing it over.
  buildPrintDocument({ title, html, sourceUrl, settings });
  const key = await stashPrintJob({ title, html, sourceUrl, filename, settings });
  await chrome.tabs.create({ url: chrome.runtime.getURL(`print/index.html?k=${key}`) });
}

async function saveCurrentPage(): Promise<void> {
  setBusy(true);
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !isEligibleTab(tab.url)) {
      setStatus('This page cannot be captured by extensions.', 'err');
      return;
    }

    setStatus('Cleaning this page…', 'info');
    const outcome = await extractFromTab(tab.id, settings.includeImages);
    if (!outcome.ok) {
      setStatus(outcome.reason, 'err');
      return;
    }

    const filename = toFilename(outcome.title);

    if (settings.singlePageMode === 'api') {
      const key = await getApiKey();
      const config = assertConfigured(settings.apiBaseUrl, key);
      setStatus('Rendering with the CleanPDF API…', 'info');
      const created = await createSingleJob(config, {
        html: wrapArticle(outcome.title, outcome.html),
        marginMm: MARGIN_MM[settings.margin],
      });
      const job = await pollJob(config, created.id, {
        onTick: (current) => setStatus(`Rendering… (${current.status})`, 'info'),
      });
      if (job.status !== 'completed' || !job.download_url) {
        setStatus(`Render failed: ${job.failure_reason ?? 'unknown error'}`, 'err');
        return;
      }
      const url = job.download_url;
      setStatus('Your PDF is ready.', 'ok', {
        label: 'Open PDF',
        run: () => void chrome.tabs.create({ url }),
      });
      await refreshCredits();
      return;
    }

    await openPrintPage(outcome.title, outcome.html, tab.url ?? '', filename);
    setStatus('Opened the print view — choose “Save as PDF”.', 'ok');
  } catch (error) {
    setStatus(describeError(error), 'err');
  } finally {
    setBusy(false);
  }
}

/** Minimal document wrapper for API single-page renders. */
function wrapArticle(title: string, html: string): string {
  const { css, body } = buildPrintDocument({ title, html, settings });
  return `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body>${body}</body></html>`;
}

// -------------------------------------------------------------- pack flow --

function defaultPackTitle(): string {
  const today = new Date().toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
  return `Reading pack — ${today}`;
}

async function buildPack(): Promise<void> {
  const chosen = selectedRows();
  if (chosen.length === 0) {
    setStatus('Tick at least one tab to include in the pack.', 'warn');
    return;
  }

  const key = await getApiKey();
  let config: ReturnType<typeof assertConfigured>;
  try {
    config = assertConfigured(settings.apiBaseUrl, key);
  } catch (error) {
    setStatus(describeError(error), 'warn', {
      label: 'Open Options',
      run: () => void chrome.runtime.openOptionsPage(),
    });
    return;
  }

  setBusy(true);
  try {
    for (const row of chosen) {
      row.state = 'idle';
      row.note = '';
    }

    setStatus(`Extracting 0 / ${chosen.length} tabs…`, 'info');
    const attempts = await extractTabs(
      chosen.map((row) => row.tab),
      settings.includeImages,
      (done, total) => {
        if (done < total) setStatus(`Extracting ${done + 1} / ${total} tabs…`, 'info');
      },
    );
    applyAttempts(attempts);

    const usable = attempts.filter((attempt) => attempt.outcome.ok);
    const broken = attempts.filter((attempt) => !attempt.outcome.ok);

    if (usable.length === 0) {
      setStatus(
        `No tabs could be extracted.\n${broken[0]?.outcome.ok === false ? broken[0].outcome.reason : ''}`,
        'err',
      );
      return;
    }

    if (broken.length > 0) {
      // Never silently drop pages: make the user choose explicitly.
      const names = broken.map((attempt) => `• ${attempt.tab.title}`).join('\n');
      setStatus(
        `${broken.length} of ${attempts.length} tabs could not be extracted:\n${names}\n\n` +
          `${broken[0]?.outcome.ok === false ? broken[0].outcome.reason : ''}`,
        'warn',
        {
          label: `Build pack with the other ${usable.length}`,
          run: () => void submitPack(config, usable),
        },
      );
      return;
    }

    await submitPack(config, usable);
  } catch (error) {
    setStatus(describeError(error), 'err');
  } finally {
    setBusy(false);
  }
}

function applyAttempts(attempts: ItemAttempt[]): void {
  for (const attempt of attempts) {
    const row = rows.find((candidate) => candidate.tab.id === attempt.tab.id);
    if (!row) continue;
    row.state = attempt.outcome.ok ? 'ok' : 'error';
    row.note = attempt.outcome.ok ? '' : attempt.outcome.reason;
  }
  renderRows();
}

async function submitPack(
  config: ReturnType<typeof assertConfigured>,
  usable: ItemAttempt[],
): Promise<void> {
  setBusy(true);
  try {
    const packTitle = ui.packTitle.value.trim() || defaultPackTitle();
    // Order is exactly the popup row order, filtered to successful extractions.
    const items = usable.map((attempt) => {
      const outcome = attempt.outcome;
      if (!outcome.ok) throw new Error('unreachable');
      return { html: outcome.html, title: outcome.title };
    });

    setStatus(`Sending ${items.length} articles…`, 'info');
    const created = await createPack(config, {
      items,
      packTitle,
      toc: ui.optToc.checked,
      titlePage: ui.optCover.checked,
      marginMm: MARGIN_MM[settings.margin],
    });

    const job = await pollJob(config, created.id, {
      onTick: (current, elapsed) =>
        setStatus(
          `Building your pack… (${current.status}, ${Math.round(elapsed / 1000)}s)`,
          'info',
        ),
    });

    if (job.status !== 'completed' || !job.download_url) {
      setStatus(`Pack failed: ${job.failure_reason ?? 'unknown error'}`, 'err');
      await refreshCredits();
      return;
    }

    const url = job.download_url;
    const filename = toFilename(
      settings.filenameSource === 'pack' ? packTitle : (items[0]?.title ?? packTitle),
    );
    setStatus(`Your pack is ready (${items.length} articles) — ${filename}`, 'ok', {
      label: 'Open PDF',
      run: () => void chrome.tabs.create({ url }),
    });
    await refreshCredits();
  } catch (error) {
    setStatus(describeError(error), 'err');
  } finally {
    setBusy(false);
  }
}

// ------------------------------------------------------------------ init ---

async function init(): Promise<void> {
  settings = await loadSettings();
  ui.packTitle.value = defaultPackTitle();

  const tabs = await listEligibleTabs();
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  rows = tabs.map((tab) => ({
    tab,
    // Pre-select the active tab: it is the one activeTab certainly grants.
    selected: tab.id === active?.id,
    state: 'idle',
    note: '',
  }));
  renderRows();

  ui.selectAll.addEventListener('change', () => {
    for (const row of rows) row.selected = ui.selectAll.checked;
    renderRows();
  });
  ui.buildPack.addEventListener('click', () => {
    if (!busy) void buildPack();
  });
  ui.savePage.addEventListener('click', () => {
    if (!busy) void saveCurrentPage();
  });
  ui.openOptions.addEventListener('click', () => void chrome.runtime.openOptionsPage());

  clearStatus();
  await refreshCredits();
}

void init();
