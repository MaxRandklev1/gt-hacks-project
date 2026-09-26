import type { Job, OnboardingDraft, UserProfile } from './client';
import { validateMeasurements } from './validation';

export function savedMeasurementsValid(profile: UserProfile | null) {
  if (!profile || !['us', 'metric'].includes(profile.measurementSystem || '')) return false;
  try { validateMeasurements(profile.heightCm!, profile.weightKg!); return true; } catch { return false; }
}

/** Resume from persisted work, even when training finishes before the questions do. */
export function onboardingProgress(profile: UserProfile | null, jobs: Job[], draft: OnboardingDraft | null, requestedTrainingId?: string) {
  const active = (job?: Job) => Boolean(job && (job.status === 'queued' || job.status === 'running'));
  const latest = jobs.find(job => job.kind === 'train');
  const persistedId = profile?.trainingJobId;
  const fallbackId = requestedTrainingId || (profile?.identity?.status === 'awaiting_reference' ? persistedId : undefined);
  const training = (requestedTrainingId ? jobs.find(job => job.id === requestedTrainingId && job.kind === 'train') : latest)
    || (fallbackId ? { id: fallbackId, uid: '', kind: 'train' as const, requestVersion: 4, status: requestedTrainingId ? 'queued' as const : 'completed' as const } : undefined);
  const background = training?.requestVersion === 4;
  const finalization = background ? jobs.find(job => job.kind === 'finalize' && job.trainingJobId === training.id) : undefined;
  const referenceSaved = Boolean(background && draft?.trainingJobId === training.id);
  const measurementsSaved = savedMeasurementsValid(profile);
  const currentIdentity = Boolean(background && training.id === persistedId && profile?.identity?.version === training.id);
  const ready = profile?.identity?.status === 'ready' && !active(training) && !active(finalization) && (!background || currentIdentity);
  const trainingFailed = training?.status === 'failed' || (profile?.identity?.status === 'failed' && !finalization);
  const finalizationFailed = finalization?.status === 'failed';
  const screen: 'photos' | 'measurements' | 'selfie' | 'preparing' | 'ready' = ready ? 'ready'
    : trainingFailed ? 'preparing'
    : background ? !measurementsSaved ? 'measurements' : !referenceSaved ? 'selfie' : 'preparing'
    : active(training) || profile?.identity?.status === 'selecting' || profile?.identity?.status === 'training' ? 'preparing' : 'photos';
  const canFinalize = Boolean(background && currentIdentity && training.status === 'completed' && profile?.identity?.status === 'awaiting_reference' && measurementsSaved && referenceSaved && !active(finalization) && finalization?.status !== 'completed');
  return { training, finalization, background, referenceSaved, measurementsSaved, ready, trainingFailed, finalizationFailed, screen, canFinalize };
}
