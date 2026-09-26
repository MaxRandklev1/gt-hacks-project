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

  it.each([1, 3, '2', 2.5, null])('rejects new training request version %s', async requestVersion => {
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

describe('Generation requirements', () => {
  it('accepts a server-ready identity and active garment', async () => {
    await seedProfile(ALICE, { identity: { status: 'ready', version: 'trained' } });
    await seed({ 'garments/coat-1': { active: true } });
    await assertSucceeds(enqueue('generation', generate()));
  });

  it.each([1, 3, '2', 2.5, null])('rejects new generation request version %s', async requestVersion => {
    await seedProfile(ALICE, { identity: { status: 'ready', version: 'trained' } });
    await seed({ 'garments/coat-1': { active: true } });
    await assertFails(enqueue('wrong-generation-version', generate({ requestVersion })));
  });

  it.each(['training', 'failed', 'selecting'])('blocks generation for identity status %s', async status => {
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
function upload(path = photoPath, uid = ALICE, contentType = 'image/jpeg', data = new Uint8Array([1, 2, 3])) {
  return Promise.resolve(env.authenticatedContext(uid).storage().ref(path).put(data, { contentType }));
}

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
