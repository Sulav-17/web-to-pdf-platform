/**
 * Production-build audit.
 *
 * Fails the build if dist/ contains secrets, test keys, localhost assumptions,
 * remote executable code, or permissions beyond the four approved ones.
 * Run with: node scripts/audit-build.mjs
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, extname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const dist = resolve(here, '..', 'dist');
const APPROVED = ['activeTab', 'storage', 'scripting', 'tabs'];

const failures = [];
const fail = (message) => failures.push(message);

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

let files;
try {
  files = walk(dist);
} catch {
  console.error('audit: dist/ not found — run `npm run build` first.');
  process.exit(1);
}

// ---------------------------------------------------------------- manifest --
const manifest = JSON.parse(readFileSync(join(dist, 'manifest.json'), 'utf8'));

if (manifest.manifest_version !== 3) fail('manifest is not Manifest V3');

const permissions = manifest.permissions ?? [];
const extra = permissions.filter((p) => !APPROVED.includes(p));
const missing = APPROVED.filter((p) => !permissions.includes(p));
if (extra.length) fail(`manifest requests unapproved permissions: ${extra.join(', ')}`);
if (missing.length) fail(`manifest is missing approved permissions: ${missing.join(', ')}`);

for (const field of ['host_permissions', 'optional_permissions', 'optional_host_permissions']) {
  if (manifest[field]) fail(`manifest must not declare ${field}`);
}
if (manifest.externally_connectable) fail('manifest must not declare externally_connectable');

// -------------------------------------------------------------- code scan --
const TEXT_EXT = new Set(['.js', '.html', '.css', '.json', '.map']);
const textFiles = files.filter((file) => TEXT_EXT.has(extname(file)));

const SECRET_PATTERNS = [
  [/sk_live_[A-Za-z0-9_-]{8,}/, 'live API key'],
  [/sk_test_[A-Za-z0-9_-]{8,}/, 'test API key'],
  [/\bBearer\s+[A-Za-z0-9_-]{20,}/, 'hard-coded bearer token'],
  [/AKIA[0-9A-Z]{16}/, 'AWS access key id'],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, 'private key'],
  [/\bwhsec_[A-Za-z0-9]{8,}/, 'Stripe webhook secret'],
];

// Remote code loading: anything that would pull executable content at runtime.
const REMOTE_PATTERNS = [
  [/\bimportScripts\s*\(/, 'importScripts()'],
  [/document\.write\s*\(/, 'document.write()'],
  [/\bnew\s+Function\s*\(/, 'new Function()'],
  [/<script[^>]+src=["']https?:/i, 'remote <script src>'],
  [/@import\s+url\(["']?https?:/i, 'remote CSS @import'],
];

for (const file of textFiles) {
  const rel = relative(dist, file).replace(/\\/g, '/');
  const content = readFileSync(file, 'utf8');

  for (const [pattern, label] of SECRET_PATTERNS) {
    if (pattern.test(content)) fail(`${rel}: contains a ${label}`);
  }
  for (const [pattern, label] of REMOTE_PATTERNS) {
    if (pattern.test(content)) fail(`${rel}: uses ${label}`);
  }
  // eval( but not the harmless ".eval" property accesses bundlers emit.
  if (/(^|[^.\w])eval\s*\(/.test(content)) fail(`${rel}: uses eval()`);

  // Localhost must never be baked into the shipped build.
  if (/localhost|127\.0\.0\.1|0\.0\.0\.0/.test(content)) {
    fail(`${rel}: contains a localhost assumption`);
  }
}

// The API base URL must ship empty so no host is assumed.
const settingsBundle = textFiles.find((file) => /assets[/\\]settings.*\.js$/.test(file));
if (settingsBundle) {
  const content = readFileSync(settingsBundle, 'utf8');
  if (/apiBaseUrl\s*:\s*["'][^"']+["']/.test(content)) {
    fail('settings bundle ships a non-empty default apiBaseUrl');
  }
}

// ------------------------------------------------------------------ report --
if (failures.length) {
  console.error('Build audit FAILED:');
  for (const message of failures) console.error(`  ✗ ${message}`);
  process.exit(1);
}

console.log(`Build audit passed: ${files.length} files, permissions [${permissions.join(', ')}]`);
