import { readFileSync } from 'node:fs';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { assertFails, assertSucceeds, initializeTestEnvironment, type RulesTestEnvironment } from '@firebase/rules-unit-testing';
import firebase from 'firebase/compat/app';
import 'firebase/compat/firestore';
import 'firebase/compat/storage';

const PROJECT = 'demo-thread-tryon';
const ALICE = 'alice';
const BOB = 'bob';
const UPLOAD_ID = '11111111-1111-4111-8111-111111111111';
const SECOND_UPLOAD_ID = '22222222-2222-4222-8222-222222222222';
const stamp = () => firebase.firestore.FieldValue.serverTimestamp();
const capturedAt = (offsetMs = 0) => firebase.firestore.Timestamp.fromMillis(Date.now() + offsetMs);
let env: RulesTestEnvironment;

function endpoint(value: string | undefined, defaultPort: number) {
  const url = new URL(`http://${value || `127.0.0.1:${defaultPort}`}`);
  return { host: url.hostname, port: Number(url.port) };
}

beforeAll(async () => {
  env = await initializeTestEnvironment({
    projectId: PROJECT,
    firestore: { ...endpoint(process.env.FIRESTORE_EMULATOR_HOST, 8080), rules: readFileSync(new URL('../firestore.rules', import.meta.url), 'utf8') },
    storage: { ...endpoint(process.env.FIREBASE_STORAGE_EMULATOR_HOST, 9199), rules: readFileSync(new URL('../storage.rules', import.meta.url), 'utf8') },
  });
}, 60_000);

beforeEach(async () => {
  await env.clearFirestore();
  // rules-unit-testing 5.0.2 clearStorage() skips nested listAll().prefixes.
  // Delete only this explicit local emulator test bucket, including user paths.
  await env.withSecurityRulesDisabled(async context => {
    async function removeTree(reference: firebase.storage.Reference): Promise<void> {
      const { items, prefixes } = await reference.listAll();
      await Promise.all([...items.map(item => item.delete()), ...prefixes.map(removeTree)]);
    }
    await removeTree(context.storage().ref());
  });
});

afterAll(async () => { await env?.cleanup(); });

const dbFor = (uid = ALICE) => env.authenticatedContext(uid).firestore();
const userData = () => ({ displayName: 'Test person', email: 'test@example.invalid', photoURL: '', createdAt: stamp(), updatedAt: stamp() });
const paths = (uid = ALICE, uploadId = UPLOAD_ID) => Array.from({ length: 8 }, (_, index) => `users/${uid}/uploads/${uploadId}/${index}.jpg`);
const selfiePathFor = (uid = ALICE, uploadId = UPLOAD_ID) => `users/${uid}/uploads/${uploadId}/selfie.jpg`;
const train = (overrides: Record<string, unknown> = {}) => ({
  uid: ALICE, kind: 'train', status: 'queued', requestVersion: 2, createdAt: stamp(),
  uploadId: UPLOAD_ID, photoPaths: paths(), selfiePath: selfiePathFor(), selfieCapturedAt: capturedAt(), ...overrides,
});
const trainV3 = (selfieSource: 'camera' | 'upload' = 'camera', overrides: Record<string, unknown> = {}) => {
  const selected = capturedAt();
  const payload: Record<string, unknown> = train({
    requestVersion: 3, selfieSource, selfieSelectedAt: selected, selfieCapturedAt: selected,
  });
  if (selfieSource === 'upload') delete payload.selfieCapturedAt;
  return { ...payload, ...overrides };
};
const generate = (overrides: Record<string, unknown> = {}) => ({
  uid: ALICE, kind: 'generate', status: 'queued', requestVersion: 2, createdAt: stamp(), garmentId: 'coat-1', ...overrides,
});

async function seed(documents: Record<string, Record<string, unknown>>) {
  await env.withSecurityRulesDisabled(async context => {
    const db = context.firestore();
    const batch = db.batch();
    for (const [path, data] of Object.entries(documents)) batch.set(db.doc(path), data);
    await batch.commit();
  });
}

async function seedProfile(uid = ALICE, extra: Record<string, unknown> = {}) {
  await seed({ [`users/${uid}`]: {
    ...userData(), heightCm: 180, weightKg: 75, consentVersion: 'identity-training-v1', consentAt: stamp(), ...extra,
  } });
}

async function enqueue(jobId: string, payload: Record<string, unknown> = train(), uid = ALICE) {
  const db = dbFor(uid);
  const batch = db.batch();
  batch.set(db.doc(`jobs/${jobId}`), payload);
  batch.set(db.doc(`users/${uid}/queue/current`), { jobId, updatedAt: stamp() });
  return batch.commit();
}

