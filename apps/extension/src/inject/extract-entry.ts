/**
 * Injected into a tab with chrome.scripting.executeScript({ files: [...] }).
 *
 * Runs in the isolated world. It defines a single global function and returns
 * nothing itself; the popup then calls that global with the user's options via
 * a second executeScript({ func, args }) call. Nothing is transmitted from
 * here — the caller decides what happens with the result.
 *
 * Mozilla Readability is bundled into this file at build time, so no code is
 * fetched at runtime.
 */

import { isProbablyReaderable, Readability } from '@mozilla/readability';
import { cleanTitle, sanitizeElement } from '../lib/sanitize';
import type { ExtractOutcome } from '../lib/types';

declare global {
  interface Window {
    __cleanpdfExtract?: (options: { includeImages: boolean }) => ExtractOutcome;
  }
}

function extract(options: { includeImages: boolean }): ExtractOutcome {
  try {
    // Readability mutates the document it is given, so always hand it a clone.
    const clone = document.cloneNode(true) as Document;
    const parsed = new Readability(clone, { keepClasses: false }).parse();

    const rawHtml = parsed?.content ?? '';
    const title = cleanTitle(parsed?.title || document.title || 'Untitled');

    if (!rawHtml.trim()) {
      return {
        ok: false,
        reason: isProbablyReaderable(document)
          ? 'Readability found no article content on this page.'
          : 'This page has no extractable article (it may be an app, feed or paywall).',
      };
    }

    // Sanitise inside the page, before anything crosses the boundary.
    const holder = document.implementation.createHTMLDocument('').createElement('div');
    holder.innerHTML = rawHtml;
    sanitizeElement(holder, { includeImages: options.includeImages });

    const html = holder.innerHTML.trim();
    if (!html) {
      return { ok: false, reason: 'Article content was empty after sanitising.' };
    }
    return { ok: true, title, html };
  } catch (error) {
    return {
      ok: false,
      reason: error instanceof Error ? error.message : 'Extraction failed on this page.',
    };
  }
}

window.__cleanpdfExtract = extract;
