import { isStoreOrigin } from './origin';

const KEY = 'thread.pending-garment.v1';
const GARMENT_ID = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/;

export function parseGarmentCode(value: string): string {
  const text = value.trim();
  if (GARMENT_ID.test(text)) return text;
  let url: URL;
  try { url = new URL(text, window.location.origin); }
  catch { throw new Error('Scan a THREAD clothing tag or enter its item code.'); }
  if (!isStoreOrigin(url.origin, window.location.origin)) throw new Error('This QR code is not a tag from this store.');
  const match = url.pathname.match(/^\/g\/([^/]+)\/?$/);
  let id: string | null;
  try { id = match ? decodeURIComponent(match[1]) : url.searchParams.get('garment'); }
  catch { throw new Error('This tag does not contain a valid item code.'); }
  if (!id || !GARMENT_ID.test(id)) throw new Error('This tag does not contain a valid item code.');
  return id;
}

export function rememberGarment(id: string) {
  if (!GARMENT_ID.test(id)) throw new Error('Invalid garment code.');
  localStorage.setItem(KEY, id);
}
export function clearPendingGarment() {
  localStorage.removeItem(KEY);
  if (/^\/g\//.test(location.pathname) || new URLSearchParams(location.search).has('garment')) {
    const url = new URL(location.href);
    url.pathname = '/'; url.searchParams.delete('garment');
    history.replaceState({}, '', url);
  }
}
export function readPendingGarment(): string | null {
  const url = new URL(location.href);
  if (/^\/g\//.test(url.pathname) || url.searchParams.has('garment')) {
    const id = parseGarmentCode(url.href); rememberGarment(id); return id;
  }
  const saved = localStorage.getItem(KEY);
  return saved && GARMENT_ID.test(saved) ? saved : null;
}