describe('Firestore ownership and protected fields', () => {
  it('allows ordinary owner profile creation/update, but blocks other users and anonymous access', async () => {
    const own = dbFor().doc(`users/${ALICE}`);
    await assertSucceeds(own.set(userData()));
    await assertSucceeds(own.update({ displayName: 'Updated name', updatedAt: stamp() }));
    await assertSucceeds(own.get());
    await assertFails(dbFor(BOB).doc(`users/${ALICE}`).get());
    await assertFails(dbFor(BOB).doc(`users/${ALICE}`).update({ displayName: 'Forged', updatedAt: stamp() }));
    await assertFails(env.unauthenticatedContext().firestore().doc(`users/${ALICE}`).get());
  });

  it('blocks ready identity and adapter forgery on create and update', async () => {
    const profile = dbFor().doc(`users/${ALICE}`);
    await assertFails(profile.set({ ...userData(), identity: { status: 'ready', loraPath: 'forged' } }));
    await assertSucceeds(profile.set(userData()));
    await assertFails(profile.update({ identity: { status: 'ready' }, updatedAt: stamp() }));
    await seedProfile(ALICE, { identity: { status: 'training', version: 'server-version' } });
    await assertFails(profile.update({ 'identity.status': 'ready', updatedAt: stamp() }));
    await assertFails(profile.update({ 'identity.loraPath': 'forged.safetensors', updatedAt: stamp() }));
    await assertFails(profile.update({ 'identity.referencePath': selfiePathFor(), updatedAt: stamp() }));
    await assertSucceeds(profile.update({ displayName: 'Allowed profile edit', updatedAt: stamp() }));
  });

  it('allows an optional US or metric preference without changing canonical measurements or protected identity', async () => {
    const profile = dbFor().doc(`users/${ALICE}`);
    await assertSucceeds(profile.set({ ...userData(), measurementSystem: 'us' }));
    await assertSucceeds(profile.update({ measurementSystem: 'metric', updatedAt: stamp() }));
    await seedProfile(ALICE, { measurementSystem: 'metric', identity: { status: 'ready', referencePath: 'server-owned-reference' } });
    await assertSucceeds(profile.update({ measurementSystem: 'us', updatedAt: stamp() }));
    const saved = (await profile.get()).data();
    expect(saved?.heightCm).toBe(180);
    expect(saved?.weightKg).toBe(75);
    expect(saved?.identity.referencePath).toBe('server-owned-reference');
    await assertFails(dbFor(BOB).doc(`users/${ALICE}`).update({ measurementSystem: 'metric', updatedAt: stamp() }));
  });

  it.each(['imperial', 'US', 1, null])('rejects unsupported measurement preference %s on create and update', async measurementSystem => {
    const profile = dbFor().doc(`users/${ALICE}`);
    await assertFails(profile.set({ ...userData(), measurementSystem }));
    await assertSucceeds(profile.set(userData()));
    await assertFails(profile.update({ measurementSystem, updatedAt: stamp() }));
  });

  it('rejects a weight-only profile update and requires measurements as a valid pair', async () => {
    const profile = dbFor().doc(`users/${ALICE}`);
    await assertSucceeds(profile.set(userData()));
    await assertFails(profile.update({ weightKg: -10, updatedAt: stamp() }));
    await assertFails(profile.update({ weightKg: 'invalid', updatedAt: stamp() }));
    await assertFails(profile.update({ weightKg: 70, updatedAt: stamp() }));
    await assertSucceeds(profile.update({ heightCm: 175, weightKg: 70, updatedAt: stamp() }));
  });

  it('isolates jobs, queue locks, and generation results by owner', async () => {
    await seedProfile();
    await enqueue('own-job');
    await seed({ [`users/${ALICE}/generations/result-1`]: { status: 'completed', imagePath: 'private/result.png' } });
    for (const path of [`jobs/own-job`, `users/${ALICE}/queue/current`, `users/${ALICE}/generations/result-1`]) {
      await assertSucceeds(dbFor().doc(path).get());
      await assertFails(dbFor(BOB).doc(path).get());
      await assertFails(env.unauthenticatedContext().firestore().doc(path).get());
    }
  });

  it('blocks client result/catalog writes and job status changes', async () => {
    await seedProfile();
    await enqueue('job-1');
    await assertFails(dbFor().doc(`users/${ALICE}/generations/result-1`).set({ status: 'completed', imagePath: 'forged.png' }));
    await assertFails(dbFor().doc('garments/coat-1').set({ active: true, imagePath: 'forged.png' }));
    await assertFails(dbFor().doc('jobs/job-1').update({ status: 'completed', resultPath: 'forged.png' }));
    await assertFails(dbFor().doc('jobs/job-1').delete());
    await assertFails(dbFor().doc(`users/${ALICE}/queue/current`).delete());
  });
});

