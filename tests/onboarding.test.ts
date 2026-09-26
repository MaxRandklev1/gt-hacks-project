import { describe, expect, it } from 'vitest';
import { onboardingProgress } from '../web/src/lib/onboarding';
import type { Job, OnboardingDraft, UserProfile } from '../web/src/lib/client';

const train: Job = { id: 'train-1', uid: 'person', kind: 'train', requestVersion: 4, status: 'running' };
const measurements = { heightCm: 178, weightKg: 70, measurementSystem: 'us' as const };
const profile: UserProfile = { trainingJobId: train.id, identity: { status: 'training', version: train.id } };
const draft = { trainingJobId: train.id, selfiePath: 'private/current-selfie.jpg' } as OnboardingDraft;
const completed = { ...train, status: 'completed' as const };
const trained: UserProfile = { ...profile, identity: { status: 'awaiting_reference', version: train.id } };

describe('Photos-first onboarding resumes independent of training speed', () => {
  it('starts with photos for a new account', () => {
    expect(onboardingProgress({}, [], null).screen).toBe('photos');
  });
  it('opens the questions immediately after enqueue, before either subscription catches up', () => {
    const state = onboardingProgress({}, [], null, train.id);
    expect(state.screen).toBe('measurements');
    expect(state.background).toBe(true);
    expect(state.ready).toBe(false);
  });
  it.each(['queued', 'running', 'completed'] as const)('keeps missing questions available when training is %s', status => {
    const state = onboardingProgress(status === 'completed' ? trained : profile, [{ ...train, status }], null);
    expect(state.screen).toBe('measurements');
    expect(state.canFinalize).toBe(false);
  });
  it('resumes at the selfie after saved measurements, even if training has already finished', () => {
    const state = onboardingProgress({ ...trained, ...measurements }, [completed], null);
    expect(state.screen).toBe('selfie');
    expect(state.canFinalize).toBe(false);
  });
  it('waits on training when the user finishes the questions first', () => {
    const state = onboardingProgress({ ...profile, ...measurements }, [train], draft);
    expect(state.screen).toBe('preparing');
    expect(state.canFinalize).toBe(false);
  });
  it('finalizes only after training, saved measurements, and the matching selfie all exist', () => {
    const state = onboardingProgress({ ...trained, ...measurements }, [completed], draft);
    expect(state.screen).toBe('preparing');
    expect(state.canFinalize).toBe(true);
    expect(state.ready).toBe(false);
  });
  it.each(['queued', 'running', 'completed'] as const)('does not request duplicate finalization when its job is %s', status => {
    const finalize: Job = { id: 'finalize-1', uid: 'person', kind: 'finalize', trainingJobId: train.id, status };
    expect(onboardingProgress({ ...trained, ...measurements }, [finalize, completed], draft).canFinalize).toBe(false);
  });
  it('exposes failed finalization for an explicit retry without training again', () => {
    const finalize: Job = { id: 'finalize-1', uid: 'person', kind: 'finalize', trainingJobId: train.id, status: 'failed' };
    const state = onboardingProgress({ ...trained, ...measurements }, [finalize, completed], draft);
    expect(state.finalizationFailed).toBe(true);
    expect(state.trainingFailed).toBe(false);
    expect(state.canFinalize).toBe(true);
  });
  it('never reuses a prior training run’s selfie', () => {
    const state = onboardingProgress({ ...trained, ...measurements }, [completed], { ...draft, trainingJobId: 'old-training' });
    expect(state.screen).toBe('selfie');
    expect(state.canFinalize).toBe(false);
  });
  it('requires a saved unit preference before finalization', () => {
    expect(onboardingProgress({ ...trained, heightCm: 178, weightKg: 70 }, [completed], draft).screen).toBe('measurements');
  });
  it('waits for the profile subscription to catch up to the completed training', () => {
    const staleProfile: UserProfile = { ...trained, ...measurements, trainingJobId: 'old-training', identity: { status: 'awaiting_reference', version: 'old-training' } };
    expect(onboardingProgress(staleProfile, [completed], draft).canFinalize).toBe(false);
  });
  it('does not mistake an old ready identity for the new finished training', () => {
    const oldReady: UserProfile = { ...profile, ...measurements, identity: { status: 'ready', version: 'old-training' } };
    expect(onboardingProgress(oldReady, [completed], draft).ready).toBe(false);
  });
  it('shows the fitting room only after the worker attaches the new reference', () => {
    const ready: UserProfile = { ...trained, ...measurements, identity: { status: 'ready', version: train.id } };
    expect(onboardingProgress(ready, [completed], draft).screen).toBe('ready');
  });
  it('keeps existing ready and in-flight legacy identities compatible', () => {
    expect(onboardingProgress({ identity: { status: 'ready' } }, [], null).ready).toBe(true);
    expect(onboardingProgress({ identity: { status: 'training' } }, [{ ...train, requestVersion: 3 }], null).screen).toBe('preparing');
  });
});
