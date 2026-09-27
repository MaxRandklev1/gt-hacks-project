import { initializeApp } from 'firebase/app';
import { getAuth, GoogleAuthProvider, signInWithRedirect, getRedirectResult, onAuthStateChanged, signOut as firebaseSignOut, type User } from 'firebase/auth';
import { getFirestore, doc, collection, onSnapshot, query, where, orderBy, limit, getDoc, runTransaction, serverTimestamp, connectFirestoreEmulator, Timestamp } from 'firebase/firestore';
import { connectAuthEmulator } from 'firebase/auth';
import { getStorage, ref, uploadBytesResumable, getBlob, connectStorageEmulator } from 'firebase/storage';
import { CONSENT_VERSION, validateMeasurements, validateReferenceSelfie } from './validation';

export type SessionUser = Pick<User, 'uid' | 'displayName' | 'email' | 'photoURL'>;
export type PhotoSelection = { index: number; selected: boolean; score: number; reason: string };
export type BodyTemplateSelection = {
  id: 'weight-1' | 'weight-2' | 'weight-3' | 'weight-4' | 'weight-5';
  bmi: number; heightCm: number; weightKg: number; policyVersion: 'bmi-visual-v1';
};
export type UserProfile = {
  displayName?: string; email?: string; photoURL?: string; heightCm?: number; weightKg?: number; measurementSystem?: 'us' | 'metric';
  trainingJobId?: string;
  identity?: { status: 'selecting' | 'training' | 'awaiting_reference' | 'ready' | 'failed'; mode?: 'faceswap' | 'personal_base'; previewPath?: string; selectedPhotos?: PhotoSelection[]; error?: string; version?: string; profileId?: string; bodyTemplate?: BodyTemplateSelection };
};
export type Job = { id: string; uid: string; kind: 'train' | 'finalize' | 'enroll' | 'generate'; requestVersion?: number; trainingJobId?: string; status: 'queued' | 'running' | 'completed' | 'failed'; stage?: string; message?: string; progress?: number; error?: string; garmentId?: string; createdAt?: Timestamp };
export type Garment = { id: string; name: string; brand?: string; description?: string; imagePath: string; baseImagePath: string; thumbnailPath?: string; active: boolean };
export type Generation = { id: string; garmentId: string; garmentName: string; status: string; imagePath?: string; image2kPath?: string; image4kPath?: string; createdAt?: Timestamp; error?: string };
const config = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};
export const firebaseConfigured = Boolean(config.apiKey && config.authDomain && config.projectId && config.storageBucket && config.appId);
const app = firebaseConfigured ? initializeApp(config) : null;
const auth = app ? getAuth(app) : null;
const db = app ? getFirestore(app) : null;
const storage = app ? getStorage(app) : null;
if (auth && db && storage && import.meta.env.VITE_USE_FIREBASE_EMULATORS === 'true') {
  if (!['localhost', '127.0.0.1'].includes(location.hostname)) throw new Error('Emulators can only be used on localhost.');
  connectAuthEmulator(auth, 'http://127.0.0.1:9099', { disableWarnings: true });
  connectFirestoreEmulator(db, '127.0.0.1', 8080);
  connectStorageEmulator(storage, '127.0.0.1', 9199);
}
function services() {
  if (!auth || !db || !storage) throw new Error('Firebase is not configured yet.');
  return { auth, db, storage };
}
function signedIn() {
  const svc = services();
  if (!svc.auth.currentUser) throw new Error('Sign in to continue.');
  return { ...svc, user: svc.auth.currentUser };
}
function assertAccount(uid: string) {
  if (services().auth.currentUser?.uid !== uid) throw new Error('Your account changed. Please try again with your current account.');
}
async function ensureUser(user: User) {
  const { db } = services();
  const target = doc(db, 'users', user.uid);
  await runTransaction(db, async tx => {
    assertAccount(user.uid);
    if ((await tx.get(target)).exists()) return;
    assertAccount(user.uid);
    tx.set(target, { displayName: user.displayName || 'Your profile', email: user.email || '', photoURL: user.photoURL || '', createdAt: serverTimestamp(), updatedAt: serverTimestamp() });
  });
}
export async function signInWithGoogle() {
  const { auth } = services();
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: 'select_account' });
  // Full-page OAuth works with phone cameras opening a new browser tab and
  // avoids depending on popup permission in an embedded browser.
  await signInWithRedirect(auth, provider);
}
export function signOut() { return firebaseSignOut(services().auth); }
type AuthErrorStage = 'redirect' | 'profile' | 'session';
let redirectCheck: Promise<Error | null> | undefined;
function authError(cause: unknown) {
  const code = cause && typeof cause === 'object' && 'code' in cause ? String(cause.code) : '';
  if (code === 'auth/network-request-failed') return new Error('Google sign-in could not finish. Check your connection and try again.');
  if (code === 'auth/web-storage-unsupported') return new Error('This browser cannot save your sign-in. Open this page in Safari or Chrome with browser storage enabled.');
  if (code === 'auth/unauthorized-domain' || code === 'auth/operation-not-allowed') return new Error('Google sign-in is not available on this site yet. Please try again later.');
  return cause instanceof Error ? cause : new Error('Sign-in could not finish. Please try again.');
}
export function subscribeAuth(callback: (user: SessionUser | null) => void, onError?: (error: Error, stage: AuthErrorStage) => void, onLoading?: () => void) {
  if (!auth) { callback(null); return () => {}; }
  // A redirect result is consumed once, including React StrictMode remounts.
  redirectCheck ??= getRedirectResult(auth).then(() => null, authError);
  let active = true;
  let version = 0;
  let stop = () => {};
  void redirectCheck.then(redirectError => {
    if (!active) return;
    let first = true;
    stop = onAuthStateChanged(auth!, user => {
      const event = ++version;
      const returnError = first ? redirectError : null;
      first = false;
      onLoading?.();
      void (async () => {
        let profileError: Error | undefined;
        if (user) {
          try { await ensureUser(user); } catch (cause) { profileError = authError(cause); }
        }
        if (!active || event !== version) return;
        callback(user);
        if (profileError) onError?.(profileError, 'profile');
        else if (returnError && !user) onError?.(returnError, 'redirect');
      })();
    }, cause => {
      if (!active) return;
      version++;
      callback(null);
      onError?.(authError(cause), 'session');
    });
  });
  return () => { active = false; version++; stop(); };
}
export function subscribeProfile(uid: string, callback: (profile: UserProfile | null) => void, onError?: (error: Error) => void) {
  return onSnapshot(doc(services().db, 'users', uid), snap => callback(snap.exists() ? snap.data() as UserProfile : null), onError);
}
export function subscribeJobs(uid: string, callback: (jobs: Job[]) => void, onError?: (error: Error) => void) {
  return onSnapshot(query(collection(services().db, 'jobs'), where('uid', '==', uid), orderBy('createdAt', 'desc'), limit(25)), snap => callback(snap.docs.map(d => ({ ...d.data(), id: d.id }) as Job)), onError);
}
export function subscribeGenerations(uid: string, callback: (generations: Generation[]) => void, onError?: (error: Error) => void) {
  return onSnapshot(query(collection(services().db, 'users', uid, 'generations'), orderBy('createdAt', 'desc'), limit(100)), snap => callback(snap.docs.map(d => ({ ...d.data(), id: d.id }) as Generation)), onError);
}
export async function getGarment(id: string): Promise<Garment | null> {
  if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/.test(id)) throw new Error('Invalid item code.');
  const snap = await getDoc(doc(services().db, 'garments', id));
  return snap.exists() && snap.data().active ? { ...snap.data(), id: snap.id } as Garment : null;
}
export async function getPrivateImage(path: string) {
  const { storage, user } = signedIn();
  const blob = await getBlob(ref(storage, path), 30 * 1024 * 1024);
  assertAccount(user.uid);
  return URL.createObjectURL(blob);
}

