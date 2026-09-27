import type { Job, UserProfile } from './client';
import { validateMeasurements } from './validation';

export function savedMeasurementsValid(profile: UserProfile | null) {
  if (!profile || !['us', 'metric'].includes(profile.measurementSystem || '') || !['male', 'female'].includes(profile.bodyStyle || '')) return false;
  try { validateMeasurements(profile.heightCm!, profile.weightKg!); return true; } catch { return false; }
}

/** A saved look remains usable, but rebuilding requires all current setup choices. */
export function replacementSetupScreen(profile: UserProfile | null): 'measurements' | 'selfie' {
  return savedMeasurementsValid(profile) ? 'selfie' : 'measurements';
}

export type OnboardingChoices = { pendingLookReview?: string; keptPreviousEnrollmentId?: string };
const choicesKey = (uid: string) => `thread:onboarding:${uid}`;
export function readOnboardingChoices(uid: string, storage?: Pick<Storage, 'getItem'>): OnboardingChoices {
  try {
    const value = JSON.parse((storage || localStorage).getItem(choicesKey(uid)) || '{}');
    return {
      pendingLookReview: typeof value?.pendingLookReview === 'string' ? value.pendingLookReview : undefined,
      keptPreviousEnrollmentId: typeof value?.keptPreviousEnrollmentId === 'string' ? value.keptPreviousEnrollmentId : undefined,
    };
  } catch { return {}; }
}
export function saveOnboardingChoices(uid: string, choices: OnboardingChoices, storage?: Pick<Storage, 'setItem'>) {
  try { (storage || localStorage).setItem(choicesKey(uid), JSON.stringify(choices)); } catch { /* Current-tab choices still work when browser storage is unavailable. */ }
}
export function needsPersonalLookReview(profile: UserProfile | null, enrollmentId?: string) {
  return Boolean(enrollmentId && profile?.identity?.status === 'ready' && profile.identity.mode === 'personal_base'
    && profile.identity.version === enrollmentId && profile.identity.previewPath);
}

/** Selfie-only onboarding: details, one selfie, then personal-base preparation. */
export function onboardingProgress(profile: UserProfile | null, jobs: Job[], requestedEnrollmentId?: string, keptPreviousEnrollmentId?: string) {
  const active = (job?: Job) => Boolean(job && (job.status === 'queued' || job.status === 'running'));
  const enrollment = (requestedEnrollmentId ? jobs.find(job => job.id === requestedEnrollmentId) : jobs.find(job => job.kind === 'enroll'))
    || (requestedEnrollmentId ? { id: requestedEnrollmentId, uid: '', kind: 'enroll' as const, requestVersion: 5, status: 'queued' as const } : undefined);
  const measurementsSaved = savedMeasurementsValid(profile);
  const enrolling = active(enrollment);
  const identityReady = profile?.identity?.status === 'ready';
  const enrollmentFailed = enrollment?.status === 'failed' && enrollment.id !== keptPreviousEnrollmentId;
  // Job and profile snapshots arrive independently even though the worker writes them together.
  const awaitingIdentity = enrollment?.status === 'completed' && profile?.identity?.version !== enrollment.id;
  const ready = identityReady && !enrolling && !enrollmentFailed && !awaitingIdentity;
  const canKeepPreviousLook = identityReady && enrollmentFailed && profile?.identity?.version !== enrollment?.id;
  const screen: 'measurements' | 'selfie' | 'preparing' | 'ready' = ready ? 'ready'
    : enrolling || enrollmentFailed || awaitingIdentity ? 'preparing'
    : !measurementsSaved ? 'measurements' : 'selfie';
  return { enrollment, measurementsSaved, ready, enrolling, enrollmentFailed, canKeepPreviousLook, awaitingIdentity, screen };
}
