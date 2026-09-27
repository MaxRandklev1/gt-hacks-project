import { describe, expect, it } from 'vitest';
import { onboardingProgress, needsPersonalLookReview, readOnboardingChoices, saveOnboardingChoices } from '../web/src/lib/onboarding';
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
  it('shows a failed replacement instead of silently using the previous identity', () => {
    const state = onboardingProgress({ ...measurements, identity: { status: 'ready', version: 'previous' } }, [{ ...enroll, status: 'failed' }]);
    expect(state.ready).toBe(false);
    expect(state.enrollmentFailed).toBe(true);
    expect(state.canKeepPreviousLook).toBe(true);
    expect(state.screen).toBe('preparing');
  });
  it('allows an explicit choice to keep the previous look after a failed retake', () => {
    const profile = { ...measurements, identity: { status: 'ready' as const, version: 'previous' } };
    const failed = { ...enroll, status: 'failed' as const };
    expect(onboardingProgress(profile, [failed], undefined, enroll.id).ready).toBe(true);
    expect(onboardingProgress(profile, [failed], undefined, 'another-job').ready).toBe(false);
    expect(onboardingProgress(measurements, [failed], undefined, enroll.id).ready).toBe(false);
  });
  it('waits for the matching identity if job completion arrives before the profile snapshot', () => {
    const profile = { ...measurements, identity: { status: 'ready' as const, version: 'previous' } };
    const completed = { ...enroll, status: 'completed' as const };
    const state = onboardingProgress(profile, [completed], enroll.id);
    expect(state.ready).toBe(false);
    expect(state.awaitingIdentity).toBe(true);
    expect(state.screen).toBe('preparing');
    expect(onboardingProgress({ ...profile, identity: { ...profile.identity, version: enroll.id } }, [completed], enroll.id).ready).toBe(true);
  });
  it('waits for job completion if the new profile snapshot arrives first', () => {
    expect(onboardingProgress({ ...measurements, identity: { status: 'ready', version: enroll.id } }, [enroll], enroll.id).ready).toBe(false);
  });
  it('treats earlier trained identities as ready and unfinished training as needing a selfie', () => {
    expect(onboardingProgress({ identity: { status: 'ready' } }, []).ready).toBe(true);
    expect(onboardingProgress({ ...measurements, identity: { status: 'awaiting_reference' } }, []).screen).toBe('selfie');
  });
});

describe('Personal-look review and account-specific choices', () => {
  const personal: UserProfile = { identity: { status: 'ready', mode: 'personal_base', version: 'new-look', previewPath: 'users/person/identity/new-look/preview.png' } };
  it('reviews the newly submitted personal look before continuing a pending scan', () => {
    expect(needsPersonalLookReview(personal, 'new-look')).toBe(true);
    expect(needsPersonalLookReview(personal, undefined)).toBe(false);
    expect(needsPersonalLookReview(personal, 'previous-look')).toBe(false);
    expect(needsPersonalLookReview({ identity: { ...personal.identity!, status: 'training' } }, 'new-look')).toBe(false);
    expect(needsPersonalLookReview({ identity: { status: 'ready', mode: 'faceswap', version: 'new-look' } }, 'new-look')).toBe(false);
  });
  it('restores an unfinished review after reload without affecting another account or an ordinary return visit', () => {
    const entries = new Map<string, string>();
    const storage = { getItem: (key: string) => entries.get(key) ?? null, setItem: (key: string, value: string) => { entries.set(key, value); } };
    saveOnboardingChoices('alice', { pendingLookReview: 'new-look' }, storage);
    expect(needsPersonalLookReview(personal, readOnboardingChoices('alice', storage).pendingLookReview)).toBe(true);
    expect(readOnboardingChoices('bob', storage)).toEqual({});
    saveOnboardingChoices('alice', {}, storage);
    expect(needsPersonalLookReview(personal, readOnboardingChoices('alice', storage).pendingLookReview)).toBe(false);
  });
  it('persists the explicit previous-look choice without suppressing a later failed attempt', () => {
    let value = '';
    const storage = { getItem: () => value, setItem: (_key: string, next: string) => { value = next; } };
    saveOnboardingChoices('alice', { keptPreviousEnrollmentId: enroll.id }, storage);
    const kept = readOnboardingChoices('alice', storage).keptPreviousEnrollmentId;
    expect(onboardingProgress(personal, [{ ...enroll, status: 'failed' }], undefined, kept).ready).toBe(true);
    expect(onboardingProgress(personal, [{ ...enroll, id: 'later', status: 'failed' }], undefined, kept).ready).toBe(false);
  });
  it('tolerates unavailable or malformed browser storage', () => {
    expect(readOnboardingChoices('alice', { getItem: () => '{broken' })).toEqual({});
    expect(readOnboardingChoices('alice', { getItem: () => '{"pendingLookReview": 42}' }).pendingLookReview).toBeUndefined();
    expect(readOnboardingChoices('alice', { getItem: () => { throw new Error('blocked'); } })).toEqual({});
    expect(() => saveOnboardingChoices('alice', {}, { setItem: () => { throw new Error('blocked'); } })).not.toThrow();
  });
});
