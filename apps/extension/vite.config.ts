import { resolve } from 'node:path';
import { defineConfig } from 'vite';

// Main build: popup, options and print pages plus the MV3 service worker.
// The injected extraction script is built separately as a classic IIFE by
// vite.inject.config.ts, because chrome.scripting.executeScript({files}) cannot
// load ES modules.
export default defineConfig({
  root: resolve(__dirname, 'src'),
  publicDir: resolve(__dirname, 'public'),
  base: './',
  build: {
    outDir: resolve(__dirname, 'dist'),
    emptyOutDir: true,
    target: 'chrome120',
    sourcemap: false,
    minify: 'esbuild',
    rollupOptions: {
      input: {
        popup: resolve(__dirname, 'src/popup/index.html'),
        options: resolve(__dirname, 'src/options/index.html'),
        print: resolve(__dirname, 'src/print/index.html'),
        'service-worker': resolve(__dirname, 'src/background/service-worker.ts'),
      },
      output: {
        entryFileNames: (chunk) =>
          chunk.name === 'service-worker' ? 'service-worker.js' : 'assets/[name].js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name][extname]',
      },
    },
  },
});
