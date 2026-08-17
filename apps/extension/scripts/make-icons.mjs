// Generates the extension PNG icons (a rounded slate "document" mark).
// Run with: node scripts/make-icons.mjs
import { deflateSync } from 'node:zlib';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(here, '..', 'public', 'icons');

function crc32(buf) {
  let c = ~0;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

function png(size, paint) {
  const raw = Buffer.alloc(size * (size * 4 + 1));
  let p = 0;
  for (let y = 0; y < size; y++) {
    raw[p++] = 0; // filter: none
    for (let x = 0; x < size; x++) {
      const [r, g, b, a] = paint(x, y, size);
      raw[p++] = r;
      raw[p++] = g;
      raw[p++] = b;
      raw[p++] = a;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

// Rounded square background with a lighter "page" and two text rules.
function paint(x, y, size) {
  const s = size;
  const radius = s * 0.22;
  const inset = s * 0.06;
  const inBox =
    x >= inset && y >= inset && x <= s - inset && y <= s - inset && rounded(x, y, s, inset, radius);
  if (!inBox) return [0, 0, 0, 0];

  const pageL = s * 0.28;
  const pageR = s * 0.72;
  const pageT = s * 0.24;
  const pageB = s * 0.78;
  if (x >= pageL && x <= pageR && y >= pageT && y <= pageB) {
    const rule1 = y > s * 0.38 && y < s * 0.44;
    const rule2 = y > s * 0.52 && y < s * 0.58;
    const inner = x > pageL + s * 0.07 && x < pageR - s * 0.07;
    if ((rule1 || rule2) && inner) return [15, 23, 42, 255];
    return [248, 250, 252, 255];
  }
  return [15, 23, 42, 255];
}

function rounded(x, y, s, inset, r) {
  const lo = inset + r;
  const hi = s - inset - r;
  const cx = x < lo ? lo : x > hi ? hi : x;
  const cy = y < lo ? lo : y > hi ? hi : y;
  return (x - cx) ** 2 + (y - cy) ** 2 <= r * r;
}

mkdirSync(outDir, { recursive: true });
for (const size of [16, 48, 128]) {
  const file = resolve(outDir, `icon${size}.png`);
  writeFileSync(file, png(size, paint));
  process.stdout.write(`wrote ${file}\n`);
}
