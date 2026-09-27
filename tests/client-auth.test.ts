import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  auth: { currentUser: null as { uid: string } | null },
  listener: undefined as undefined | ((user: { uid: string } | null) => void),
  redirect: vi.fn(), listen: vi.fn(), transaction: vi.fn(), getDoc: vi.fn(), getDocs: vi.fn(), upload: vi.fn(),
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
  getDoc: mocks.getDoc, getDocs: mocks.getDocs, runTransaction: mocks.transaction, serverTimestamp: () => 'timestamp', connectFirestoreEmulator: vi.fn(),
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

  it('requires sign-in before listing the garment inventory', async () => {
    const { listActiveGarments } = await import('../web/src/lib/client');
    await expect(listActiveGarments()).rejects.toThrow(/sign in/i);
    expect(mocks.getDocs).not.toHaveBeenCalled();
  });

  it('orders numbered THREAD garments naturally, keeps other active items, and uses document IDs', async () => {
    mocks.auth.currentUser = userA;
    mocks.getDocs.mockResolvedValue({ docs: ['demo-shirt', 'thread-10', 'thread-2', 'thread-1'].map(id => ({
      id, data: () => ({ id: 'untrusted-field-id', name: id, active: true, imagePath: `garments/${id}/reference.png` }),
    })) });
    const { listActiveGarments } = await import('../web/src/lib/client');
    expect((await listActiveGarments()).map(item => item.id)).toEqual(['thread-1', 'thread-2', 'thread-10', 'demo-shirt']);
  });

  it('rejects an inventory response when the account changes before it arrives', async () => {
    const inventory = deferred<unknown>(); mocks.getDocs.mockReturnValue(inventory.promise);
    mocks.auth.currentUser = userA;
    const { listActiveGarments } = await import('../web/src/lib/client');
    const request = listActiveGarments();
    mocks.auth.currentUser = userB;
    inventory.resolve({ docs: [] });
    await expect(request).rejects.toThrow(/account changed/i);
  });

  function imagePreparation() {
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 512, height: 512, close: vi.fn() })));
    let photo = 0;
    vi.stubGlobal('document', { createElement: () => ({ getContext: () => ({ drawImage: vi.fn() }), toBlob: (done: (blob: Blob) => void) => done(new Blob([`unique-photo-${photo++}`])) }) });
  }
  const selfieInput = (source: 'camera' | 'upload' = 'camera', consent = true) => {
    const now = Date.now();
    return { selfie: new File(['selfie'], source === 'camera' ? 'selfie.jpg' : 'recent.png', { type: source === 'camera' ? 'image/jpeg' : 'image/png', lastModified: 1 }), selfieSource: source, selfieSelectedAt: now, ...(source === 'camera' ? { selfieCapturedAt: now } : {}), consent };
  };
  function completedUpload() {
    mocks.upload.mockImplementation(() => ({ on: (_event: string, progress: (snapshot: unknown) => void, _error: unknown, complete: () => void) => { progress({ bytesTransferred: 1, totalBytes: 1 }); complete(); } }));
  }

  it('requires consent before uploading the selfie', async () => {
    mocks.auth.currentUser = userA;
    const { startEnrollment } = await import('../web/src/lib/client');
    await expect(startEnrollment(selfieInput('camera', false))).rejects.toThrow(/Confirm that this selfie/);
    expect(mocks.upload).not.toHaveBeenCalled();
    expect(mocks.transaction).not.toHaveBeenCalled();
  });

  it.each(['camera', 'upload'] as const)('queues a version-5 enrollment from one %s selfie, without photos or training', async source => {
    imagePreparation(); completedUpload();
    const write = vi.fn();
    mocks.transaction.mockImplementation(async (_db, update) => update({ get: async () => ({ exists: () => false }), set: write }));
    mocks.auth.currentUser = userA;
    const input = selfieInput(source);
    const { startEnrollment } = await import('../web/src/lib/client');
    const progress = vi.fn();
    expect(await startEnrollment(input, progress)).toBe('new-job');
    expect(mocks.upload).toHaveBeenCalledTimes(1);
    expect(progress).toHaveBeenLastCalledWith(1);
    const job = write.mock.calls.map(call => call[1]).find(data => data.kind === 'enroll');
    expect(job).toMatchObject({ requestVersion: 5, uid: userA.uid, status: 'queued', selfieSource: source, selfieSelectedAt: { captureMillis: input.selfieSelectedAt } });
    expect(job.selfiePath).toBe(`users/account-a/uploads/${job.uploadId}/selfie.jpg`);
    expect(job).not.toHaveProperty('photoPaths');
    if (source === 'camera') expect(job.selfieCapturedAt).toEqual({ captureMillis: input.selfieSelectedAt });
    else expect(job).not.toHaveProperty('selfieCapturedAt');
    expect(write.mock.calls.some(call => call[1].consentVersion)).toBe(true);
  });

  it('does not enqueue enrollment when the account changes during the selfie upload', async () => {
    imagePreparation();
    mocks.upload.mockImplementation(() => ({ on: (_event: string, _progress: unknown, _error: unknown, complete: () => void) => { mocks.auth.currentUser = userB; complete(); } }));
    mocks.auth.currentUser = userA;
    const { startEnrollment } = await import('../web/src/lib/client');
    await expect(startEnrollment(selfieInput())).rejects.toThrow(/account changed/i);
    expect(mocks.transaction).not.toHaveBeenCalled();
  });

  it('does not save measurements to a stale account after its transaction read', async () => {
    const write = vi.fn();
    mocks.transaction.mockImplementation(async (_db, update) => update({ get: async () => { mocks.auth.currentUser = userB; return {}; }, set: write }));
    mocks.auth.currentUser = userA;
    const { saveMeasurements } = await import('../web/src/lib/client');
    await expect(saveMeasurements({ heightCm: 178, weightKg: 70, measurementSystem: 'us', bodyStyle: 'male' })).rejects.toThrow(/account changed/i);
    expect(write).not.toHaveBeenCalled();
  });

  it('saves normalized measurements without placing a job in the GPU queue', async () => {
    const write = vi.fn();
    mocks.transaction.mockImplementation(async (_db, update) => update({ get: async () => ({}), set: write }));
    mocks.auth.currentUser = userA;
    const { saveMeasurements } = await import('../web/src/lib/client');
    await saveMeasurements({ heightCm: 177.8, weightKg: 68.04, measurementSystem: 'us', bodyStyle: 'female' });
    expect(write).toHaveBeenCalledTimes(1);
    expect(write).toHaveBeenCalledWith(expect.objectContaining({ path: 'users/account-a' }), { heightCm: 177.8, weightKg: 68.04, measurementSystem: 'us', bodyStyle: 'female', updatedAt: 'timestamp' }, { merge: true });
  });

  it('reuses an in-flight try-on of the same garment and queues new requests as version 5', async () => {
    const garment = { exists: () => true, data: () => ({ active: true, name: 'Tee' }), id: 'tee' };
    mocks.getDoc.mockResolvedValue(garment);
    const write = vi.fn();
    mocks.transaction.mockImplementation(async (_db, update) => update({ get: async (target: { path: string }) => target.path.endsWith('/queue/current') ? { exists: () => true, data: () => ({ jobId: 'running-tee' }) } : { id: 'running-tee', exists: () => true, data: () => ({ kind: 'generate', garmentId: 'tee', status: 'running' }) }, set: write }));
    mocks.auth.currentUser = userA;
    const { requestGeneration } = await import('../web/src/lib/client');
    expect(await requestGeneration('tee')).toBe('running-tee');
    expect(write).not.toHaveBeenCalled();
    mocks.transaction.mockImplementation(async (_db, update) => update({ get: async () => ({ exists: () => false }), set: write }));
    await requestGeneration('tee');
    expect(write.mock.calls.map(call => call[1]).find(data => data.kind === 'generate')).toMatchObject({ garmentId: 'tee', requestVersion: 5 });
  });
});
