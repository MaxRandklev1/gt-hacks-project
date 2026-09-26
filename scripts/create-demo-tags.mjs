import QRCode from 'qrcode';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const root = fileURLToPath(new URL('..', import.meta.url));
const origin = new URL(process.argv[2] || 'https://gt-hacks-thread-2026.firebaseapp.com');
if (origin.protocol !== 'https:') throw new Error('Use the public HTTPS address phones can open.');
const garments = [1, 2, 3].map(number => ({
  id: `thread-${number}`,
  label: `THREAD ${number}`,
  description: ['Navy & white striped polo', 'Dragon graphic T-shirt', 'Olive hooded jacket'][number - 1],
  url: new URL(`/g/thread-${number}`, origin.origin).href,
}));
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
  @media print { :root { background: #fff; } body { padding: 0; } header, .button, .note, .destination { display: none !important; } .grid { gap: 5mm; } .tag { border-radius: 0; padding: 5mm; } .single { max-width: 150mm; } .single .tag { margin: 0 auto; } .qr { max-width: 100%; } }
`;
const document = (title, content) => `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex"><title>${escape(title)} · THREAD</title><style>${style}</style></head><body>${content}</body></html>\n`;
const card = (garment, index) => `<article class="tag"><span class="number">${index + 1}</span><h2>${garment.label}</h2><p class="description">${escape(garment.description)}</p><p class="code">${garment.id}</p><img class="qr" src="${garment.id}.svg" width="1024" height="1024" alt="Scan to try on ${garment.label}"><a class="button" href="${garment.id}.html">Show larger QR code ↗</a><a class="destination" href="${escape(garment.url)}">${escape(garment.url)}</a></article>`;
const gallery = document('Demo clothing QR codes', `<main><header><div class="brand">THREAD ↗</div><h1>Scan a piece. See it on you.</h1><p class="intro">Open your phone camera and scan a code below. Each code opens that piece in THREAD. Sign in and finish your profile once, then try on your choice.</p></header><section class="grid" aria-label="Three demo clothing tags">${garments.map(card).join('')}</section><p class="note">Already in THREAD? Use the scanner on your fitting-room home screen. Keep this page open on your PC, and choose “Show larger QR code” if your phone needs a closer look.</p></main>`);

// Keep the checked-in tags and the deployable static pages identical.
for (const directory of ['docs/tags', 'web/public/tags']) {
  const output = resolve(root, directory);
  await mkdir(output, { recursive: true });
  await writeFile(resolve(output, 'demo-clothes.html'), gallery);
  for (const [index, garment] of garments.entries()) {
    const svg = await QRCode.toString(garment.url, {
      type: 'svg', errorCorrectionLevel: 'M', margin: 4, width: 1024,
      color: { dark: '#000000', light: '#ffffff' },
    });
    await writeFile(resolve(output, `${garment.id}.svg`), svg);
    const tag = document(`${garment.label} QR code`, `<main class="single"><header><div class="brand">THREAD ↗</div><a href="demo-clothes.html">All three pieces</a></header><article class="tag"><span class="number">${index + 1}</span><h1>${garment.label}</h1><p class="description">${escape(garment.description)}</p><p class="code">${garment.id}</p><img class="qr" src="${garment.id}.svg" width="1024" height="1024" alt="Scan to try on ${garment.label}"><p>Scan with your phone camera.</p><a class="destination" href="${escape(garment.url)}">${escape(garment.url)}</a></article><p class="note">Sign in and finish your profile once. Your fitting begins when your profile is ready.</p></main>`);
    await writeFile(resolve(output, `${garment.id}.html`), tag);
  }
}
console.log(`Demo QR page: ${new URL('/tags/demo-clothes.html', origin.origin).href}`);
for (const garment of garments) console.log(`${garment.label}: ${garment.url}`);