describe('Training requests and the per-user queue lock', () => {
  beforeEach(async () => { await seedProfile(); });

  it('accepts the real atomic onboarding contract, including updated measurements and consent', async () => {
    await seed({ [`users/${ALICE}`]: userData() });
    const db = dbFor();
    const batch = db.batch();
    batch.set(db.doc(`users/${ALICE}`), { heightCm: 175, weightKg: 70, measurementSystem: 'us', consentVersion: 'identity-training-v1', consentAt: stamp(), updatedAt: stamp() }, { merge: true });
    batch.set(db.doc('jobs/onboarding'), train());
    batch.set(db.doc(`users/${ALICE}/queue/current`), { jobId: 'onboarding', updatedAt: stamp() });
    await assertSucceeds(batch.commit());
  });

  it('blocks UID forgery and jobs without their matching atomic queue lock', async () => {
    await assertFails(enqueue('forged-owner', train({ uid: BOB })));
    await assertFails(dbFor().doc('jobs/no-lock').set(train()));
    await assertFails(enqueue('forged-status', train({ status: 'completed' })));
    await assertFails(enqueue('forged-fields', train({ loraPath: 'injected.safetensors' })));
  });

  it.each([
    ['seven paths', paths().slice(0, 7)],
    ['nine paths', [...paths(), `users/${ALICE}/uploads/${UPLOAD_ID}/8.jpg`]],
    ['duplicate slot', [...paths().slice(0, 7), paths()[0]]],
    ['another owner', paths(BOB)],
    ['another upload batch', paths(ALICE, SECOND_UPLOAD_ID)],
    ['wrong extension', paths().map(path => path.replace('.jpg', '.png'))],
    ['reordered slots', [...paths()].reverse()],
  ])('rejects %s', async (_label, photoPaths) => {
    await assertFails(enqueue('bad-paths', train({ photoPaths })));
  });

  it.each(['../escape', 'batch-1', 'ABCDEFAB-1234-4234-8234-123456789ABC'])('rejects noncanonical upload ID %s even when all paths match', async uploadId => {
    await assertFails(enqueue('invalid-upload', train({ uploadId, photoPaths: paths(ALICE, uploadId), selfiePath: selfiePathFor(ALICE, uploadId) })));
  });

  it.each(['selfiePath', 'selfieCapturedAt'])('requires %s on every new training request', async field => {
    const payload: Record<string, unknown> = train();
    delete payload[field];
    await assertFails(enqueue('missing-selfie', payload));
  });

  it.each([
    ['another owner', selfiePathFor(BOB)],
    ['another upload batch', selfiePathFor(ALICE, SECOND_UPLOAD_ID)],
    ['one of the eight training photos', paths()[0]],
    ['wrong extension', selfiePathFor().replace('.jpg', '.png')],
    ['non-string', 123],
  ])('rejects a selfie path for %s', async (_label, selfiePath) => {
    await assertFails(enqueue('invalid-selfie-path', train({ selfiePath })));
  });

  it.each([
    ['older than one hour', -61 * 60_000],
    ['beyond future clock tolerance', 3 * 60_000],
  ])('rejects a capture timestamp %s', async (_label, offsetMs) => {
    await assertFails(enqueue('invalid-capture-time', train({ selfieCapturedAt: capturedAt(Number(offsetMs)) })));
  });

  it.each([-59 * 60_000, 90_000])('accepts capture time within the freshness window (%s ms)', async offsetMs => {
    await assertSucceeds(enqueue('valid-capture-time', train({ selfieCapturedAt: capturedAt(offsetMs) })));
  });

  it.each(['2026-09-26T12:00:00Z', 123456789, null])('rejects a non-timestamp capture value %s', async selfieCapturedAt => {
    await assertFails(enqueue('wrong-capture-type', train({ selfieCapturedAt })));
  });

  it.each([1, 4, '2', '3', 2.5, 3.5, null, true])('rejects new training request version %s', async requestVersion => {
    await assertFails(enqueue('wrong-train-version', train({ requestVersion })));
  });

  it('prevents changing a queued job to substitute a selfie or refresh its capture time', async () => {
    await assertSucceeds(enqueue('immutable-selfie'));
    const job = dbFor().doc('jobs/immutable-selfie');
    await assertFails(job.update({ selfiePath: selfiePathFor(ALICE, SECOND_UPLOAD_ID) }));
    await assertFails(job.update({ selfieCapturedAt: capturedAt() }));
  });

  it('requires valid measurements and current consent', async () => {
    await seedProfile(ALICE, { heightCm: 20 });
    await assertFails(enqueue('bad-height'));
    await seedProfile(ALICE, { consentVersion: 'outdated' });
    await assertFails(enqueue('bad-consent'));
  });

  it.each(['queued', 'running'])('cannot replace a lock while its job is %s', async status => {
    await seed({ 'jobs/previous': { uid: ALICE, kind: 'train', requestVersion: 1, status }, [`users/${ALICE}/queue/current`]: { jobId: 'previous', updatedAt: stamp() } });
    await assertSucceeds(dbFor().doc('jobs/previous').get());
    await assertFails(enqueue('next-job'));
  });

  it.each(['completed', 'failed'])('can queue again after its previous job is %s', async status => {
    await seed({ 'jobs/previous': { uid: ALICE, kind: 'train', requestVersion: 1, status }, [`users/${ALICE}/queue/current`]: { jobId: 'previous', updatedAt: stamp() } });
    await assertSucceeds(dbFor().doc('jobs/previous').get());
    await assertSucceeds(enqueue('next-job'));
  });

  it('admits only one of two concurrently submitted jobs', async () => {
    const results = await Promise.allSettled([enqueue('race-a'), enqueue('race-b')]);
    expect(results.filter(result => result.status === 'fulfilled')).toHaveLength(1);
    expect(results.filter(result => result.status === 'rejected')).toHaveLength(1);
    const lock = await dbFor().doc(`users/${ALICE}/queue/current`).get();
    expect(['race-a', 'race-b']).toContain(lock.data()?.jobId);
  });
});

