import { resolve } from 'node:path';
import { defineConfig } from 'vite';

// Second build pass: the page-extraction script, emitted as a self-contained
// IIFE (no imports, no dynamic loading) so it can be injected with
// chrome.scripting.executeScript({ files: ['inject/extract.js'] }).
// Mozilla Readability is bundled in here, so the shipped extension contains no
// remotely loaded code.
export default defineConfig({
  publicDir: false,
  build: {
    outDir: resolve(__dirname, 'dist/inject'),
    emptyOutDir: true,
    target: 'chrome120',
    sourcemap: false,
    minify: 'esbuild',
    lib: {
      entry: resolve(__dirname, 'src/inject/extract-entry.ts'),
      formats: ['iife'],
      name: '__cleanpdfInject',
      fileName: () => 'extract.js',
    },
  },
});
