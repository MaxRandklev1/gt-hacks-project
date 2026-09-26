import QRCode from 'qrcode';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const [origin, garmentId, ...titleWords] = process.argv.slice(2);
if (!origin || !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/.test(garmentId || '')) {
  throw new Error('Usage: pnpm qr https://YOUR-PROJECT.firebaseapp.com garment-id Item name');
}
const base = new URL(origin);
if (base.protocol !== 'https:' && !['localhost','127.0.0.1'].includes(base.hostname)) throw new Error('Use the HTTPS address judges can reach on their phones.');
const url = new URL(`/g/${garmentId}`, base.origin).href;
const title = titleWords.join(' ') || garmentId;
const escaped = title.replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const output = resolve('docs/tags');
await mkdir(output, {recursive:true});
const svg = await QRCode.toString(url, { type:'svg', errorCorrectionLevel:'M', margin:4, width:360, color:{dark:'#171a17',light:'#ffffff'} });
await writeFile(resolve(output,`${garmentId}.svg`),svg);
await writeFile(resolve(output,`${garmentId}.html`),`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escaped} · THREAD tag</title><style>body{margin:0;background:#e9e8e0;font:16px system-ui;color:#171a17;display:grid;place-items:center;min-height:100vh}.tag{background:#fff;box-sizing:border-box;width:340px;padding:32px;text-align:center;border:1px solid #deded5;border-radius:14px}.brand{font-size:18px;font-weight:800;letter-spacing:.18em}.hole{width:12px;height:12px;border:1px solid #ddd;border-radius:50%;margin:0 auto 24px}.tag h1{font-size:32px;letter-spacing:-.06em;margin:28px 0 8px}.tag img{width:260px;height:260px;display:block;margin:auto}.name{font-size:14px;color:#61645c}.code{font:12px monospace;letter-spacing:.1em}.note{font-size:13px;line-height:1.6;color:#61645c}@media print{body{background:#fff;min-height:0}.tag{margin:12mm auto;break-inside:avoid}}</style><article class="tag"><div class="hole"></div><div class="brand">THREAD ↗</div><h1>See it on you.</h1><p class="name">${escaped}</p><img src="${garmentId}.svg" alt="QR code for ${escaped}"><p class="code">${garmentId}</p><p class="note">Scan with your phone camera.<br>Sign in once. Try on something new.</p></article></html>`);
console.log(`Tag: docs/tags/${garmentId}.html\nQR destination: ${url}`);