describe('Version-3 camera and uploaded references', () => {
  beforeEach(async () => { await seedProfile(); });

  it.each(['camera', 'upload'] as const)('accepts an owner %s reference with a recent selection timestamp', async source => {
    await assertSucceeds(enqueue('reference-v3', trainV3(source)));
  });

  it('retains the strict version-2 camera payload for cached clients', async () => {
    await assertSucceeds(enqueue('legacy-camera-v2', train()));
  });

  it.each([
    { selfieSource: 'camera' },
    { selfieSource: 'upload' },
    { selfieSelectedAt: capturedAt() },
  ])('rejects mixing version-3 fields into the strict version-2 payload (%j)', async extra => {
    await assertFails(enqueue('mixed-v2-fields', train(extra)));
  });

  it.each(['selfiePath', 'selfieSource', 'selfieSelectedAt'] as const)('requires version-3 field %s for both sources', async field => {
    for (const source of ['camera', 'upload'] as const) {
      const payload: Record<string, unknown> = trainV3(source);
      delete payload[field];
      await assertFails(enqueue(`missing-${source}`, payload));
    }
  });

  it.each(['library', 'Camera', '', 1, null])('rejects unsupported reference source %s', async selfieSource => {
    await assertFails(enqueue('invalid-source', trainV3('upload', { selfieSource })));
  });

  it.each(['2026-09-26T12:00:00Z', 123456789, null])('rejects non-timestamp selection %s', async selfieSelectedAt => {
    await assertFails(enqueue('invalid-selection-type', trainV3('upload', { selfieSelectedAt })));
  });

  it.each([-61 * 60_000, 3 * 60_000])('rejects selection outside the fresh window (%s ms)', async offset => {
    for (const source of ['camera', 'upload'] as const) {
      const time = capturedAt(offset);
      await assertFails(enqueue(`invalid-selection-${source}`, trainV3(source, {
        selfieSelectedAt: time, ...(source === 'camera' ? { selfieCapturedAt: time } : {}),
      })));
    }
  });

  it.each([-59 * 60_000, 90_000])('accepts upload selection within clock tolerance (%s ms), without claiming a capture date', async offset => {
    await assertSucceeds(enqueue('recent-upload-selection', trainV3('upload', { selfieSelectedAt: capturedAt(offset) })));
  });

  it('requires camera capture time and permits capture before or at selection', async () => {
    const selected = capturedAt();
    await assertSucceeds(enqueue('camera-before-selection', trainV3('camera', {
      selfieCapturedAt: capturedAt(-30_000), selfieSelectedAt: selected,
    })));
  });

  it('rejects a camera payload without a capture timestamp', async () => {
    const payload = trainV3();
    delete payload.selfieCapturedAt;
    await assertFails(enqueue('missing-camera-capture', payload));
  });

  it.each(['2026-09-26T12:00:00Z', 123456789, null])('rejects non-timestamp camera capture %s', async selfieCapturedAt => {
    await assertFails(enqueue('invalid-camera-capture-type', trainV3('camera', { selfieCapturedAt })));
  });

  it.each([-61 * 60_000, 3 * 60_000])('rejects camera capture outside the fresh window (%s ms)', async offset => {
    await assertFails(enqueue('invalid-camera-capture', trainV3('camera', { selfieCapturedAt: capturedAt(offset) })));
  });

  it('rejects a camera capture after selection even when both are fresh', async () => {
    const selected = capturedAt();
    await assertFails(enqueue('capture-after-selection', trainV3('camera', {
      selfieSelectedAt: selected, selfieCapturedAt: firebase.firestore.Timestamp.fromMillis(selected.toMillis() + 1000),
    })));
  });

  it.each([null, 'unknown', 123456789])('forbids capture-time field on uploaded references even when its value is %s', async selfieCapturedAt => {
    await assertFails(enqueue('upload-claims-capture', trainV3('upload', { selfieCapturedAt })));
  });

  it('rejects uploaded references with a forged fresh capture timestamp', async () => {
    await assertFails(enqueue('upload-forged-capture', trainV3('upload', { selfieCapturedAt: capturedAt() })));
  });

  it.each(['camera', 'upload'] as const)('keeps owner, batch and photo-slot boundaries for %s references', async source => {
    for (const selfiePath of [selfiePathFor(BOB), selfiePathFor(ALICE, SECOND_UPLOAD_ID), paths()[0]]) {
      await assertFails(enqueue('wrong-reference-owner', trainV3(source, { selfiePath })));
    }
    await assertFails(enqueue('wrong-uid', trainV3(source, { uid: BOB })));
    await assertFails(enqueue('wrong-photo-count', trainV3(source, { photoPaths: paths().slice(0, 7) })));
    await assertFails(enqueue('extra-field', trainV3(source, { referenceCapturedAt: capturedAt() })));
    await assertFails(enqueue('forged-submission-time', trainV3(source, { createdAt: capturedAt(-30_000) })));
  });

  it('keeps reference source and selection immutable and preserves one active job per user', async () => {
    await assertSucceeds(enqueue('camera-first', trainV3('camera')));
    await assertFails(enqueue('upload-second', trainV3('upload')));
    await assertFails(dbFor().doc('jobs/camera-first').update({ selfieSource: 'upload' }));
    await assertFails(dbFor().doc('jobs/camera-first').update({ selfieSelectedAt: capturedAt() }));
  });
});

