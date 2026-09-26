import { describe, expect, it } from 'vitest';
import { canonicalAppUrl, isStoreOrigin } from '../web/src/lib/origin';

describe('Firebase redirect login origin', () => {
  it('preserves garment and return state when moving to the auth origin', () => {
    expect(canonicalAppUrl('https://demo.web.app/g/shirt?campaign=tag#section', 'demo', 'demo.firebaseapp.com'))
      .toBe('https://demo.firebaseapp.com/g/shirt?campaign=tag#section');
  });
  it.each(['https://demo.firebaseapp.com/', 'http://localhost:5173/g/shirt', 'https://other.web.app/g/shirt', 'https://demo.web.app.evil/g/shirt'])('does not redirect %s', href => {
    expect(canonicalAppUrl(href, 'demo', 'demo.firebaseapp.com')).toBeNull();
  });
  it('does not redirect to a custom or missing auth domain', () => {
    expect(canonicalAppUrl('https://demo.web.app/', 'demo', 'other.example')).toBeNull();
    expect(canonicalAppUrl('https://demo.web.app/', '', '')).toBeNull();
  });
  it('accepts the two exact Firebase Hosting aliases for this project', () => {
    expect(isStoreOrigin('https://demo.web.app', 'https://demo.firebaseapp.com')).toBe(true);
    expect(isStoreOrigin('https://demo.firebaseapp.com', 'https://demo.web.app')).toBe(true);
  });
  it.each(['https://other.web.app', 'http://demo.web.app', 'https://demo.web.app.evil', 'https://demo.web.app:8443'])('rejects foreign origin %s', origin => {
    expect(isStoreOrigin(origin, 'https://demo.firebaseapp.com')).toBe(false);
  });
});
