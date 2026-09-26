import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CONSENT_VERSION, PHOTO_COUNT, validateMeasurements, validatePhotoFiles } from '../web/src/lib/validation';
import { clearPendingGarment, parseGarmentCode, readPendingGarment, rememberGarment } from '../web/src/lib/qr';

describe('Onboarding validation', () => {
  it('uses eight photos and the current consent version', () => {
    expect(PHOTO_COUNT).toBe(8);
    expect(CONSENT_VERSION).toBe('identity-training-v1');
  });

  it.each([[80, 25], [250, 300], [175.5, 70.2]])('accepts supported measurements %s cm / %s kg', (height, weight) => {
    expect(() => validateMeasurements(height, weight)).not.toThrow();
  });

  it.each([79.9, 250.1, Number.NaN, Number.POSITIVE_INFINITY])('rejects invalid height %s', height => {
    expect(() => validateMeasurements(height, 70)).toThrow(/height/i);
  });

  it.each([24.9, 300.1, Number.NaN, Number.NEGATIVE_INFINITY])('rejects invalid weight %s', weight => {
    expect(() => validateMeasurements(175, weight)).toThrow(/weight/i);
  });

  const photos = () => Array.from({ length: 8 }, (_, index) => ({ name: `photo-${index}.jpg`, type: 'image/jpeg', size: 1024 }));

  it('accepts exactly eight JPEG, PNG, or WebP files including size boundaries', () => {
    const files = photos();
    files[0] = { name: 'small.png', type: 'image/png', size: 1 };
    files[1] = { name: 'large.webp', type: 'image/webp', size: 20 * 1024 * 1024 };
    expect(() => validatePhotoFiles(files)).not.toThrow();
  });

  it.each([0, 7, 9])('rejects %s photos', count => {
    const files = Array.from({ length: count }, () => photos()[0]);
    expect(() => validatePhotoFiles(files)).toThrow(/exactly eight/i);
  });

  it.each(['image/heic', 'image/gif', '', 'text/plain'])('rejects unsupported MIME type %s with the filename', type => {
    const files = photos();
    files[3] = { ...files[3], type };
    expect(() => validatePhotoFiles(files)).toThrow(/photo-3.jpg: use JPG, PNG, or WebP/);
  });

  it.each([0, 20 * 1024 * 1024 + 1])('rejects invalid file size %s', size => {
    const files = photos();
    files[2] = { ...files[2], size };
    expect(() => validatePhotoFiles(files)).toThrow(/photo-2.jpg: each photo/);
  });
});

describe('Garment QR parsing and pending-tag persistence', () => {
  let stored: Map<string, string>;
  let replaceState: ReturnType<typeof vi.fn>;

  function locationAt(path: string) {
    const location = new URL(path, 'https://thread.example');
    vi.stubGlobal('location', location);
    vi.stubGlobal('window', { location });
  }

  beforeEach(() => {
    stored = new Map();
    locationAt('/');
    replaceState = vi.fn();
    vi.stubGlobal('history', { replaceState });
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => { stored.set(key, value); },
      removeItem: (key: string) => { stored.delete(key); },
    });
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it.each([
    [' coat-1 ', 'coat-1'],
    ['/g/coat-1', 'coat-1'],
    ['https://thread.example/g/coat-1/', 'coat-1'],
    ['https://thread.example/?garment=coat_2', 'coat_2'],
    ['/g/coat%2D3', 'coat-3'],
  ])('accepts supported tag %s', (input, expected) => {
    expect(parseGarmentCode(input)).toBe(expected);
  });

  it.each(['https://evil.example/g/coat-1', '//evil.example/g/coat-1', 'https://thread.example.evil/g/coat-1', 'javascript:alert(1)'])('rejects foreign or non-web tag %s', input => {
    expect(() => parseGarmentCode(input)).toThrow(/not a tag from this store/);
  });

  it.each(['/g/', '/g/../', '/g/coat%2Fextra', '/g/_leading', '/?garment=with%20space', `/g/${'a'.repeat(81)}`, '/g/%E0%A4%A'])('rejects invalid garment data %s', input => {
    expect(() => parseGarmentCode(input)).toThrow();
  });

  it('persists a valid tag across a login-style navigation', () => {
    locationAt('/g/coat-1');
    expect(readPendingGarment()).toBe('coat-1');
    locationAt('/');
    expect(readPendingGarment()).toBe('coat-1');
  });

  it('prefers the current tag over an older saved tag', () => {
    rememberGarment('old-coat');
    locationAt('/?garment=new-coat');
    expect(readPendingGarment()).toBe('new-coat');
  });

  it('rejects invalid saved values and does not persist invalid codes', () => {
    expect(() => rememberGarment('../bad')).toThrow(/Invalid garment/);
    stored.set('thread.pending-garment.v1', '<script>');
    expect(readPendingGarment()).toBeNull();
  });

  it('clears the saved tag and URL while preserving unrelated query parameters', () => {
    rememberGarment('coat-1');
    locationAt('/g/coat-1?garment=coat-1&campaign=demo');
    clearPendingGarment();
    expect(stored.size).toBe(0);
    expect(replaceState).toHaveBeenCalledOnce();
    const rewritten = replaceState.mock.calls[0][2] as URL;
    expect(rewritten.pathname).toBe('/');
    expect(rewritten.searchParams.has('garment')).toBe(false);
    expect(rewritten.searchParams.get('campaign')).toBe('demo');
  });

  it('does not rewrite an ordinary page when clearing a stored tag', () => {
    rememberGarment('coat-1');
    locationAt('/profile?campaign=demo');
    clearPendingGarment();
    expect(replaceState).not.toHaveBeenCalled();
    expect(readPendingGarment()).toBeNull();
  });
});