describe('Generation requirements', () => {
  it.each([2, 3, 4, 5])('accepts version %s generation with a server-ready identity and active garment', async requestVersion => {
    await seedProfile(ALICE, { identity: { status: 'ready', version: 'trained' } });
    await seed({ 'garments/coat-1': { active: true } });
    await assertSucceeds(enqueue('generation', generate({ requestVersion })));
  });

  it.each([1, 6, '2', '3', '4', '5', 2.5, 3.5, null, true])('rejects new generation request version %s', async requestVersion => {
    await seedProfile(ALICE, { identity: { status: 'ready', version: 'trained' } });
    await seed({ 'garments/coat-1': { active: true } });
    await assertFails(enqueue('wrong-generation-version', generate({ requestVersion })));
  });

  it.each(['training', 'failed', 'selecting', 'awaiting_reference'])('blocks generation for identity status %s', async status => {
    await seedProfile(ALICE, { identity: { status } });
    await seed({ 'garments/coat-1': { active: true } });
    await assertFails(enqueue('not-ready', generate()));
  });

  it('blocks missing identity, inactive garments, and nonexistent garments', async () => {
    await seedProfile();
    await seed({ 'garments/coat-1': { active: true } });
    await assertFails(enqueue('no-identity', generate()));
    await seedProfile(ALICE, { identity: { status: 'ready' } });
    await seed({ 'garments/coat-1': { active: false } });
    await assertFails(enqueue('inactive', generate()));
    await assertFails(enqueue('missing', generate({ garmentId: 'missing' })));
  });
});

const photoPath = `users/${ALICE}/uploads/${UPLOAD_ID}/0.jpg`;

