import { describe, expect, it } from 'vitest';
import { validateReferenceSelfie } from '../web/src/lib/validation';

describe('Required current selfie', () => {
  const now = 1_800_000_000_000;
  const selfie = { type: 'image/jpeg', size: 500_000 };
  it('accepts a fresh JPEG and the capture-age boundary', () => {
    expect(() => validateReferenceSelfie(selfie, now, now)).not.toThrow();
    expect(() => validateReferenceSelfie(selfie, now - 3_600_000, now)).not.toThrow();
  });
  it.each([null, undefined, { type: 'image/png', size: 100 }, { type: 'image/jpeg', size: 0 }, { type: 'image/jpeg', size: 20 * 1024 * 1024 + 1 }])('rejects a missing or invalid selfie %s', value => {
    expect(() => validateReferenceSelfie(value, now, now)).toThrow(/camera/);
  });
  it.each([NaN, Infinity, now - 3_600_001, now + 120_001])('rejects expired or invalid capture time %s', capturedAt => {
    expect(() => validateReferenceSelfie(selfie, capturedAt, now)).toThrow(/new selfie/);
  });
});
