import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  auth: { currentUser: null as { uid: string } | null },
  listener: undefined as undefined | ((user: { uid: string } | null) => void),
  redirect: vi.fn(), listen: vi.fn(), transaction: vi.fn(), getDoc: vi.fn(), upload: vi.fn(),
}));
vi.mock('firebase/app', () => ({ initializeApp: () => ({}) }));
vi.mock('firebase/auth', () => ({
  getAuth: () => mocks.auth, GoogleAuthProvider: class {}, signInWithRedirect: vi.fn(),
  getRedirectResult: mocks.redirect, onAuthStateChanged: mocks.listen,
  signOut: vi.fn(), connectAuthEmulator: vi.fn(),
}));
vi.mock('firebase/firestore', () => ({
  getFirestore: () => ({}), doc: (_: unknown, ...parts: string[]) => parts.length ? { path: parts.join('/'), id: parts.at(-1) } : { path: 'jobs/new-job', id: 'new-job' }, collection: vi.fn(),
  onSnapshot: vi.fn(), query: vi.fn(), where: vi.fn(), orderBy: vi.fn(), limit: vi.fn(),
  getDoc: mocks.getDoc, runTransaction: mocks.transaction, serverTimestamp: () => 'timestamp', connectFirestoreEmulator: vi.fn(),
  Timestamp: { fromMillis: (value: number) => ({ captureMillis: value }) },
}));
vi.mock('firebase/storage', () => ({
  getStorage: () => ({}), ref: (_: unknown, path: string) => path,
  uploadBytesResumable: mocks.upload, getBlob: vi.fn(), connectStorageEmulator: vi.fn(),
}));

