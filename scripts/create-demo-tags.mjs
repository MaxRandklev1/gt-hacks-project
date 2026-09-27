import QRCode from 'qrcode';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const root = fileURLToPath(new URL('..', import.meta.url));
// These ten destinations are printed on physical tags. Never repoint or reuse them.
const canonicalOrigin = 'https://gt-hacks-thread-2026.firebaseapp.com';
if (process.argv.length > 3 || (process.argv[2] && ![canonicalOrigin, `${canonicalOrigin}/`].includes(process.argv[2]))) {
  throw new Error(`Printed THREAD tags are permanently pinned to ${canonicalOrigin}. Run pnpm qr:demo without an alternate origin.`);
}
const manifest = JSON.parse(await readFile(resolve(root, 'docs/tags/printed-threads.json'), 'utf8'));
if (manifest.schema !== 1 || manifest.origin !== canonicalOrigin || manifest.frozenOn !== '2026-09-27' || manifest.garments?.length !== 10) {
  throw new Error('The printed-tag manifest must retain its original schema, origin, freeze date and ten garments.');
}
const garments = manifest.garments;
const directories = ['docs/tags', 'web/public/tags'];
const svgs = new Map();
const sha256 = value => createHash('sha256').update(value).digest('hex');
// Validate every destination and both existing copies before writing any assets.
for (const [index, garment] of garments.entries()) {
  const number = index + 1;
  const id = `thread-${number}`;
  if (garment.id !== id || garment.label !== `THREAD ${number}` || garment.source !== `ClothesSwap/THREAD${number}.png`
    || garment.url !== `${canonicalOrigin}/g/${id}` || !/^[a-f0-9]{64}$/.test(garment.svgSha256)
    || typeof garment.description !== 'string' || !garment.description.trim()) {
    throw new Error(`The permanent mapping for ${id} is invalid. Restore the checked-in printed-tag manifest.`);
  }
  const svg = await QRCode.toString(garment.url, {
    type: 'svg', errorCorrectionLevel: 'M', margin: 4, width: 1024,
    color: { dark: '#000000', light: '#ffffff' },
  });
  if (sha256(svg) !== garment.svgSha256) {
    throw new Error(`${id}: regenerated SVG differs from the frozen code. Restore the pinned QR dependency; do not change the manifest hash.`);
  }
  for (const directory of directories) {
    const path = resolve(root, directory, `${id}.svg`);
    try {
      if (sha256(await readFile(path)) !== garment.svgSha256) {
        throw new Error(`${path} differs from its frozen hash. Refusing to overwrite an existing printed QR code.`);
      }
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
  svgs.set(id, svg);
}
const escape = value => value.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const style = `
  :root { font-family: Arial, Helvetica, sans-serif; color: #202720; background: #f3f2e9; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 40px clamp(16px, 4vw, 64px); }
  a { color: inherit; text-underline-offset: 4px; }
  .brand { font-size: 17px; font-weight: 800; letter-spacing: .18em; }
  main { max-width: 1240px; margin: 0 auto; }
  header { margin-bottom: 28px; }
  h1 { font-size: clamp(32px, 4vw, 52px); letter-spacing: -.055em; line-height: 1.05; margin: 28px 0 14px; }
  .intro { max-width: 690px; font-size: 17px; line-height: 1.55; color: #596157; margin: 0; }
  .actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 20px; }
  .grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
  .tag { text-align: center; background: #fff; border: 1px solid #dfe2d7; border-radius: 18px; padding: 24px 16px 26px; break-inside: avoid; }
  .number { display: inline-flex; align-items: center; justify-content: center; width: 34px; height: 34px; border-radius: 50%; background: #ecf0e6; color: #425c32; font-weight: 700; }
  h2 { font-size: 23px; letter-spacing: -.03em; margin: 14px 0 2px; }
  .code { font: 12px monospace; color: #747b70; margin: 0 0 10px; }
  .description { font-size: 14px; color: #596157; margin: 8px 0 12px; }
  .qr { display: block; width: 100%; height: auto; max-width: 350px; margin: 0 auto; }
  .button { display: inline-block; padding: 12px 20px; border: 1px solid #cbd3c1; border-radius: 999px; text-decoration: none; font-size: 14px; font-weight: 700; }
  .button:hover, .button:focus-visible { background: #edf1e6; outline-offset: 4px; }
  .destination { display: block; margin-top: 17px; font-size: 12px; line-height: 1.55; overflow-wrap: anywhere; color: #626c5e; }
  .note { margin: 24px 0 0; font-size: 14px; line-height: 1.6; color: #626c5e; }
  .single { max-width: 730px; margin: 0 auto; }
  .single header { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
  .single .tag { padding: 24px clamp(16px, 5vw, 46px) 30px; }
  .single .qr { max-width: 560px; }
  .single h1 { margin: 14px 0 6px; font-size: 40px; }
  .single .note { text-align: center; }
  @media (max-width: 700px) { body { padding-top: 24px; } .grid { grid-template-columns: 1fr; } .tag { max-width: 460px; width: 100%; margin: 0 auto; } .single .tag { max-width: none; } }
  @media print { :root { background: #fff; } body { padding: 0; } header, .button, .note, .destination { display: none !important; } .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 5mm; } .tag { border-radius: 0; padding: 5mm; } .single { max-width: 150mm; } .single .tag { margin: 0 auto; } .qr { max-width: 100%; } }
`;
const document = (title, content) => `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex"><title>${escape(title)} · THREAD</title><style>${style}</style></head><body>${content}</body></html>\n`;
const card = (garment, index) => `<article class="tag"><span class="number">${index + 1}</span><h2>${garment.label}</h2><p class="description">${escape(garment.description)}</p><p class="code">${garment.id}</p><img class="qr" src="${garment.id}.svg" width="1024" height="1024" alt="Scan to try on ${garment.label}"><a class="button" href="${garment.id}.html">Show larger QR code ↗</a><a class="destination" href="${escape(garment.url)}">${escape(garment.url)}</a></article>`;
const gallery = document('Ten permanent clothing QR codes', `<main><header><div class="brand">THREAD ↗</div><h1>Ten pieces. One fitting room.</h1><p class="intro">These are the final printed codes for THREAD 1–10. Each opens its fixed garment address, with no expiry. Scan with your phone camera, sign in and finish your profile once.</p><div class="actions"><a class="button" href="thread-tags-print.pdf">Download all ten printable tags (PDF) ↓</a></div><p class="note">THREAD 1–3 are available for try-on. THREAD 4–10 have their final codes reserved; try-on will be available after their garments and presets are prepared.</p></header><section class="grid" aria-label="Ten permanent clothing tags">${garments.map(card).join('')}</section><p class="note">The codes stay the same when the app, clothing images or presets are updated. Trying on a piece requires internet, the live app, a prepared garment and the running worker. Already in THREAD? Use the scanner on your fitting-room home screen.</p></main>`);

// Keep the checked-in tags and the deployable static pages identical.
for (const directory of directories) {
  const output = resolve(root, directory);
  await mkdir(output, { recursive: true });
  await writeFile(resolve(output, 'demo-clothes.html'), gallery);
  for (const [index, garment] of garments.entries()) {
    // Exclusive creation preserves the exact bytes of existing printed SVGs.
    try {
      await writeFile(resolve(output, `${garment.id}.svg`), svgs.get(garment.id), { flag: 'wx' });
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      if (sha256(await readFile(resolve(output, `${garment.id}.svg`))) !== garment.svgSha256) {
        throw new Error(`${garment.id}: existing SVG changed after validation. Refusing to overwrite it.`);
      }
    }
    const tag = document(`${garment.label} QR code`, `<main class="single"><header><div class="brand">THREAD ↗</div><a href="demo-clothes.html">All ten pieces</a></header><article class="tag"><span class="number">${index + 1}</span><h1>${garment.label}</h1><p class="description">${escape(garment.description)}</p><p class="code">${garment.id}</p><img class="qr" src="${garment.id}.svg" width="1024" height="1024" alt="Scan to try on ${garment.label}"><p>Scan with your phone camera.</p><a class="destination" href="${escape(garment.url)}">${escape(garment.url)}</a></article><p class="note">Final printed code · fixed destination · no expiry.<br>Try-on requires the live app, a prepared garment and the running worker.<br><a href="thread-tags-print.pdf">Download all ten printable tags (PDF)</a></p></main>`);
    await writeFile(resolve(output, `${garment.id}.html`), tag);
  }
}
console.log(`Permanent QR page: ${canonicalOrigin}/tags/demo-clothes.html`);
for (const garment of garments) console.log(`${garment.label}: ${garment.url}`);
