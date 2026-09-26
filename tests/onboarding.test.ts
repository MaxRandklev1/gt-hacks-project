import { describe, expect, it } from 'vitest';
import { onboardingProgress } from '../web/src/lib/onboarding';
import type { Job, UserProfile } from '../web/src/lib/client';

const measurements = { heightCm: 178, weightKg: 70, measurementSystem: 'us' as const };
const enroll: Job = { id: 'enroll-1', uid: 'person', kind: 'enroll', requestVersion: 5, status: 'running' };

describe('Selfie-only onboarding', () => {
  it('starts with details for a new account, then asks for one selfie', () => {
    expect(onboardingProgress({}, []).screen).toBe('measurements');
    expect(onboardingProgress(measurements, []).screen).toBe('selfie');
  });
  it('requires a saved unit preference before the selfie', () => {
    expect(onboardingProgress({ heightCm: 178, weightKg: 70 }, []).screen).toBe('measurements');
  });
  it('shows setup immediately after enqueue, before the jobs subscription catches up', () => {
    const state = onboardingProgress(measurements, [], 'enroll-1');
    expect(state.screen).toBe('preparing');
    expect(state.enrolling).toBe(true);
  });
  it.each(['queued', 'running'] as const)('waits while enrollment is %s', status => {
    expect(onboardingProgress(measurements, [{ ...enroll, status }]).screen).toBe('preparing');
  });
  it('opens the fitting room once the worker marks the identity ready', () => {
    const ready = { ...measurements, identity: { status: 'ready' as const, mode: 'faceswap' as const, version: enroll.id } };
    expect(onboardingProgress(ready, [{ ...enroll, status: 'completed' }]).screen).toBe('ready');
  });
  it('surfaces a rejected selfie for a retake', () => {
    const state = onboardingProgress(measurements, [{ ...enroll, status: 'failed', error: 'No clear face.' }]);
    expect(state.screen).toBe('preparing');
    expect(state.enrollmentFailed).toBe(true);
  });
  it('keeps an existing ready identity usable when a retake fails', () => {
    const state = onboardingProgress({ ...measurements, identity: { status: 'ready' } }, [{ ...enroll, status: 'failed' }]);
    expect(state.ready).toBe(true);
    expect(state.enrollmentFailed).toBe(false);
  });
  it('treats earlier trained identities as ready and unfinished training as needing a selfie', () => {
    expect(onboardingProgress({ identity: { status: 'ready' } }, []).ready).toBe(true);
    expect(onboardingProgress({ ...measurements, identity: { status: 'awaiting_reference' } }, []).screen).toBe('selfie');
  });
});
