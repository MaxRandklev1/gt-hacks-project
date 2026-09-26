import { describe, expect, it } from 'vitest';
import { validateReferenceSelfie, type SelfieSelection } from '../web/src/lib/validation';

describe('Required current selfie', () => {
  const now = 1_800_000_000_000;
  const selfie = { type: 'image/jpeg', size: 500_000 };
  const camera: SelfieSelection = { source: 'camera', selectedAt: now, capturedAt: now };
  it('accepts a fresh JPEG and the capture-age boundary', () => {
    expect(() => validateReferenceSelfie(selfie, camera, now)).not.toThrow();
    expect(() => validateReferenceSelfie(selfie, { ...camera, capturedAt: now - 3_600_000 }, now)).not.toThrow();
  });
  it.each([null, undefined, { type: 'image/heic', size: 100 }, { type: 'image/jpeg', size: 0 }, { type: 'image/jpeg', size: 20 * 1024 * 1024 + 1 }])('rejects a missing or invalid selfie %s', value => {
    expect(() => validateReferenceSelfie(value, camera, now)).toThrow(/recent selfie/);
  });
  it.each([NaN, Infinity, now - 3_600_001, now + 120_001])('rejects expired or invalid capture time %s', capturedAt => {
    expect(() => validateReferenceSelfie(selfie, { ...camera, capturedAt }, now)).toThrow(/again/);
  });
  it.each(['image/jpeg', 'image/png', 'image/webp'])('accepts a chosen %s without inventing its capture time', type => {
    expect(() => validateReferenceSelfie({ ...selfie, type }, { source: 'upload', selectedAt: now }, now)).not.toThrow();
  });
  it.each([NaN, now - 3_600_001, now + 120_001])('requires a current selection session %s', selectedAt => {
    expect(() => validateReferenceSelfie(selfie, { source: 'upload', selectedAt }, now)).toThrow(/again/);
  });
  it('rejects missing camera capture time, reversed times and invented upload capture time', () => {
    for (const selection of [{ source: 'camera', selectedAt: now }, { ...camera, capturedAt: now + 1 }, { source: 'upload', selectedAt: now, capturedAt: now }] as SelfieSelection[]) {
      expect(() => validateReferenceSelfie(selfie, selection, now)).toThrow(/again/);
    }
  });
});
