import { describe, expect, it } from 'vitest';
import { Timestamp } from 'firebase/firestore';
import type { Generation, Job } from '../web/src/lib/client';
import { selectTryOnResult } from '../web/src/lib/tryon-result';

const time = (millis: number) => Timestamp.fromMillis(millis);
const priorJob: Job = { id: 'prior-job', uid: 'person', kind: 'generate', garmentId: 'prior-shirt', status: 'completed', createdAt: time(1000) };
const currentJob: Job = { id: 'current-job', uid: 'person', kind: 'generate', garmentId: 'current-shirt', status: 'running', createdAt: time(3000) };
const priorGeneration: Generation = {
  id: priorJob.id, garmentId: 'prior-shirt', garmentName: 'Prior shirt', status: 'completed',
  imagePath: 'users/person/generations/prior-job/result.png', createdAt: time(1500),
};
const currentGeneration: Generation = {
  id: currentJob.id, garmentId: 'current-shirt', garmentName: 'Current shirt', status: 'completed',
  imagePath: 'users/person/generations/current-job/result.png', createdAt: time(3500),
};

describe('Current try-on result', () => {
  it('is empty without a try-on, ignoring enrollment and training jobs', () => {
    expect(selectTryOnResult([], [])).toEqual({ status: 'empty' });
    expect(selectTryOnResult([{ ...currentJob, kind: 'enroll' }, { ...priorJob, kind: 'train' }], [])).toEqual({ status: 'empty' });
  });

  it('waits for an explicit request instead of displaying an earlier outfit', () => {
    const result = selectTryOnResult([priorJob], [priorGeneration], currentJob.id);
    expect(result.status).toBe('pending');
    expect(result.job).toBeUndefined();
    expect(result.generation).toBeUndefined();
  });

  it('keeps the explicit request selected even when newer unrelated history arrives', () => {
    const result = selectTryOnResult([currentJob, priorJob], [currentGeneration, priorGeneration], priorJob.id);
    expect(result).toEqual({ job: priorJob, generation: priorGeneration, status: 'completed' });
  });

  it.each(['queued', 'running', 'completed'] as const)('does not show the prior garment while the newest %s job has no history', status => {
    const job = { ...currentJob, status };
    const result = selectTryOnResult([job, priorJob], [priorGeneration]);
    expect(result).toEqual({ job, generation: undefined, status: 'pending' });
  });

  it('does not reuse an earlier image of the same garment', () => {
    const repeat = { ...currentJob, garmentId: priorJob.garmentId };
    expect(selectTryOnResult([repeat, priorJob], [priorGeneration], repeat.id).generation).toBeUndefined();
  });

  it('waits when job completion arrives before completed history', () => {
    const job = { ...currentJob, status: 'completed' as const };
    const history = { ...currentGeneration, status: 'running', imagePath: undefined };
    expect(selectTryOnResult([job, priorJob], [history, priorGeneration], job.id))
      .toEqual({ job, generation: history, status: 'pending' });
  });

  it.each(['queued', 'running'] as const)('shows completed matching history while the job is still reported as %s', status => {
    const job = { ...currentJob, status };
    expect(selectTryOnResult([job, priorJob], [currentGeneration, priorGeneration], job.id))
      .toEqual({ job, generation: currentGeneration, status: 'completed' });
  });

  it('shows matching completed history before the requested job snapshot arrives', () => {
    expect(selectTryOnResult([priorJob], [currentGeneration, priorGeneration], currentJob.id))
      .toEqual({ job: undefined, generation: currentGeneration, status: 'completed' });
  });

  it('uses newer generation history when the jobs subscription is behind', () => {
    expect(selectTryOnResult([priorJob], [currentGeneration, priorGeneration]))
      .toEqual({ job: undefined, generation: currentGeneration, status: 'completed' });
  });

  it('can show history after the job has left the limited jobs subscription', () => {
    expect(selectTryOnResult([], [currentGeneration, priorGeneration]))
      .toEqual({ job: undefined, generation: currentGeneration, status: 'completed' });
  });

  it('does not report a completed outfit until the matching image path exists', () => {
    const generation = { ...currentGeneration, imagePath: undefined };
    expect(selectTryOnResult([{ ...currentJob, status: 'completed' }], [generation]).status).toBe('pending');
  });

  it('surfaces a job failure before failure history arrives', () => {
    const job = { ...currentJob, status: 'failed' as const, error: 'This garment is unavailable.' };
    expect(selectTryOnResult([job, priorJob], [priorGeneration]))
      .toEqual({ job, generation: undefined, status: 'failed' });
  });

  it('surfaces a history failure before the job snapshot catches up', () => {
    const generation = { ...currentGeneration, status: 'failed', imagePath: undefined, error: 'Could not create this look.' };
    expect(selectTryOnResult([currentJob, priorJob], [generation, priorGeneration]))
      .toEqual({ job: currentJob, generation, status: 'failed' });
  });

  it('keeps explicit failure visible even with conflicting completed history', () => {
    expect(selectTryOnResult([{ ...currentJob, status: 'failed' }], [currentGeneration]).status).toBe('failed');
  });

  it('prefers the current request when timestamps are not yet comparable', () => {
    const job = { ...currentJob, createdAt: undefined };
    expect(selectTryOnResult([job, priorJob], [priorGeneration]))
      .toEqual({ job, generation: undefined, status: 'pending' });
  });

  it('resolves equal timestamps in favor of the newest request', () => {
    const generation = { ...priorGeneration, createdAt: currentJob.createdAt };
    expect(selectTryOnResult([currentJob, priorJob], [generation]).generation).toBeUndefined();
  });
});