describe('Photos-first onboarding and deferred reference finalization', () => {
  const photoTrain = (extra: Record<string, unknown> = {}) => ({ uid: ALICE, kind: 'train', status: 'queued', requestVersion: 4, createdAt: stamp(), uploadId: UPLOAD_ID, photoPaths: paths(), ...extra });
  const draft = (extra: Record<string, unknown> = {}) => ({ trainingJobId: 'early-train', uploadId: SECOND_UPLOAD_ID, selfiePath: selfiePathFor(ALICE, SECOND_UPLOAD_ID), selfieSource: 'upload', selfieSelectedAt: capturedAt(), submittedAt: stamp(), ...extra });
  const finalJob = (extra: Record<string, unknown> = {}) => ({ uid: ALICE, kind: 'finalize', status: 'queued', requestVersion: 4, createdAt: stamp(), trainingJobId: 'early-train', ...extra });
  const draftRef = () => dbFor().doc(`users/${ALICE}/onboarding/current`);
  async function start() {
    await seed({ [`users/${ALICE}`]: { ...userData(), consentVersion: 'identity-training-v1', consentAt: stamp() } });
    await enqueue('early-train', photoTrain());
  }
  async function completed() {
    await start();
    await seedProfile(ALICE, { trainingJobId: 'early-train', measurementSystem: 'us', identity: { status: 'awaiting_reference', version: 'early-train' } });
    await seed({ 'jobs/early-train': photoTrain({ status: 'completed' }) });
    await draftRef().set(draft());
  }

  it('starts training from eight photos and consent without measurements or a selfie', async () => {
    await seed({ [`users/${ALICE}`]: userData() });
    const db = dbFor(); const batch = db.batch();
    batch.set(db.doc(`users/${ALICE}`), { consentVersion: 'identity-training-v1', consentAt: stamp(), updatedAt: stamp() }, { merge: true });
    batch.set(db.doc('jobs/early-train'), photoTrain());
    batch.set(db.doc(`users/${ALICE}/queue/current`), { jobId: 'early-train', updatedAt: stamp() });
    await assertSucceeds(batch.commit());
    const profile = (await db.doc(`users/${ALICE}`).get()).data();
    expect(profile).not.toHaveProperty('heightCm');
  });
  it('requires consent before any early training and keeps step counts server-owned', async () => {
    await seed({ [`users/${ALICE}`]: userData() });
    await assertFails(enqueue('no-consent', photoTrain()));
    await seedProfile();
    await assertFails(enqueue('injected-steps', photoTrain({ steps: 4000 })));
    await assertFails(enqueue('injected-selfie', photoTrain({ selfiePath: selfiePathFor() })));
  });
  it('retains eight-photo owner, batch and unique slot restrictions', async () => {
    await seedProfile();
    for (const photoPaths of [paths(BOB), paths().slice(0, 7), [...paths().slice(0, 7), paths()[0]]]) await assertFails(enqueue('bad-photos', photoTrain({ photoPaths })));
  });
  it.each(['queued', 'running', 'completed'])('allows measurements and a saved selfie while training is %s', async status => {
    await start(); await seed({ 'jobs/early-train': photoTrain({ status }) });
    await assertSucceeds(dbFor().doc(`users/${ALICE}`).update({ heightCm: 177.8, weightKg: 70, measurementSystem: 'us', updatedAt: stamp() }));
    await assertSucceeds(draftRef().set(draft()));
    await assertSucceeds(draftRef().get());
    await assertFails(dbFor(BOB).doc(`users/${ALICE}/onboarding/current`).get());
  });
  it('keeps camera provenance and freshness on drafts', async () => {
    await start(); const time = capturedAt();
    await assertSucceeds(draftRef().set(draft({ selfieSource: 'camera', selfieSelectedAt: time, selfieCapturedAt: time })));
    await assertFails(draftRef().set(draft({ selfieSelectedAt: capturedAt(-3_700_000) })));
    await assertFails(draftRef().set(draft({ selfieCapturedAt: time })));
    await assertFails(draftRef().set(draft({ selfieSource: 'camera' })));
    await assertFails(draftRef().set(draft({ submittedAt: capturedAt(-30_000) })));
  });
  it('rejects wrong owner, training version, selfie path, and arbitrary draft documents', async () => {
    await start();
    await assertFails(draftRef().set(draft({ selfiePath: selfiePathFor(BOB, SECOND_UPLOAD_ID) })));
    await assertFails(draftRef().set(draft({ selfiePath: paths()[0] })));
    await assertFails(draftRef().set(draft({ trainingJobId: 'missing' })));
    await assertFails(dbFor(BOB).doc(`users/${ALICE}/onboarding/current`).set(draft()));
    await assertFails(dbFor().doc(`users/${ALICE}/onboarding/other`).set(draft()));
    await assertFails(draftRef().set(draft({ identity: { status: 'ready' } })));
    await seed({ 'jobs/early-train': photoTrain({ requestVersion: 3 }) });
    await assertFails(draftRef().set(draft()));
  });
  it('does not let an older training draft replace the current onboarding', async () => {
    await start();
    await seed({ 'jobs/new-train': photoTrain(), [`users/${ALICE}/queue/current`]: { jobId: 'new-train', updatedAt: stamp() } });
    await assertFails(draftRef().set(draft()));
  });
  it('finalizes after either order: questions first or training first', async () => {
    await completed();
    await assertSucceeds(enqueue('finish', finalJob()));
    await assertFails(draftRef().set(draft())); // frozen while consumed
    await assertFails(draftRef().delete());
    await assertFails(enqueue('duplicate', finalJob()));
  });
  it('does not expire a saved reference while training was in the queue', async () => {
    await completed();
    await seed({ [`users/${ALICE}/onboarding/current`]: draft({ selfieSelectedAt: capturedAt(-8_000_000), submittedAt: capturedAt(-8_000_000) }) });
    await assertSucceeds(enqueue('delayed-finish', finalJob()));
  });
  it.each(['queued', 'running', 'failed'])('refuses finalization while parent training is %s', async status => {
    await completed(); await seed({ 'jobs/early-train': photoTrain({ status }) });
    await assertFails(enqueue('too-early', finalJob()));
  });
  it('requires measurements, the matching draft, and the current server-owned trained identity', async () => {
    await completed(); await seedProfile(ALICE, { trainingJobId: 'early-train', identity: { status: 'awaiting_reference', version: 'early-train' }, heightCm: 20 });
    await assertFails(enqueue('bad-measurements', finalJob()));
    await seedProfile(ALICE, { trainingJobId: 'new-train', identity: { status: 'awaiting_reference', version: 'new-train' } });
    await assertFails(enqueue('stale-training', finalJob()));
    await seedProfile(ALICE, { trainingJobId: 'early-train', measurementSystem: 'us', identity: { status: 'awaiting_reference', version: 'early-train' } });
    await seed({ [`users/${ALICE}/onboarding/current`]: draft({ trainingJobId: 'other' }) });
    await assertFails(enqueue('wrong-draft', finalJob()));
  });
  it('refuses forged finalize inputs or parent ownership', async () => {
    await completed();
    await assertFails(enqueue('extra-finalize', finalJob({ selfiePath: selfiePathFor() })));
    await assertFails(enqueue('wrong-version', finalJob({ requestVersion: 3 })));
    await seed({ 'jobs/early-train': photoTrain({ status: 'completed', uid: BOB }) });
    await assertFails(enqueue('foreign-parent', finalJob()));
  });
  it('allows a failed finalization to retake the reference and retry without retraining', async () => {
    await completed(); await enqueue('finish', finalJob());
    await seed({ 'jobs/finish': finalJob({ status: 'failed' }) });
    await assertSucceeds(draftRef().set(draft()));
    await assertSucceeds(enqueue('retry-finish', finalJob()));
    await assertFails(draftRef().set(draft()));
  });
  it('freezes a successful finalization draft and keeps unfinalized identities from generating', async () => {
    await completed(); await seed({ 'garments/coat-1': { active: true } });
    await assertFails(enqueue('premature-generation', generate({ requestVersion: 4 })));
    await enqueue('finish', finalJob());
    await seed({ 'jobs/finish': finalJob({ status: 'completed' }) });
    await assertFails(draftRef().set(draft()));
  });
});

