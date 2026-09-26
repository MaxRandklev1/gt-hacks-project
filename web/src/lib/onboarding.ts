import type { Job, UserProfile } from './client';
import { validateMeasurements } from './validation';

export function savedMeasurementsValid(profile: UserProfile | null) {
  if (!profile || !['us', 'metric'].includes(profile.measurementSystem || '')) return false;
  try { validateMeasurements(profile.heightCm!, profile.weightKg!); return true; } catch { return false; }
}

/** Selfie-only onboarding: details, one selfie, then a few-second enrollment job. */
export function onboardingProgress(profile: UserProfile | null, jobs: Job[], requestedEnrollmentId?: string) {
  const active = (job?: Job) => Boolean(job && (job.status === 'queued' || job.status === 'running'));
  const enrollment = (requestedEnrollmentId ? jobs.find(job => job.id === requestedEnrollmentId) : jobs.find(job => job.kind === 'enroll'))
    || (requestedEnrollmentId ? { id: requestedEnrollmentId, uid: '', kind: 'enroll' as const, requestVersion: 5, status: 'queued' as const } : undefined);
  const measurementsSaved = savedMeasurementsValid(profile);
  const enrolling = active(enrollment);
  const ready = profile?.identity?.status === 'ready' && !enrolling;
  const enrollmentFailed = enrollment?.status === 'failed' && !ready;
  const screen: 'measurements' | 'selfie' | 'preparing' | 'ready' = ready ? 'ready'
    : enrolling || enrollmentFailed ? 'preparing'
    : !measurementsSaved ? 'measurements' : 'selfie';
  return { enrollment, measurementsSaved, ready, enrolling, enrollmentFailed, screen };
}
