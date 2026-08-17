/**
 * Extension-controlled printable page (local mode).
 *
 * Reads the one-shot job out of session storage, renders it, and opens the
 * browser print dialog. No network request is made from this page.
 */

import { buildPrintDocument } from '../lib/print-doc';
import { takePrintJob } from '../lib/print-handoff';

async function main(): Promise<void> {
  const content = document.getElementById('content');
  const styleEl = document.getElementById('print-style');
  const printButton = document.getElementById('print');
  if (!content || !styleEl || !printButton) return;

  const key = new URLSearchParams(location.search).get('k') ?? '';
  const job = await takePrintJob(key);

  if (!job) {
    content.className = 'cleanpdf-empty';
    content.textContent =
      'This print job has already been used or has expired. Reopen CleanPDF from the toolbar to create a new one.';
    return;
  }

  const { css, body } = buildPrintDocument({
    title: job.title,
    html: job.html,
    sourceUrl: job.sourceUrl,
    settings: job.settings,
  });

  styleEl.textContent = css;
  content.innerHTML = body;
  // Chrome seeds the "Save as PDF" filename from the document title.
  document.title = job.filename.replace(/\.pdf$/i, '');

  printButton.addEventListener('click', () => window.print());
  // Give layout and images a moment before opening the dialog.
  setTimeout(() => window.print(), 350);
}

void main();
