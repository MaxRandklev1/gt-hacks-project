import type { Job } from './client';

export const enrollmentSteps = [
  { label: 'Check your selfie', detail: 'Make sure your face is clear and usable.' },
  { label: 'Select your body template', detail: 'Use your saved height and weight to choose the closest template.' },
  { label: 'Create your likeness', detail: 'Build your personal look from your face, hair and other visible features.' },
  { label: 'Prepare the details', detail: 'Prepare larger images and align your look to the pose.' },
  { label: 'Save your look', detail: 'Save it to your account, ready for clothing try-ons.' },
] as const;

const stageDetails: Record<string, { index: number; title: string; detail: string }> = {
  checking_selfie: { index: 0, title: 'Checking your selfie', detail: 'Checking that we can find a clear face in the photo you chose.' },
  checking_catalog: { index: 1, title: 'Selecting your body template', detail: 'Matching your saved measurements to a body template and checking its clothing previews.' },
  preparing_pieces: { index: 1, title: 'Checking your fitting room', detail: 'Checking the clothing previews for your body template.' },
  preparing_reference: { index: 2, title: 'Preparing your reference', detail: 'Preparing your selfie so your current features guide the image.' },
  creating_look: { index: 2, title: 'Creating your likeness', detail: 'Building your personal look from your selfie. This is the main image-creation stage.' },
  upscaling_look: { index: 3, title: 'Preparing larger images', detail: 'Preparing the larger version of your look for try-ons.' },
  fitting_look: { index: 3, title: 'Aligning and finishing your look', detail: 'Aligning the result with your body template and separating hair from clothing.' },
  saving_look: { index: 4, title: 'Saving your personal look', detail: 'Saving your images to your account so future scans can reuse them.' },
};

export function timestampMillis(value: unknown): number | undefined {
  try {
    const millis = value instanceof Date ? value.getTime()
      : typeof value === 'number' ? value
        : typeof value === 'string' ? Date.parse(value)
          : value && typeof value === 'object' && 'toMillis' in value && typeof value.toMillis === 'function' ? value.toMillis()
            : undefined;
    return typeof millis === 'number' && Number.isFinite(millis) ? millis : undefined;
  } catch { return undefined; }
}

export function durationText(milliseconds: number) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${String(seconds % 60).padStart(2, '0')}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;
}

function measuredCounter(value: unknown, total: unknown): { value: number; total: number } | undefined {
  if (typeof value !== 'number' || typeof total !== 'number' || !Number.isSafeInteger(value)
    || !Number.isSafeInteger(total) || total <= 0 || value < 0 || value > total) return;
  return { value, total };
}

export type StepState = 'pending' | 'current' | 'done';

/** Presentation uses reported worker stages, never elapsed time or milestone percentages. */
export function enrollmentDisplay(job: Job | undefined, now: number, awaitingIdentity = false, preview = false) {
  const queued = !job || job.status === 'queued';
  const failed = !preview && job?.status === 'failed';
  const completed = !preview && job?.status === 'completed';
  const active = !preview && !failed && !completed;
  const stage = !queued && !failed && !completed ? stageDetails[job?.stage || ''] : undefined;
  const preparation = !preview && queued && job?.stage === 'preparing_catalog'
    ? measuredCounter(job.preparation?.completed, job.preparation?.total) : undefined;
  const sampling = !preview && !queued && !failed && !completed && job?.stage === 'creating_look'
    ? measuredCounter(job.sampling?.step, job.sampling?.total) : undefined;
  const counter = preparation ? { ...preparation, label: 'Shared clothing previews' }
    : sampling ? { ...sampling, label: 'Image creation steps' } : undefined;
  const reportedAt = [timestampMillis(job?.updatedAt), timestampMillis(job?.workerSeenAt)]
    .filter((value): value is number => value !== undefined);
  const lastUpdate = reportedAt.length ? Math.max(...reportedAt) : undefined;
  const created = timestampMillis(job?.createdAt);
  // Do not keep a stopped job's timer running, or invent a start time on page load.
  const endpoint = active ? now : lastUpdate;
  const elapsed = !preview && created !== undefined && endpoint !== undefined
    ? Math.max(0, endpoint - created) : undefined;
  const updateAge = active && lastUpdate !== undefined ? Math.max(0, now - lastUpdate) : undefined;
  const stale = updateAge !== undefined && updateAge >= 120_000;
  // Submission time is not a worker heartbeat. Only unclaimed queued jobs get this hint.
  const noWorkerUpdateYet = active && queued && lastUpdate === undefined && elapsed !== undefined && elapsed >= 120_000;

  let title: string, detail: string;
  if (preview) {
    title = 'A look at the setup process';
    detail = 'These are the stages you’ll see after submitting a selfie. This preview has not started a request.';
  } else if (failed) {
    title = 'We couldn’t finish this look';
    detail = job?.error || job?.message || 'Your look was not completed. You can try another clear, front-facing selfie.';
  } else if (completed) {
    title = awaitingIdentity ? 'Your look is saved' : 'Your personal look is ready';
    detail = awaitingIdentity ? 'Waiting for your account to receive the saved look. Your image has finished processing.' : 'Your saved look is ready to review.';
  } else if (queued) {
    title = job?.stage === 'preparing_catalog' ? 'One-time fitting room preparation' : 'Waiting to begin';
    detail = job?.stage === 'preparing_catalog'
      ? 'This shared setup prepares clothing previews for everyone, not once per person. Your likeness has not started yet; it begins afterward.'
      : 'Your request is saved in the queue. Your selfie has not started processing yet.';
  } else {
    title = stage?.title || 'Starting your setup';
    detail = stage?.detail || 'The worker has accepted your request. Waiting for the next reported stage.';
  }
  const message = !preview && !failed && (!queued || job?.stage === 'preparing_catalog')
    && job?.message && job.message !== title && job.message !== detail ? job.message : undefined;
  const steps = enrollmentSteps.map((step, index) => ({ ...step,
    state: (completed ? 'done' : stage && !preview ? index < stage.index ? 'done' : index === stage.index ? 'current' : 'pending' : 'pending') as StepState,
  }));
  return { title, detail, message, queued, failed, completed, active, counter, elapsed, updateAge, stale, noWorkerUpdateYet, steps };
}
