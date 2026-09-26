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
  getFirestore: () => ({}), doc: (_: unknown, ...parts: string[]) => parts.join('/'), collection: vi.fn(),
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

  it('does not enqueue training when the account changes during the final photo upload', async () => {
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 512, height: 512, close: vi.fn() })));
    let photo = 0;
    vi.stubGlobal('document', { createElement: () => ({ getContext: () => ({ drawImage: vi.fn() }), toBlob: (done: (blob: Blob) => void) => done(new Blob([`unique-photo-${photo++}`])) }) });
    let upload = 0;
    mocks.upload.mockImplementation(() => ({ on: (_event: string, progress: (snapshot: unknown) => void, _error: unknown, complete: () => void) => {
      progress({ bytesTransferred: 1, totalBytes: 1 });
      if (++upload === 9) mocks.auth.currentUser = userB;
      complete();
    } }));
    const { submitOnboarding } = await import('../web/src/lib/client');
    mocks.auth.currentUser = userA;
    const photos = Array.from({ length: 8 }, (_, i) => new File([`photo${i}`], `photo${i}.jpg`, { type: 'image/jpeg' }));
    const selfie = new File(['selfie'], 'live-selfie.jpg', { type: 'image/jpeg' });
    const capturedAt = Date.now();
    await expect(submitOnboarding({ heightCm: 175, weightKg: 70, measurementSystem: 'us', photos, selfie, selfieSource: 'camera', selfieSelectedAt: capturedAt, selfieCapturedAt: capturedAt, consent: true })).rejects.toThrow(/account changed/i);
    expect(mocks.upload).toHaveBeenCalledTimes(9);
    expect(mocks.transaction).not.toHaveBeenCalled();
  });

  it.each(['camera', 'upload'] as const)('uploads the %s selfie separately as the training request reference', async source => {
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 512, height: 512, close: vi.fn() })));
    let photo = 0;
    vi.stubGlobal('document', { createElement: () => ({ getContext: () => ({ drawImage: vi.fn() }), toBlob: (done: (blob: Blob) => void) => done(new Blob([`unique-photo-${photo++}`])) }) });
    mocks.upload.mockImplementation(() => ({ on: (_event: string, _progress: unknown, _error: unknown, complete: () => void) => complete() }));
    const write = vi.fn();
    mocks.transaction.mockImplementation(async (_db, update) => update({ get: async () => ({ exists: () => false }), set: write }));
    mocks.auth.currentUser = userA;
    const photos = Array.from({ length: 8 }, (_, i) => new File([`photo${i}`], `photo${i}.jpg`, { type: 'image/jpeg' }));
    const selfie = new File(['current-look'], source === 'camera' ? 'selfie.jpg' : 'recent-selfie.png', { type: source === 'camera' ? 'image/jpeg' : 'image/png', lastModified: 1 });
    const capturedAt = Date.now();
    const { submitOnboarding } = await import('../web/src/lib/client');
    await submitOnboarding({ heightCm: 175, weightKg: 70, measurementSystem: 'us', photos, selfie, selfieSource: source, selfieSelectedAt: capturedAt, ...(source === 'camera' ? { selfieCapturedAt: capturedAt } : {}), consent: true });
    expect(mocks.upload).toHaveBeenCalledTimes(9);
    const selfiePath = mocks.upload.mock.calls[8][0];
    expect(selfiePath).toMatch(/^users\/account-a\/uploads\/[a-f0-9-]+\/selfie\.jpg$/);
    const job = write.mock.calls.map(call => call[1]).find(data => data.kind === 'train');
    expect(job).toMatchObject({ requestVersion: 3, uid: userA.uid, selfiePath, selfieSource: source, selfieSelectedAt: { captureMillis: capturedAt } });
    if (source === 'camera') expect(job.selfieCapturedAt).toEqual({ captureMillis: capturedAt });
    else expect(job).not.toHaveProperty('selfieCapturedAt');
    expect(mocks.upload.mock.calls[8][2]).toMatchObject({ contentType: 'image/jpeg', customMetadata: { source: source === 'camera' ? 'onboarding-camera' : 'onboarding-upload' } });
    expect(job.photoPaths).toHaveLength(8);
    expect(job.photoPaths).not.toContain(selfiePath);
    expect(write.mock.calls.some(call => call[1].measurementSystem === 'us')).toBe(true);
  });
});