function upload(path = photoPath, uid = ALICE, contentType = 'image/jpeg', data = new Uint8Array([1, 2, 3])) {
  return Promise.resolve(env.authenticatedContext(uid).storage().ref(path).put(data, { contentType }));
}

describe('Selfie-only version-5 enrollment', () => {
  const enroll = (selfieSource: 'camera' | 'upload' = 'upload', extra: Record<string, unknown> = {}) => {
    const selected = capturedAt();
    const payload: Record<string, unknown> = { uid: ALICE, kind: 'enroll', status: 'queued', requestVersion: 5, createdAt: stamp(), uploadId: UPLOAD_ID, selfiePath: selfiePathFor(), selfieSource, selfieSelectedAt: selected, selfieCapturedAt: selected };
    if (selfieSource === 'upload') delete payload.selfieCapturedAt;
    return { ...payload, ...extra };
  };

  it.each(['camera', 'upload'] as const)('accepts one %s selfie with measurements and consent in the same commit', async source => {
    await seed({ [`users/${ALICE}`]: { ...userData(), heightCm: 180, weightKg: 75, measurementSystem: 'us' } });
    const db = dbFor(); const batch = db.batch();
    batch.set(db.doc(`users/${ALICE}`), { consentVersion: 'identity-training-v1', consentAt: stamp(), updatedAt: stamp() }, { merge: true });
    batch.set(db.doc('jobs/enroll-1'), enroll(source));
    batch.set(db.doc(`users/${ALICE}/queue/current`), { jobId: 'enroll-1', updatedAt: stamp() });
    await assertSucceeds(batch.commit());
  });
  it('requires consent, measurements and a unit preference', async () => {
    await seed({ [`users/${ALICE}`]: { ...userData(), heightCm: 180, weightKg: 75, measurementSystem: 'us' } });
    await assertFails(enqueue('no-consent', enroll()));
    await seedProfile();
    await assertFails(enqueue('no-units', enroll()));
  });
  it('rejects photo sets, foreign or stale selfies, forged capture times and wrong versions', async () => {
    await seedProfile(ALICE, { measurementSystem: 'us' });
    for (const [id, payload] of Object.entries({
      photos: enroll('upload', { photoPaths: paths() }), foreign: enroll('upload', { selfiePath: selfiePathFor(BOB) }),
      stale: enroll('upload', { selfieSelectedAt: capturedAt(-2 * 60 * 60 * 1000) }), forged: enroll('upload', { selfieCapturedAt: capturedAt() }),
      v4: enroll('upload', { requestVersion: 4 }), steps: enroll('upload', { steps: 80 }), uid: enroll('upload', { uid: BOB }),
    })) await assertFails(enqueue(`bad-${id}`, payload));
  });
  it('lets a ready face-swap identity request a version-5 generation', async () => {
    await seedProfile(ALICE, { measurementSystem: 'us', identity: { status: 'ready', mode: 'faceswap', version: 'enroll-1' } });
    await seed({ 'garments/coat-1': { active: true, name: 'Coat' } });
    await assertSucceeds(enqueue('gen-v5', generate({ requestVersion: 5 })));
  });
});