const userA = { uid: 'account-a' };
const userB = { uid: 'account-b' };
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.resetModules(); vi.clearAllMocks();
  for (const key of ['API_KEY', 'AUTH_DOMAIN', 'PROJECT_ID', 'STORAGE_BUCKET', 'APP_ID']) vi.stubEnv(`VITE_FIREBASE_${key}`, 'test');
  vi.stubEnv('VITE_USE_FIREBASE_EMULATORS', 'false');
  mocks.auth.currentUser = null; mocks.listener = undefined;
  mocks.redirect.mockResolvedValue(null);
  mocks.listen.mockImplementation((_auth, callback) => { mocks.listener = callback; return vi.fn(); });
  mocks.transaction.mockResolvedValue(undefined);
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe('Authentication return and account boundaries', () => {
  it('consumes a redirect once across remounts and surfaces its failure to the active subscriber', async () => {
    mocks.redirect.mockRejectedValue(Object.assign(new Error('network'), { code: 'auth/network-request-failed' }));
    const { subscribeAuth } = await import('../web/src/lib/client');
    const first = vi.fn(); const second = vi.fn(); const onError = vi.fn();
    subscribeAuth(first)();
    const stop = subscribeAuth(second, onError);
    await flush(); mocks.listener!(null); await flush();
    expect(mocks.redirect).toHaveBeenCalledTimes(1);
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith(null);
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringMatching(/connection/i) }), 'redirect');
    stop();
  });

  it('discards a previous account bootstrap that completes after the new account', async () => {
    const oldProfile = deferred<void>(); const newProfile = deferred<void>();
    mocks.transaction.mockImplementationOnce(() => oldProfile.promise).mockImplementationOnce(() => newProfile.promise);
    const { subscribeAuth } = await import('../web/src/lib/client');
    const onUser = vi.fn(); const stop = subscribeAuth(onUser);
    await flush();
    mocks.auth.currentUser = userA; mocks.listener!(userA);
    mocks.auth.currentUser = userB; mocks.listener!(userB);
    newProfile.resolve(); await flush(); oldProfile.resolve(); await flush();
    expect(onUser).toHaveBeenCalledTimes(1);
    expect(onUser).toHaveBeenCalledWith(userB);
    stop();
  });

  it('reports profile setup failures without losing the authenticated account', async () => {
    mocks.transaction.mockRejectedValue(new Error('Profile permission denied'));
    const { subscribeAuth } = await import('../web/src/lib/client');
    const onUser = vi.fn(); const onError = vi.fn(); const stop = subscribeAuth(onUser, onError);
    await flush(); mocks.auth.currentUser = userA; mocks.listener!(userA); await flush();
    expect(onUser).toHaveBeenCalledWith(userA);
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: 'Profile permission denied' }), 'profile');
    stop();
  });

  it('does not queue a garment for a different account after its lookup completes', async () => {
    const garment = deferred<unknown>(); mocks.getDoc.mockReturnValue(garment.promise);
    const { requestGeneration } = await import('../web/src/lib/client');
    mocks.auth.currentUser = userA;
    const request = requestGeneration('tee');
    mocks.auth.currentUser = userB;
    garment.resolve({ exists: () => true, data: () => ({ active: true, name: 'Tee' }), id: 'tee' });
    await expect(request).rejects.toThrow(/account changed/i);
    expect(mocks.transaction).not.toHaveBeenCalled();
  });

  function imagePreparation() {
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 512, height: 512, close: vi.fn() })));
    let photo = 0;
    vi.stubGlobal('document', { createElement: () => ({ getContext: () => ({ drawImage: vi.fn() }), toBlob: (done: (blob: Blob) => void) => done(new Blob([`unique-photo-${photo++}`])) }) });
  }
  const eightPhotos = () => Array.from({ length: 8 }, (_, i) => new File([`photo${i}`], `photo${i}.jpg`, { type: 'image/jpeg' }));
  const reference = (source: 'camera' | 'upload' = 'camera') => {
    const now = Date.now();
    return { trainingJobId: 'training-1', selfie: new File(['selfie'], source === 'camera' ? 'selfie.jpg' : 'recent.png', { type: source === 'camera' ? 'image/jpeg' : 'image/png', lastModified: 1 }), selfieSource: source, selfieSelectedAt: now, ...(source === 'camera' ? { selfieCapturedAt: now } : {}), consent: true };
  };
  const trainingSnapshot = () => ({ exists: () => true, data: () => ({ uid: userA.uid, kind: 'train', requestVersion: 4, status: 'running' }) });
  function completedUpload() {
    mocks.upload.mockImplementation(() => ({ on: (_event: string, progress: (snapshot: unknown) => void, _error: unknown, complete: () => void) => { progress({ bytesTransferred: 1, totalBytes: 1 }); complete(); } }));
  }

  it('requires explicit training consent before uploading any photos', async () => {
    mocks.auth.currentUser = userA;
    const { startTraining } = await import('../web/src/lib/client');
    await expect(startTraining({ photos: eightPhotos(), consent: false })).rejects.toThrow(/allow identity training/);
    expect(mocks.upload).not.toHaveBeenCalled();
    expect(mocks.transaction).not.toHaveBeenCalled();
  });

  it('queues identity training after eight uploads without waiting for measurements or a selfie', async () => {
    imagePreparation(); completedUpload();
    const write = vi.fn();
    mocks.transaction.mockImplementation(async (_db, update) => update({ get: async () => ({ exists: () => false }), set: write }));
    mocks.auth.currentUser = userA;
    const { startTraining } = await import('../web/src/lib/client');
    const progress = vi.fn();
    expect(await startTraining({ photos: eightPhotos(), consent: true }, progress)).toBe('new-job');
    expect(mocks.upload).toHaveBeenCalledTimes(8);
    expect(progress).toHaveBeenLastCalledWith(1);
    const job = write.mock.calls.map(call => call[1]).find(data => data.kind === 'train');
    expect(job).toMatchObject({ requestVersion: 4, uid: userA.uid, status: 'queued' });
    expect(job.photoPaths).toHaveLength(8);
    expect(job).not.toHaveProperty('selfiePath');
    expect(write.mock.calls.some(call => call[1].consentVersion)).toBe(true);
    expect(write.mock.calls.every(call => !('heightCm' in call[1]))).toBe(true);
  });

  it('does not enqueue training when the account changes during the eighth upload', async () => {
    imagePreparation();
    let upload = 0;
    mocks.upload.mockImplementation(() => ({ on: (_event: string, _progress: unknown, _error: unknown, complete: () => void) => { if (++upload === 8) mocks.auth.currentUser = userB; complete(); } }));
    const { startTraining } = await import('../web/src/lib/client');
    mocks.auth.currentUser = userA;
    await expect(startTraining({ photos: eightPhotos(), consent: true })).rejects.toThrow(/account changed/i);
    expect(mocks.upload).toHaveBeenCalledTimes(8);
    expect(mocks.transaction).not.toHaveBeenCalled();
  });

  it.each(['camera', 'upload'] as const)('saves a %s reference independently while its photo training is running', async source => {
    imagePreparation(); completedUpload();
    const write = vi.fn();
    mocks.transaction.mockImplementation(async (_db, update) => update({ get: async () => trainingSnapshot(), set: write }));
    mocks.auth.currentUser = userA;
    const input = reference(source);
    const { saveOnboardingReference } = await import('../web/src/lib/client');
    const saved = await saveOnboardingReference(input);
    expect(mocks.upload).toHaveBeenCalledTimes(1);
    expect(saved.selfiePath).toMatch(/^users\/account-a\/uploads\/[a-f0-9-]+\/selfie\.jpg$/);
    expect(saved).toMatchObject({ trainingJobId: 'training-1', selfieSource: source, selfieSelectedAt: { captureMillis: input.selfieSelectedAt } });
    if (source === 'camera') expect(saved.selfieCapturedAt).toEqual({ captureMillis: input.selfieSelectedAt });
    else expect(saved).not.toHaveProperty('selfieCapturedAt');
    expect(write).toHaveBeenCalledWith(expect.objectContaining({ path: 'users/account-a/onboarding/current' }), saved);
    expect(write.mock.calls.some(call => call[1].kind)).toBe(false);
  });

  it('does not attach a selfie after the account switches during its upload', async () => {
    imagePreparation();
    mocks.upload.mockImplementation(() => ({ on: (_event: string, _progress: unknown, _error: unknown, complete: () => void) => { mocks.auth.currentUser = userB; complete(); } }));
    mocks.auth.currentUser = userA;
    const { saveOnboardingReference } = await import('../web/src/lib/client');
    await expect(saveOnboardingReference(reference())).rejects.toThrow(/account changed/i);
    expect(mocks.transaction).not.toHaveBeenCalled();
  });

  it('does not attach a selfie after an account switch during training verification', async () => {
    imagePreparation(); completedUpload();
    const write = vi.fn();
    mocks.transaction.mockImplementation(async (_db, update) => update({ get: async () => { mocks.auth.currentUser = userB; return trainingSnapshot(); }, set: write }));
    mocks.auth.currentUser = userA;
    const { saveOnboardingReference } = await import('../web/src/lib/client');
    await expect(saveOnboardingReference(reference())).rejects.toThrow(/account changed/i);
    expect(write).not.toHaveBeenCalled();
  });

  it('does not save measurements to a stale account after its transaction read', async () => {
    const write = vi.fn();
    mocks.transaction.mockImplementation(async (_db, update) => update({ get: async () => { mocks.auth.currentUser = userB; return {}; }, set: write }));
    mocks.auth.currentUser = userA;
    const { saveMeasurements } = await import('../web/src/lib/client');
    await expect(saveMeasurements({ heightCm: 178, weightKg: 70, measurementSystem: 'us' })).rejects.toThrow(/account changed/i);
    expect(write).not.toHaveBeenCalled();
  });

  it('saves normalized measurements without placing a job in the GPU queue', async () => {
    const write = vi.fn();
    mocks.transaction.mockImplementation(async (_db, update) => update({ get: async () => ({}), set: write }));
    mocks.auth.currentUser = userA;
    const { saveMeasurements } = await import('../web/src/lib/client');
    await saveMeasurements({ heightCm: 177.8, weightKg: 68.04, measurementSystem: 'us' });
    expect(write).toHaveBeenCalledTimes(1);
    expect(write).toHaveBeenCalledWith(expect.objectContaining({ path: 'users/account-a' }), { heightCm: 177.8, weightKg: 68.04, measurementSystem: 'us', updatedAt: 'timestamp' }, { merge: true });
  });

  it.each(['queued', 'running', 'completed'])('reuses an existing %s finalization across browser retries', async status => {
    const write = vi.fn();
    mocks.transaction.mockImplementation(async (_db, update) => update({ get: async (target: { path: string }) => target.path.endsWith('/queue/current') ? { exists: () => true, data: () => ({ jobId: 'existing-finalize' }) } : { id: 'existing-finalize', exists: () => true, data: () => ({ kind: 'finalize', trainingJobId: 'training-1', status }) }, set: write }));
    mocks.auth.currentUser = userA;
    const { requestFinalization } = await import('../web/src/lib/client');
    expect(await requestFinalization('training-1')).toBe('existing-finalize');
    expect(write).not.toHaveBeenCalled();
  });

  it('allows a failed finalization to be retried with a small v4 job', async () => {
    const write = vi.fn();
    mocks.transaction.mockImplementation(async (_db, update) => update({ get: async (target: { path: string }) => target.path.endsWith('/queue/current') ? { exists: () => true, data: () => ({ jobId: 'failed-finalize' }) } : { id: 'failed-finalize', exists: () => true, data: () => ({ kind: 'finalize', trainingJobId: 'training-1', status: 'failed' }) }, set: write }));
    mocks.auth.currentUser = userA;
    const { requestFinalization } = await import('../web/src/lib/client');
    await requestFinalization('training-1');
    const job = write.mock.calls.map(call => call[1]).find(data => data.kind === 'finalize');
    expect(job).toEqual({ kind: 'finalize', trainingJobId: 'training-1', requestVersion: 4, uid: userA.uid, status: 'queued', createdAt: 'timestamp' });
  });
});