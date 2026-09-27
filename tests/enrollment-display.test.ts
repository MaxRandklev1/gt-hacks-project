import { describe, expect, it } from 'vitest';
import { Timestamp } from 'firebase/firestore';
import { durationText, enrollmentDisplay, timestampMillis } from '../web/src/lib/enrollment-display';
import type { Job } from '../web/src/lib/client';

const now = Date.parse('2026-09-27T08:00:00Z');
const queued: Job = { id: 'new-look', uid: 'person', kind: 'enroll', status: 'queued', createdAt: Timestamp.fromMillis(now - 75_000) };
const running: Job = { ...queued, status: 'running', stage: 'creating_look', updatedAt: Timestamp.fromMillis(now - 10_000) };

describe('Truthful enrollment progress', () => {
  it('keeps every personal stage pending while queued, even with stale rendering fields', () => {
    const state = enrollmentDisplay({ ...queued, stage: 'creating_look', message: 'Creating your look', progress: 0.9, sampling: { step: 20, total: 24 } }, now);
    expect(state.title).toBe('Waiting to begin');
    expect(state.steps.every(step => step.state === 'pending')).toBe(true);
    expect(state.counter).toBeUndefined();
    expect(state.message).toBeUndefined();
    expect(state.elapsed).toBe(75_000);
  });
  it('labels shared preparation separately and never starts likeness from its counter', () => {
    const state = enrollmentDisplay({ ...queued, stage: 'preparing_catalog', preparation: { completed: 8, total: 20 }, progress: null }, now);
    expect(state.title).toBe('One-time fitting room preparation');
    expect(state.counter).toEqual({ value: 8, total: 20, label: 'Shared clothing previews' });
    expect(state.detail).toContain('not once per person');
    expect(state.detail).toContain('Your likeness has not started yet');
    expect(state.steps.every(step => step.state === 'pending')).toBe(true);
  });
  it.each([
    ['checking_selfie', 0], ['checking_catalog', 1], ['preparing_reference', 2], ['creating_look', 2],
    ['upscaling_look', 3], ['fitting_look', 3], ['saving_look', 4],
  ])('maps reported %s to the checklist without advancing from elapsed time', (stage, current) => {
    const state = enrollmentDisplay({ ...running, stage }, now + 600_000);
    expect(state.steps.map(step => step.state)).toEqual(Array.from({ length: 5 }, (_, i) => i < Number(current) ? 'done' : i === current ? 'current' : 'pending'));
    expect(state.counter).toBeUndefined();
  });
  it('does not turn milestone percentages or unknown stages into a measured progress bar', () => {
    const state = enrollmentDisplay({ ...running, stage: 'new_worker_stage', progress: 0.85 }, now);
    expect(state.steps.every(step => step.state === 'pending')).toBe(true);
    expect(state.counter).toBeUndefined();
    expect(state.title).toBe('Starting your setup');
  });
  it('shows actual sampling only for the image-creation stage', () => {
    expect(enrollmentDisplay({ ...running, sampling: { step: 7, total: 24 } }, now).counter)
      .toEqual({ value: 7, total: 24, label: 'Image creation steps' });
    expect(enrollmentDisplay({ ...running, stage: 'fitting_look', sampling: { step: 24, total: 24 } }, now).counter).toBeUndefined();
  });
  it.each([{ completed: -1, total: 20 }, { completed: 21, total: 20 }, { completed: 1, total: 0 }, { completed: 0.5, total: 20 }, { completed: NaN, total: 20 }])('rejects malformed counters %j', preparation => {
    expect(enrollmentDisplay({ ...queued, stage: 'preparing_catalog', preparation }, now).counter).toBeUndefined();
  });
  it('shows completed processing while the matching profile snapshot catches up', () => {
    const state = enrollmentDisplay({ ...running, status: 'completed', updatedAt: Timestamp.fromMillis(now - 5_000) }, now, true);
    expect(state.title).toBe('Your look is saved');
    expect(state.detail).toContain('Waiting for your account');
    expect(state.steps.every(step => step.state === 'done')).toBe(true);
    expect(state.elapsed).toBe(70_000);
    expect(state.active).toBe(false);
  });
  it('shows the reported failure and stops timers without claiming stages completed', () => {
    const state = enrollmentDisplay({ ...running, status: 'failed', error: 'No clear face.', updatedAt: Timestamp.fromMillis(now - 2_000) }, now + 900_000);
    expect(state.failed).toBe(true);
    expect(state.detail).toBe('No clear face.');
    expect(state.elapsed).toBe(73_000);
    expect(state.stale).toBe(false);
    expect(state.counter).toBeUndefined();
  });
  it('uses the latest worker heartbeat for update age without making stale status a failure', () => {
    const fresh = enrollmentDisplay({ ...running, updatedAt: Timestamp.fromMillis(now - 180_000), workerSeenAt: Timestamp.fromMillis(now - 3_000) }, now);
    expect(fresh.stale).toBe(false);
    const stale = enrollmentDisplay({ ...running, updatedAt: Timestamp.fromMillis(now - 120_000) }, now);
    expect(stale.stale).toBe(true);
    expect(stale.failed).toBe(false);
    expect(stale.active).toBe(true);
  });
  it('does not invent times or a heartbeat before server timestamps arrive', () => {
    const state = enrollmentDisplay(undefined, now);
    expect(state.title).toBe('Waiting to begin');
    expect(state.elapsed).toBeUndefined();
    expect(state.updateAge).toBeUndefined();
    expect(state.stale).toBe(false);
  });
  it('flags a long queued wait with no worker update without inventing a heartbeat', () => {
    const state = enrollmentDisplay({ ...queued, createdAt: Timestamp.fromMillis(now - 120_000) }, now);
    expect(state.noWorkerUpdateYet).toBe(true);
    expect(state.elapsed).toBe(120_000);
    expect(state.updateAge).toBeUndefined();
    expect(state.stale).toBe(false);
    expect(state.failed).toBe(false);
    expect(enrollmentDisplay({ ...queued, createdAt: Timestamp.fromMillis(now - 119_000) }, now).noWorkerUpdateYet).toBe(false);
  });
  it('does not show the no-worker hint once there is evidence of processing, or in a terminal job or preview', () => {
    const old = { ...queued, createdAt: Timestamp.fromMillis(now - 600_000) };
    expect(enrollmentDisplay({ ...old, updatedAt: Timestamp.fromMillis(now - 5_000) }, now).noWorkerUpdateYet).toBe(false);
    expect(enrollmentDisplay({ ...old, workerSeenAt: Timestamp.fromMillis(now - 5_000) }, now).noWorkerUpdateYet).toBe(false);
    expect(enrollmentDisplay({ ...old, status: 'running' }, now).noWorkerUpdateYet).toBe(false);
    expect(enrollmentDisplay({ ...old, status: 'completed' }, now).noWorkerUpdateYet).toBe(false);
    expect(enrollmentDisplay({ ...old, status: 'failed' }, now).noWorkerUpdateYet).toBe(false);
    expect(enrollmentDisplay(old, now, false, true).noWorkerUpdateYet).toBe(false);
    expect(enrollmentDisplay(undefined, now).noWorkerUpdateYet).toBe(false);
  });
  it('keeps the design preview inert even if supplied a real job', () => {
    const state = enrollmentDisplay({ ...running, sampling: { step: 7, total: 24 } }, now, false, true);
    expect(state.active).toBe(false);
    expect(state.elapsed).toBeUndefined();
    expect(state.counter).toBeUndefined();
    expect(state.steps.every(step => step.state === 'pending')).toBe(true);
    expect(state.title).toBe('A look at the setup process');
  });
});

describe('Elapsed-time display', () => {
  it('accepts Firestore timestamps and rejects invalid or throwing values', () => {
    expect(timestampMillis(Timestamp.fromMillis(now))).toBe(now);
    expect(timestampMillis(new Date(now))).toBe(now);
    expect(timestampMillis('not a date')).toBeUndefined();
    expect(timestampMillis({ toMillis: () => { throw new Error('bad timestamp'); } })).toBeUndefined();
    expect(timestampMillis({ toMillis: () => NaN })).toBeUndefined();
  });
  it('formats seconds, minutes and long waits without implying time remaining', () => {
    expect(durationText(0)).toBe('0s');
    expect(durationText(75_900)).toBe('1m 15s');
    expect(durationText(3_720_000)).toBe('1h 02m');
    expect(durationText(-1_000)).toBe('0s');
  });
});
