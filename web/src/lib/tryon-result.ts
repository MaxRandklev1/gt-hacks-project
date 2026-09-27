import type { Generation, Job } from './client';
import { timestampMillis } from './enrollment-display';

export type TryOnResult = {
  job?: Job;
  generation?: Generation;
  status: 'empty' | 'pending' | 'completed' | 'failed';
};

/** Job and history subscriptions are newest-first; history document IDs equal job IDs. */
export function selectTryOnResult(jobs: Job[], generations: Generation[], requestedJobId?: string): TryOnResult {
  const latestJob = jobs.find(job => job.kind === 'generate');
  const latestGeneration = generations[0];
  let selectedId = requestedJobId;

  if (!selectedId) {
    const jobTime = timestampMillis(latestJob?.createdAt);
    const generationTime = timestampMillis(latestGeneration?.createdAt);
    // Without comparable timestamps, a known request takes precedence over history.
    selectedId = latestJob && !(generationTime !== undefined && jobTime !== undefined && generationTime > jobTime)
      ? latestJob.id : latestGeneration?.id;
  }
  if (!selectedId) return { status: 'empty' };

  // Never substitute another garment's history while this request catches up.
  const job = jobs.find(item => item.kind === 'generate' && item.id === selectedId);
  const generation = generations.find(item => item.id === selectedId);
  const status = job?.status === 'failed' || generation?.status === 'failed' ? 'failed'
    : generation?.status === 'completed' && generation.imagePath ? 'completed' : 'pending';
  return { job, generation, status };
}