async function enqueue(uid: string, payload: Record<string, unknown>, profileFields?: Record<string, unknown>) {
  assertAccount(uid);
  const { db, user } = signedIn();
  const job = doc(collection(db, 'jobs'));
  const lock = doc(db, 'users', user.uid, 'queue', 'current');
  return runTransaction(db, async tx => {
    assertAccount(uid);
    const current = await tx.get(lock);
    if (current.exists()) {
      const previous = await tx.get(doc(db, 'jobs', current.data().jobId));
      assertAccount(uid);
      if (previous.exists() && ['queued', 'running'].includes(previous.data().status)) {
        if (payload.kind === 'generate' && previous.data().kind === 'generate' && previous.data().garmentId === payload.garmentId) return previous.id;
        throw new Error('Your previous request is still processing. It will continue even if you close this page.');
      }
    }
    assertAccount(uid);
    if (profileFields) tx.set(doc(db, 'users', user.uid), profileFields, { merge: true });
    tx.set(job, { ...payload, uid: user.uid, status: 'queued', requestVersion: 5, createdAt: serverTimestamp() });
    tx.set(lock, { jobId: job.id, updatedAt: serverTimestamp() });
    return job.id;
  });
}
async function cleanPhoto(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  try {
    if (Math.min(bitmap.width, bitmap.height) < 384) throw new Error(`${file.name}: use a photo at least 384 pixels on each side.`);
    if (bitmap.width * bitmap.height > 40_000_000) throw new Error(`${file.name}: choose a photo smaller than 40 megapixels.`);
    const scale = Math.min(1, 2048 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale); canvas.height = Math.round(bitmap.height * scale);
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Your browser could not prepare the photos.');
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return await new Promise<Blob>((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('Could not prepare this photo.')), 'image/jpeg', 0.94));
  } finally { bitmap.close(); }
}
export async function saveMeasurements(input: { heightCm: number; weightKg: number; measurementSystem: 'us' | 'metric' }) {
  validateMeasurements(input.heightCm, input.weightKg);
  if (!['us', 'metric'].includes(input.measurementSystem)) throw new Error('Choose US or metric measurements.');
  const { db, user } = signedIn();
  await runTransaction(db, async tx => {
    assertAccount(user.uid);
    await tx.get(doc(db, 'users', user.uid));
    assertAccount(user.uid);
    tx.set(doc(db, 'users', user.uid), { ...input, updatedAt: serverTimestamp() }, { merge: true });
  });
}
/** Version-5 onboarding: one selfie becomes the face-swap identity. No photo set or training. */
export async function startEnrollment(input: { selfie: File; selfieSource: 'camera' | 'upload'; selfieSelectedAt: number; selfieCapturedAt?: number; consent: boolean }, onProgress?: (progress: number) => void) {
  if (!input.consent) throw new Error('Confirm that this selfie is of you and agree to use it for your try-ons.');
  const selection = { source: input.selfieSource, selectedAt: input.selfieSelectedAt, capturedAt: input.selfieCapturedAt };
  validateReferenceSelfie(input.selfie, selection);
  const { storage, user } = signedIn();
  const selfie = await cleanPhoto(input.selfie);
  const uploadId = crypto.randomUUID();
  assertAccount(user.uid);
  const selfiePath = `users/${user.uid}/uploads/${uploadId}/selfie.jpg`;
  const task = uploadBytesResumable(ref(storage, selfiePath), selfie, { contentType: 'image/jpeg', customMetadata: { owner: user.uid, source: input.selfieSource === 'camera' ? 'onboarding-camera' : 'onboarding-upload' } });
  await new Promise<void>((resolve, reject) => task.on('state_changed', snapshot => onProgress?.(snapshot.bytesTransferred / snapshot.totalBytes), reject, resolve));
  validateReferenceSelfie(input.selfie, selection);
  return enqueue(user.uid, {
    kind: 'enroll', uploadId, selfiePath, selfieSource: input.selfieSource,
    selfieSelectedAt: Timestamp.fromMillis(input.selfieSelectedAt),
    ...(input.selfieSource === 'camera' ? { selfieCapturedAt: Timestamp.fromMillis(input.selfieCapturedAt!) } : {}),
  }, { consentVersion: CONSENT_VERSION, consentAt: serverTimestamp(), updatedAt: serverTimestamp() });
}
export async function requestGeneration(garmentId: string) {
  const { user } = signedIn();
  const garment = await getGarment(garmentId);
  if (!garment) throw new Error('This clothing tag is unavailable. Try another item.');
  return enqueue(user.uid, { kind: 'generate', garmentId });
}