describe('Storage ownership and immutable uploads', () => {
  it('allows a new owner upload and owner read, but denies other-user and anonymous reads', async () => {
    await assertSucceeds(upload());
    await assertSucceeds(env.authenticatedContext(ALICE).storage().ref(photoPath).getMetadata());
    await assertFails(env.authenticatedContext(BOB).storage().ref(photoPath).getMetadata());
    await assertFails(env.unauthenticatedContext().storage().ref(photoPath).getMetadata());
    await assertFails(upload(`users/${BOB}/uploads/${UPLOAD_ID}/0.jpg`));
  });

  it('prevents overwriting or deleting an uploaded photo', async () => {
    await assertSucceeds(upload());
    await assertSucceeds(env.authenticatedContext(ALICE).storage().ref(photoPath).getMetadata());
    await assertFails(upload(photoPath, ALICE, 'image/jpeg', new Uint8Array([9, 8, 7, 6])));
    await assertFails(env.authenticatedContext(ALICE).storage().ref(photoPath).delete());
  });

  it('allows an owner selfie once and prevents cross-owner reads, writes, overwrites and deletion', async () => {
    const selfiePath = selfiePathFor();
    await assertSucceeds(upload(selfiePath));
    await assertSucceeds(env.authenticatedContext(ALICE).storage().ref(selfiePath).getMetadata());
    await assertFails(env.authenticatedContext(BOB).storage().ref(selfiePath).getMetadata());
    await assertFails(env.unauthenticatedContext().storage().ref(selfiePath).getMetadata());
    await assertFails(upload(selfiePathFor(BOB)));
    await assertFails(upload(selfiePath, ALICE, 'image/jpeg', new Uint8Array([9, 8, 7])));
    await assertFails(env.authenticatedContext(ALICE).storage().ref(selfiePath).delete());
  });

  it.each(['8.jpg', '0.png', 'extra.jpg', 'selfie.png', 'selfie-2.jpg', 'Selfie.jpg'])('blocks invalid upload slot %s', async filename => {
    await assertFails(upload(`users/${ALICE}/uploads/${UPLOAD_ID}/${filename}`));
  });

  it.each([photoPath, selfiePathFor()])('enforces JPEG content type, nonempty content, and the 20 MB limit for %s', async path => {
    await assertFails(upload(path, ALICE, 'image/png'));
    await assertFails(upload(path, ALICE, 'image/jpeg', new Uint8Array()));
    await assertFails(upload(path, ALICE, 'image/jpeg', new Uint8Array(20 * 1024 * 1024 + 1)));
  });

  it('rejects new non-UUID upload batches while preserving owner reads of old uploads', async () => {
    const legacyPath = `users/${ALICE}/uploads/batch-1/0.jpg`;
    await assertFails(upload(legacyPath));
    await assertFails(upload(selfiePathFor(ALICE, 'batch-1')));
    await env.withSecurityRulesDisabled(async context => {
      await context.storage().ref(legacyPath).put(new Uint8Array([1]));
    });
    await assertSucceeds(env.authenticatedContext(ALICE).storage().ref(legacyPath).getMetadata());
    await assertFails(env.authenticatedContext(BOB).storage().ref(legacyPath).getMetadata());
  });

  it.each([
    `users/${ALICE}/generations/result-1/result.png`,
    `users/${ALICE}/identity/v1/person.safetensors`,
    'garments/coat-1/catalog.png',
  ])('blocks client writes to server-owned asset %s', async path => {
    await assertFails(upload(path));
  });

  it('isolates server-created results and identity assets by owner', async () => {
    const protectedPaths = [`users/${ALICE}/generations/result-1/result.png`, `users/${ALICE}/identity/v1/person.safetensors`];
    await env.withSecurityRulesDisabled(async context => {
      for (const path of protectedPaths) await context.storage().ref(path).put(new Uint8Array([1]));
    });
    for (const path of protectedPaths) {
      await assertSucceeds(env.authenticatedContext(ALICE).storage().ref(path).getMetadata());
      await assertFails(env.authenticatedContext(BOB).storage().ref(path).getMetadata());
    }
  });
});
