import { describe, expect, it } from 'vitest';
import { onboardingProgress, replacementSetupScreen, savedMeasurementsValid, readOnboardingChoices, saveOnboardingChoices } from '../web/src/lib/onboarding';
import type { Job, UserProfile } from '../web/src/lib/client';

const measurements = { heightCm: 178, weightKg: 70, measurementSystem: 'us' as const, bodyStyle: 'male' as const };
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

describe('Automatic personal-look completion and account-specific choices', () => {
  const personal: UserProfile = { identity: { status: 'ready', mode: 'personal_base', version: 'new-look', previewPath: 'users/person/identity/new-look/preview.png' } };
  it('uses a completed personal look immediately, including after reload', () => {
    const completed = { ...enroll, id: 'new-look', status: 'completed' as const };
    expect(onboardingProgress(personal, [completed], completed.id).screen).toBe('ready');
    expect(onboardingProgress(personal, [completed]).screen).toBe('ready');
    expect(onboardingProgress({ identity: { ...personal.identity!, status: 'training' } }, [completed]).ready).toBe(false);
  });
  it('ignores legacy pending reviews without losing account-specific failure recovery choices', () => {
    const entries = new Map<string, string>([['thread:onboarding:alice', JSON.stringify({ pendingLookReview: 'new-look', keptPreviousEnrollmentId: 'failed-retake' })]]);
    const storage = { getItem: (key: string) => entries.get(key) ?? null, setItem: (key: string, value: string) => { entries.set(key, value); } };
    expect(readOnboardingChoices('alice', storage)).toEqual({ keptPreviousEnrollmentId: 'failed-retake' });
    expect(readOnboardingChoices('bob', storage)).toEqual({});
    saveOnboardingChoices('alice', {}, storage);
    expect(readOnboardingChoices('alice', storage)).toEqual({});
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
    expect(readOnboardingChoices('alice', { getItem: () => '{"keptPreviousEnrollmentId": 42}' }).keptPreviousEnrollmentId).toBeUndefined();
    expect(readOnboardingChoices('alice', { getItem: () => { throw new Error('blocked'); } })).toEqual({});
    expect(() => saveOnboardingChoices('alice', {}, { setItem: () => { throw new Error('blocked'); } })).not.toThrow();
  });
});

describe('Body style choice', () => {
  it('requires male or female alongside height, weight and units', () => {
    expect(savedMeasurementsValid(measurements)).toBe(true);
    expect(savedMeasurementsValid({ ...measurements, bodyStyle: 'female' })).toBe(true);
    const { bodyStyle: _omitted, ...withoutStyle } = measurements;
    expect(savedMeasurementsValid(withoutStyle)).toBe(false);
    expect(savedMeasurementsValid({ ...measurements, bodyStyle: 'other' as never })).toBe(false);
  });
  it('keeps an existing look usable without a body choice, but collects it before a retake', () => {
    const { bodyStyle: _omitted, ...oldMeasurements } = measurements;
    const profile: UserProfile = { ...oldMeasurements, identity: { status: 'ready', mode: 'personal_base', version: 'old-look' } };
    expect(onboardingProgress(profile, []).ready).toBe(true);
    expect(onboardingProgress(profile, []).screen).toBe('ready');
    expect(replacementSetupScreen(profile)).toBe('measurements');
  });
  it.each(['male', 'female'] as const)('lets a %s account with complete details retake directly', bodyStyle => {
    expect(replacementSetupScreen({ ...measurements, bodyStyle, identity: { status: 'ready', version: 'saved-look' } })).toBe('selfie');
  });
  it('sends incomplete new accounts to details before a replacement selfie', () => {
    expect(replacementSetupScreen(null)).toBe('measurements');
    expect(replacementSetupScreen({ bodyStyle: 'female' })).toBe('measurements');
  });
});
