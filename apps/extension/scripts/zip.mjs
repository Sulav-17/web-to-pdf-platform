/**
 * Packages dist/ into a distributable ZIP for the Chrome Web Store.
 * Uses a minimal stored+deflated ZIP writer so no extra dependency is needed.
 * Run with: node scripts/zip.mjs
 */
import { deflateRawSync } from 'node:zlib';
import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const dist = resolve(here, '..', 'dist');
const outDir = resolve(here, '..', 'release');
const manifest = JSON.parse(readFileSync(join(dist, 'manifest.json'), 'utf8'));
const outFile = resolve(outDir, `cleanpdf-extension-v${manifest.version}.zip`);

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

function crc32(buf) {
  let c = ~0;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

// Fixed timestamp keeps the archive byte-reproducible.
const DOS_TIME = 0;
const DOS_DATE = 0x2821; // 2020-01-01

const files = walk(dist).sort();
const locals = [];
const central = [];
let offset = 0;

for (const file of files) {
  const name = relative(dist, file).replace(/\\/g, '/');
  const raw = readFileSync(file);
  const deflated = deflateRawSync(raw, { level: 9 });
  const useDeflate = deflated.length < raw.length;
  const data = useDeflate ? deflated : raw;
  const method = useDeflate ? 8 : 0;
  const nameBuf = Buffer.from(name, 'utf8');
  const crc = crc32(raw);

  const local = Buffer.alloc(30);
  local.writeUInt32LE(0x04034b50, 0);
  local.writeUInt16LE(20, 4); // version needed
  local.writeUInt16LE(0, 6); // flags
  local.writeUInt16LE(method, 8);
  local.writeUInt16LE(DOS_TIME, 10);
  local.writeUInt16LE(DOS_DATE, 12);
  local.writeUInt32LE(crc, 14);
  local.writeUInt32LE(data.length, 18);
  local.writeUInt32LE(raw.length, 22);
  local.writeUInt16LE(nameBuf.length, 26);
  local.writeUInt16LE(0, 28);
  locals.push(local, nameBuf, data);

  const dirEntry = Buffer.alloc(46);
  dirEntry.writeUInt32LE(0x02014b50, 0);
  dirEntry.writeUInt16LE(20, 4); // version made by
  dirEntry.writeUInt16LE(20, 6); // version needed
  dirEntry.writeUInt16LE(0, 8);
  dirEntry.writeUInt16LE(method, 10);
  dirEntry.writeUInt16LE(DOS_TIME, 12);
  dirEntry.writeUInt16LE(DOS_DATE, 14);
  dirEntry.writeUInt32LE(crc, 16);
  dirEntry.writeUInt32LE(data.length, 20);
  dirEntry.writeUInt32LE(raw.length, 24);
  dirEntry.writeUInt16LE(nameBuf.length, 28);
  dirEntry.writeUInt32LE(0, 38); // external attrs
  dirEntry.writeUInt32LE(offset, 42);
  central.push(dirEntry, nameBuf);

  offset += local.length + nameBuf.length + data.length;
}

const centralBuf = Buffer.concat(central);
const end = Buffer.alloc(22);
end.writeUInt32LE(0x06054b50, 0);
end.writeUInt16LE(files.length, 8);
end.writeUInt16LE(files.length, 10);
end.writeUInt32LE(centralBuf.length, 12);
end.writeUInt32LE(offset, 16);

mkdirSync(outDir, { recursive: true });
writeFileSync(outFile, Buffer.concat([...locals, centralBuf, end]));
console.log(`Wrote ${outFile} (${files.length} files)`);
