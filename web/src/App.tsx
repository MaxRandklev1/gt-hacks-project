import { useEffect, useRef, useState } from 'react';
import {
  firebaseConfigured, signInWithGoogle, signOut, subscribeAuth, subscribeProfile,
  subscribeJobs, subscribeGenerations, subscribeOnboarding, getGarment, getPrivateImage,
  startTraining, saveMeasurements, saveOnboardingReference, requestFinalization, requestGeneration,
  type SessionUser, type UserProfile, type Job, type Garment, type Generation, type OnboardingDraft,
} from './lib/client';
import { parseGarmentCode, readPendingGarment, rememberGarment, clearPendingGarment } from './lib/qr';
import { GoogleMark, Icon } from './components/Icon';
import { Scanner } from './components/Scanner';
import { SelfieCapture, type CapturedSelfie } from './components/SelfieCapture';
import { createMeasurements, editMeasurement, formatHeight, formatWeight, measurementsValid, switchMeasurementSystem } from './lib/measurements';
import { validateReferenceSelfie } from './lib/validation';
import { onboardingProgress } from './lib/onboarding';
import './styles.css';

type View = 'home' | 'scanner' | 'gallery' | 'detail' | 'account';
type Screen = View | 'welcome' | 'measurements' | 'photos' | 'selfie' | 'preparing' | 'loading';
const demoImage = '/demo/try-on-final-1024.png';
const demoGeneration: Generation = { id: 'design-preview', garmentId: 'preview-piece', garmentName: 'The photographic tee', status: 'completed', imagePath: undefined };
const previewScreens: { id: Screen; label: string }[] = [
  { id: 'welcome', label: 'Welcome' }, { id: 'photos', label: 'Eight photos' },
  { id: 'measurements', label: 'Your profile' }, { id: 'selfie', label: 'Recent selfie' }, { id: 'preparing', label: 'Training' },
  { id: 'home', label: 'Fitting room' }, { id: 'scanner', label: 'Scanner' },
  { id: 'gallery', label: 'Your looks' }, { id: 'detail', label: 'Look detail' },
];

function errorText(error: unknown) { return error instanceof Error ? error.message : 'Something went wrong. Please try again.'; }
function initialPending() { try { return readPendingGarment(); } catch { return null; } }
function dateText(value: unknown) {
  let date: Date | undefined;
  if (value instanceof Date) date = value;
  else if (typeof value === 'string' || typeof value === 'number') date = new Date(value);
  else if (value && typeof value === 'object' && 'toDate' in value && typeof value.toDate === 'function') date = value.toDate();
  return date && Number.isFinite(date.getTime()) ? date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';
}

function usePrivateImage(path?: string) {
  const [url, setUrl] = useState<string>();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setUrl(undefined); setFailed(false);
    if (!path) return;
    let cancelled = false;
    let ownedUrl: string | undefined;
    getPrivateImage(path).then(result => {
      ownedUrl = result;
      if (cancelled) URL.revokeObjectURL(result); else setUrl(result);
    }).catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; if (ownedUrl) URL.revokeObjectURL(ownedUrl); };
  }, [path]);
  return { url, failed };
}

function ProtectedImage({ path, alt, className, preview = false }: { path?: string; alt: string; className?: string; preview?: boolean }) {
  const { url, failed } = usePrivateImage(preview ? undefined : path);
  if (preview || url) return <img src={preview ? demoImage : url} alt={alt} className={className} />;
  return <div className={`image-placeholder ${className || ''}`}>{failed ? <><Icon name="retry" /><span>Image unavailable</span></> : <><Icon name="spark" /><span>{path ? 'Loading image' : 'Your look will appear here'}</span></>}</div>;
}

function ProgressBar({ value, label }: { value?: number; label: string }) {
  const valid = typeof value === 'number' && Number.isFinite(value);
  const percent = valid ? Math.round(Math.min(1, Math.max(0, value!)) * 100) : undefined;
  return <div className="progress-block"><div className="progress-label"><span>{label}</span>{percent !== undefined && <span>{percent}%</span>}</div><div className={`progress-track ${valid ? '' : 'indeterminate'}`} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={valid ? { width: `${percent}%` } : undefined} /></div></div>;
}

function App() {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [authLoading, setAuthLoading] = useState(firebaseConfigured);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [accountError, setAccountError] = useState('');
  const [authRetry, setAuthRetry] = useState(0);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [onboarding, setOnboarding] = useState<OnboardingDraft | null>(null);
  const [onboardingLoading, setOnboardingLoading] = useState(false);
  const [generations, setGenerations] = useState<Generation[]>([]);
  const [view, setView] = useState<View>('scanner');
  const [preview, setPreview] = useState(false);
  const [previewScreen, setPreviewScreen] = useState<Screen>('welcome');
  const [phase, setPhase] = useState<'measurements' | 'photos' | 'selfie' | null>(null);
  const [editingProfile, setEditingProfile] = useState(false);
  const [measurements, setMeasurements] = useState(createMeasurements);
  const [selfie, setSelfie] = useState<CapturedSelfie | null>(null);
  const [photos, setPhotos] = useState<File[]>([]);
  const [consent, setConsent] = useState(false);
  const [trainingConsent, setTrainingConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number>();
  const [uploading, setUploading] = useState(false);
  const [trainingRequestId, setTrainingRequestId] = useState<string>();
  const [finalizing, setFinalizing] = useState(false);
  const [finalizationError, setFinalizationError] = useState('');
  const [finalizationRetry, setFinalizationRetry] = useState(0);
  const finalizationAttempts = useRef(new Set<string>());
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pendingGarment, setPendingGarment] = useState<string | null>(initialPending);
  const [garment, setGarment] = useState<Garment | null>(null);
  const [garmentLoading, setGarmentLoading] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [requestedJob, setRequestedJob] = useState<string>();
  const [selectedGeneration, setSelectedGeneration] = useState<Generation | null>(null);
  const [downloading, setDownloading] = useState(false);
  const requested = useRef(new Set<string>());
  const sessionUid = useRef<string | null>(null);
  const sessionEpoch = useRef(0);
  const photoInput = useRef<HTMLInputElement>(null);
  const [photoUrls, setPhotoUrls] = useState<string[]>([]);
  useEffect(() => {
    const urls = photos.map(photo => URL.createObjectURL(photo));
    setPhotoUrls(urls);
    return () => urls.forEach(url => URL.revokeObjectURL(url));
  }, [photos]);

  useEffect(() => {
    if (!firebaseConfigured || preview) return;
    setAuthLoading(true);
    const stop = subscribeAuth(next => {
      sessionUid.current = next?.uid || null;
      setUser(next); setAuthLoading(false); setProfileLoading(Boolean(next)); setJobsLoading(Boolean(next)); setOnboardingLoading(Boolean(next));
      setAccountError(''); setError(''); setNotice('');
      setProfile(null); setJobs([]); setOnboarding(null); setGenerations([]); setSelectedGeneration(null);
      setPhotos([]); setConsent(false); setTrainingConsent(false); setMeasurements(createMeasurements()); setSelfie(null);
      setEditingProfile(false); setPhase(null); setTrainingRequestId(undefined);
      setFinalizing(false); setFinalizationError(''); setFinalizationRetry(0); finalizationAttempts.current.clear();
      setUploading(false); setUploadProgress(undefined); setBusy(false); setDownloading(false);
      setRequestedJob(undefined); setRequesting(false); requested.current.clear();
      setView('scanner');
    }, (cause, stage) => {
      setAuthLoading(false);
      if (stage === 'profile') { setAccountError(errorText(cause)); setProfileLoading(false); }
      else setError(errorText(cause));
    }, () => {
      sessionEpoch.current++; sessionUid.current = null;
      setAuthLoading(true);
    });
    return () => { stop(); sessionEpoch.current++; sessionUid.current = null; };
  }, [preview, authRetry]);

  useEffect(() => {
    if (!user || preview || authLoading) return;
    const uid = user.uid;
    let active = true;
    const current = () => active && sessionUid.current === uid;
    const report = (cause: unknown) => { if (current()) setError(errorText(cause)); };
    const stops = [
      subscribeProfile(uid, next => {
        if (!current()) return;
        setProfile(next); setProfileLoading(false);
        setAccountError(next ? '' : 'Your profile is unavailable. Retry to reconnect your account.');
      }, cause => { if (current()) { setAccountError(errorText(cause)); setProfileLoading(false); } }),
      subscribeJobs(uid, next => { if (current()) { setJobs(next); setJobsLoading(false); } }, cause => { if (current()) { setAccountError(errorText(cause)); setJobsLoading(false); } }),
      subscribeOnboarding(uid, next => { if (current()) { setOnboarding(next); setOnboardingLoading(false); } }, cause => { if (current()) { setAccountError(errorText(cause)); setOnboardingLoading(false); } }),
      subscribeGenerations(uid, next => { if (current()) setGenerations(next); }, report),
    ];
    return () => { active = false; stops.forEach(stop => stop()); };
  }, [user?.uid, preview, authLoading, authRetry]);

  useEffect(() => {
    setMeasurements(createMeasurements(profile?.heightCm, profile?.weightKg, profile?.measurementSystem || 'us'));
  }, [profile?.heightCm, profile?.weightKg, profile?.measurementSystem]);

  useEffect(() => {
    if (preview || !user || !pendingGarment) { setGarment(null); return; }
    let current = true;
    setGarmentLoading(true); setGarment(null);
    getGarment(pendingGarment).then(next => {
      if (!current) return;
      if (!next || !next.active) { setError('This garment code is not available. Check the code or scan a different piece.'); return; }
      setGarment(next);
    }).catch(cause => { if (current) setError(errorText(cause)); }).finally(() => { if (current) setGarmentLoading(false); });
    return () => { current = false; };
  }, [pendingGarment, user?.uid, preview]);

  const identityStatus = profile?.identity?.status;
  const setup = onboardingProgress(profile, jobs, onboarding, trainingRequestId);
  const ready = setup.ready;
  const trainJob = setup.training;
  const finalizeJob = setup.finalization;
  const trainingFailed = setup.trainingFailed;
  const finalizationFailed = setup.finalizationFailed;
  const setupLoaded = !authLoading && !profileLoading && !jobsLoading && !onboardingLoading && !accountError;
  useEffect(() => {
    if (ready && trainingRequestId && profile?.identity?.version === trainingRequestId) setTrainingRequestId(undefined);
  }, [ready, trainingRequestId, profile?.identity?.version]);
  useEffect(() => {
    if (preview || !user || !setupLoaded || !setup.canFinalize || !trainJob || (finalizationFailed && finalizationRetry === 0)) return;
    const key = `${user.uid}:${trainJob.id}:${finalizationRetry}`;
    if (finalizationAttempts.current.has(key)) return;
    finalizationAttempts.current.add(key);
    const epoch = sessionEpoch.current;
    setFinalizing(true); setFinalizationError('');
    requestFinalization(trainJob.id).catch(cause => {
      if (sessionEpoch.current === epoch) { setError(errorText(cause)); setFinalizationError(errorText(cause)); }
    }).finally(() => { if (sessionEpoch.current === epoch) setFinalizing(false); });
  }, [preview, user?.uid, setupLoaded, setup.canFinalize, trainJob?.id, finalizationFailed, finalizationRetry]);
  useEffect(() => {
    if (preview || !setupLoaded || !user || !ready || !garment || garment.id !== pendingGarment || editingProfile) return;
    const key = `${user.uid}:${garment.id}`;
    if (requested.current.has(key)) return;
    requested.current.add(key);
    let current = true;
    setRequesting(true); setError(''); setView('home');
    requestGeneration(garment.id).then(jobId => {
      if (!current) return;
      setRequesting(false); setRequestedJob(jobId); clearPendingGarment(); setPendingGarment(null);
      setNotice('Your piece is in the fitting room. We’ll keep its progress here.');
    }).catch(cause => { if (current) setError(errorText(cause)); }).finally(() => { if (current) setRequesting(false); });
    return () => { current = false; };
  }, [garment, pendingGarment, user?.uid, ready, preview, editingProfile, setupLoaded]);

  const activeJob = jobs.find(job => job.kind === 'generate' && (job.status === 'running' || job.status === 'queued'));
  const jobFromRequest = jobs.find(job => job.id === requestedJob);
  const awaitingJob = Boolean(requestedJob && !jobFromRequest);
  const showGenerationProgress = !preview && (requesting || activeJob || awaitingJob);
  const generationList = preview ? [demoGeneration] : generations;
  const chosenGeneration = preview ? demoGeneration : (generations.find(item => item.id === selectedGeneration?.id) || selectedGeneration);
  const screen: Screen = preview ? previewScreen : authLoading ? 'loading' : !user ? 'welcome' : !setupLoaded ? 'loading' : editingProfile && phase ? phase : ready ? view : phase || (setup.screen === 'ready' ? view : setup.screen);
  const firstName = user?.displayName?.trim().split(/\s+/)[0] || 'there';
  const isMain = ['home', 'scanner', 'gallery', 'detail', 'account'].includes(screen);
  const measurementValid = measurementsValid(measurements);

  function navigate(next: Screen) {
    setError(''); setNotice('');
    if (preview) setPreviewScreen(next);
    else if (next === 'measurements' || next === 'photos' || next === 'selfie') { setPhase(next); }
    else if (next !== 'welcome' && next !== 'loading' && next !== 'preparing') setView(next);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  async function login() {
    if (preview || !firebaseConfigured) return;
    setBusy(true); setError('');
    try { await signInWithGoogle(); } catch (cause) { setError(errorText(cause)); } finally { setBusy(false); }
  }
  async function logout() {
    if (preview) { setPreview(false); setPreviewScreen('welcome'); return; }
    setBusy(true); setError('');
    try { await signOut(); setEditingProfile(false); setPhotos([]); setSelfie(null); setConsent(false); } catch (cause) { setError(errorText(cause)); } finally { setBusy(false); }
  }
  function addPhotos(files: FileList | null) {
    if (preview || !files || uploading) return;
    const chosen = Array.from(files);
    if (photos.length + chosen.length > 8) { setError(`Choose exactly eight photos in total. You have room for ${8 - photos.length} more.`); return; }
    if (chosen.some(file => !['image/jpeg', 'image/png', 'image/webp'].includes(file.type))) { setError('Please use JPG, PNG, or WebP images. Convert HEIC photos before uploading.'); return; }
    if (chosen.some(file => file.size === 0 || file.size > 20 * 1024 * 1024)) { setError('Each photo must be a nonempty image no larger than 20 MB.'); return; }
    setError(''); setPhotos(current => [...current, ...chosen]);
    if (photoInput.current) photoInput.current.value = '';
  }
  async function beginTraining() {
    if (preview) { setPreviewScreen('measurements'); return; }
    if (uploading) return;
    if (photos.length !== 8 || !trainingConsent) { setError('Choose eight photos and allow your personal identity training to continue.'); return; }
    setError(''); setUploading(true); setUploadProgress(0);
    const epoch = sessionEpoch.current;
    try {
      const jobId = await startTraining({ photos, consent: trainingConsent }, progress => { if (sessionEpoch.current === epoch) setUploadProgress(progress); });
      if (sessionEpoch.current !== epoch) return;
      setTrainingRequestId(jobId);
      setEditingProfile(false); setPhase('measurements'); setPhotos([]); setSelfie(null); setConsent(false); setTrainingConsent(false);
      setFinalizationRetry(0); setFinalizationError(''); setNotice('Your identity training is starting in the background. Let’s finish your details.');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (cause) { if (sessionEpoch.current === epoch) setError(errorText(cause)); }
    finally { if (sessionEpoch.current === epoch) setUploading(false); }
  }
  async function continueMeasurements() {
    if (preview) { setPreviewScreen('selfie'); return; }
    if (busy || !measurementValid) return;
    const epoch = sessionEpoch.current;
    setBusy(true); setError('');
    try {
      const saved = { heightCm: measurements.heightCm!, weightKg: measurements.weightKg!, measurementSystem: measurements.system };
      await saveMeasurements(saved);
      if (sessionEpoch.current !== epoch) return;
      setProfile(current => current ? { ...current, ...saved } : current);
      navigate('selfie');
    } catch (cause) { if (sessionEpoch.current === epoch) setError(errorText(cause)); }
    finally { if (sessionEpoch.current === epoch) setBusy(false); }
  }
  async function finishReference() {
    if (preview) { setPreviewScreen('preparing'); return; }
    if (uploading || !trainJob || !setup.background) return;
    if (!selfie || !consent) { setError('Choose a recent selfie and confirm that it matches your current look.'); return; }
    try { validateReferenceSelfie(selfie.file, { source: selfie.source, selectedAt: selfie.selectedAt, capturedAt: selfie.capturedAt }); } catch (cause) { setError(errorText(cause)); return; }
    const epoch = sessionEpoch.current;
    setError(''); setUploading(true); setUploadProgress(0);
    try {
      const saved = await saveOnboardingReference({ trainingJobId: trainJob.id, selfie: selfie.file, selfieSource: selfie.source, selfieSelectedAt: selfie.selectedAt, selfieCapturedAt: selfie.capturedAt, consent }, progress => { if (sessionEpoch.current === epoch) setUploadProgress(progress); });
      if (sessionEpoch.current !== epoch) return;
      setOnboarding(saved); setPhase(null); setEditingProfile(false); setSelfie(null); setConsent(false); setFinalizationError(''); setFinalizationRetry(current => current + 1);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (cause) { if (sessionEpoch.current === epoch) setError(errorText(cause)); }
    finally { if (sessionEpoch.current === epoch) setUploading(false); }
  }
  function restartPhotos() {
    setEditingProfile(true); setPhase('photos'); setPhotos([]); setSelfie(null); setConsent(false); setTrainingConsent(false); setTrainingRequestId(undefined); setError(''); setFinalizationRetry(0);
  }
  function scan(value: string) {
    try {
      const id = parseGarmentCode(value);
      if (preview) { setNotice('Preview result — no garment request was sent.'); setPreviewScreen('detail'); return; }
      rememberGarment(id); requested.current.delete(`${user?.uid}:${id}`); setPendingGarment(id); setView('home'); setError('');
    } catch (cause) { setError(errorText(cause)); }
  }
  function retryGarment(id: string) {
    if (preview) { setPreviewScreen('detail'); setNotice('Preview only — no generation was started.'); return; }
    requested.current.delete(`${user?.uid}:${id}`);
    rememberGarment(id); setPendingGarment(id); setView('home'); setError('');
    // Re-selecting the same failed pending piece should also restart its lookup.
    if (pendingGarment === id && garment) setGarment({ ...garment });
  }
  function openLook(item: Generation) { setSelectedGeneration(item); navigate('detail'); }
  async function downloadLook() {
    if (!chosenGeneration) return;
    if (preview) { const link = document.createElement('a'); link.href = '/demo/try-on-final-4k.png'; link.download = 'thread-demo-4k.png'; link.click(); return; }
    const path = chosenGeneration.image4kPath || chosenGeneration.imagePath;
    if (!path) return;
    setDownloading(true); setError('');
    const epoch = sessionEpoch.current;
    try {
      const url = await getPrivateImage(path);
      if (sessionEpoch.current !== epoch) { URL.revokeObjectURL(url); return; }
      const link = document.createElement('a'); link.href = url; link.download = `thread-${chosenGeneration.id}.png`; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 3000);
    } catch (cause) { if (sessionEpoch.current === epoch) setError(errorText(cause)); }
    finally { if (sessionEpoch.current === epoch) setDownloading(false); }
  }

  function lookCard(item: Generation, index: number) {
    const done = item.status === 'completed';
    return <button className="look-card" key={item.id} onClick={() => openLook(item)}>
      <div className="look-image"><ProtectedImage path={done ? item.imagePath : undefined} alt={`${item.garmentName} virtual try-on`} preview={preview} /><span className="look-index">{String(index + 1).padStart(2, '0')}</span><span className={`look-status ${done ? 'complete' : item.status === 'failed' ? 'failed' : ''}`}>{preview ? 'EXAMPLE LOOK' : done ? 'READY TO VIEW' : item.status === 'failed' ? 'TRY AGAIN' : 'IN THE FITTING ROOM'}</span><span className="look-open"><Icon name="arrow" /></span></div>
      <div className="look-caption"><div><h3>{item.garmentName}</h3><p>{preview ? 'A generated demo, not your profile' : dateText(item.createdAt) || 'Personalized for you'}</p></div><Icon name="chevron" size={17} /></div>
    </button>;
  }

  return <div className="app-shell">
    {preview && <div className="preview-bar"><span><span className="status-dot" /> Design preview <em>· no account, uploads, or training changes</em></span><div><label className="sr-only" htmlFor="preview-screen">Preview screen</label><select id="preview-screen" value={previewScreen} onChange={event => navigate(event.target.value as Screen)}>{previewScreens.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select><button onClick={() => { setPreview(false); setError(''); setNotice(''); }}>Exit <Icon name="close" size={14} /></button></div></div>}
    <header className="site-header"><button className="wordmark" aria-label="THREAD home" onClick={() => navigate(isMain ? 'scanner' : 'welcome')}>THREAD<span className="brand-asterisk">✳</span></button><span className="header-caption">YOUR FITTING ROOM.</span><div className="header-actions">{(user || preview) && isMain ? <><button className={`desktop-nav ${screen === 'gallery' ? 'selected' : ''}`} onClick={() => navigate('gallery')}>Your looks <span>{generationList.length}</span></button><button className="avatar" aria-label="Your profile" onClick={() => navigate('account')}>{!preview && user?.photoURL ? <img src={user.photoURL} alt="" referrerPolicy="no-referrer" /> : <Icon name="user" size={18} />}</button></> : <span className="header-edition">A NEW WAY TO TRY IT ON <span>↗</span></span>}</div></header>
    {error && <div className="global-message error-message" role="alert"><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss error"><Icon name="close" size={17} /></button></div>}
    {notice && <div className="global-message notice-message" role="status"><span>{notice}</span><button onClick={() => setNotice('')} aria-label="Dismiss message"><Icon name="close" size={17} /></button></div>}
    {pendingGarment && !isMain && screen !== 'loading' && <div className="intent-banner"><Icon name="scan" size={17} /><span>Your scanned piece is waiting. Finish your profile to try it on.</span></div>}

    <main className={`main-content screen-${screen}`}>
      {screen === 'loading' && <div className="loading-screen">{accountError && !authLoading ? <><Icon name="retry" size={30} /><h2>Let’s reconnect your profile.</h2><p role="alert">{accountError}</p><button className="button button-ink" onClick={() => { setAccountError(''); setError(''); setAuthLoading(true); setAuthRetry(value => value + 1); }}>Retry connection <Icon name="retry" size={17} /></button><button className="text-button" disabled={busy} onClick={logout}>Sign out</button></> : <><span className="spinner large" /><p>Opening your fitting room…</p></>}</div>}

      {screen === 'welcome' && <div className="welcome-layout fade-in"><section className="welcome-copy"><div className="editorial-tag"><span className="status-dot" /> PERSONAL STYLE, PERSONALLY YOURS</div><h1>A fitting room.<br />Made for <span className="serif-emphasis">you.</span></h1><p className="hero-description">Found a piece you love?<br />See yourself in it before you decide.</p><div className="welcome-cta"><button className="button button-ink google-button" onClick={login} disabled={busy || !firebaseConfigured || preview}>{busy ? <span className="spinner" /> : <GoogleMark />}Continue with Google<Icon name="arrow" size={18} /></button>{preview ? <button className="text-button" onClick={() => setPreviewScreen('photos')}>Preview the experience <Icon name="arrow" size={17} /></button> : <button className="text-button" onClick={() => { setPreview(true); setPreviewScreen('home'); }}>Explore the demo <Icon name="arrow" size={17} /></button>}</div>{!firebaseConfigured && !preview && <p className="config-note">Live sign-in is not configured on this deployment. Explore the clearly labeled demo instead.</p>}<p className="privacy-line"><Icon name="shield" size={15} /> Your photos. Your private fitting room.</p><div className="how-it-works"><div><span>01</span><p>Make it yours<small>Eight photos. One recent selfie.</small></p></div><div><span>02</span><p>Scan a piece<small>Look for a THREAD QR.</small></p></div><div><span>03</span><p>See the possibility<small>Your own virtual try-on.</small></p></div></div></section><aside className="hero-visual"><div className="hero-topline"><span>THE PERSONAL FITTING ROOM</span><span>EST. 2026</span></div><img src={demoImage} alt="Example virtual try-on of a blue photographic T-shirt" /><div className="hero-sticker"><Icon name="spark" size={21} /><span>Same piece.<br /><strong>Your perspective.</strong></span></div><div className="hero-bottomline"><span>ONE PIECE. A WHOLE NEW POINT OF VIEW.</span><span>01 / 01</span></div><div className="sample-disclaimer">Generated example · not your profile</div></aside></div>}

      {(['measurements', 'photos', 'selfie'] as Screen[]).includes(screen) && <div className="onboarding-layout fade-in">
        <aside className="onboarding-aside"><p className="eyebrow">LET’S MAKE THIS PERSONAL</p><h1>Your style.<br /> Your <span className="serif-emphasis">perspective.</span></h1><p>Set up once. Every piece you discover starts with you.</p>
          <div className="setup-steps">
            <div className={screen === 'photos' ? 'active' : 'done'}><span>{screen !== 'photos' ? <Icon name="check" size={15} /> : '01'}</span><div>Eight photos<small>Start identity training</small></div></div>
            <div className={screen === 'measurements' ? 'active' : screen === 'selfie' ? 'done' : ''}><span>{screen === 'selfie' ? <Icon name="check" size={15} /> : '02'}</span><div>Your details<small>Height & weight</small></div></div>
            <div className={screen === 'selfie' ? 'active' : ''}><span>03</span><div>Recent selfie<small>Your current look</small></div></div>
            <div><span>04</span><div>Your fitting room<small>We prepare your profile</small></div></div>
          </div>
          <div className="setup-reassurance"><Icon name="shield" size={21} /><p>Your photos and selfie stay in your private account. They aren’t shown in the public demo.</p></div>
        </aside>
        <section className="onboarding-card">
          {(screen === 'measurements' || screen === 'selfie') && <div className="background-training-note" role="status"><Icon name={trainingFailed ? 'retry' : 'clock'} size={17} /><div><strong>{preview ? 'Training continues in the background' : trainingFailed ? 'Training needs another try' : trainJob?.status === 'completed' ? 'Your identity training is complete' : trainJob?.status === 'queued' ? 'Your photos are queued for training' : 'Training continues in the background'}</strong><p>{preview ? 'Preview only — no training is running.' : trainingFailed ? trainJob?.error || profile?.identity?.error || 'Please choose your photos again.' : trainJob?.status === 'completed' ? 'Finish your details and reference selfie to open your fitting room.' : 'Keep going with your details while we prepare your identity.'}</p>{!preview && !trainingFailed && trainJob?.status !== 'completed' && <ProgressBar value={trainJob?.progress} label={trainJob?.stage?.replaceAll('_', ' ') || 'Preparing your photos'} />}{trainingFailed && !preview && <button className="text-button" onClick={restartPhotos}>Choose photos again <Icon name="retry" size={14} /></button>}</div></div>}
          {screen === 'measurements' && <>
            <div className="step-kicker">STEP 02 <span>/ 04</span></div><h2>Now, a little<br />about you.</h2><p className="section-description">Just two details to make your profile yours.</p>
            <div className="measurement-system" role="group" aria-label="Measurement units"><button type="button" aria-pressed={measurements.system === 'us'} onClick={() => setMeasurements(current => switchMeasurementSystem(current, 'us'))}>US <span>ft / lb</span></button><button type="button" aria-pressed={measurements.system === 'metric'} onClick={() => setMeasurements(current => switchMeasurementSystem(current, 'metric'))}>Metric <span>cm / kg</span></button></div>
            <form onSubmit={event => { event.preventDefault(); void continueMeasurements(); }}>
              {measurements.system === 'us' ? <div className="measurement-fields us-measurements">
                <fieldset className="height-field"><legend>Height</legend><div className="height-inputs"><label className="unit-input"><span className="sr-only">Height in feet</span><input type="number" inputMode="numeric" min="0" max="8" step="1" placeholder="5" aria-label="Height in feet" value={measurements.fields.feet} onChange={event => setMeasurements(current => editMeasurement(current, 'feet', event.target.value))} required={!preview} /><span>ft</span></label><label className="unit-input"><span className="sr-only">Additional inches</span><input type="number" inputMode="decimal" min="0" max="11.9" step="0.1" placeholder="10" aria-label="Additional inches" value={measurements.fields.inches} onChange={event => setMeasurements(current => editMeasurement(current, 'inches', event.target.value))} /><span>in</span></label></div></fieldset>
                <label>Weight<div className="unit-input"><input type="number" inputMode="decimal" min="55" max="662" step="0.1" placeholder="150" value={measurements.fields.pounds} onChange={event => setMeasurements(current => editMeasurement(current, 'pounds', event.target.value))} required={!preview} /><span>lb</span></div></label>
              </div> : <div className="measurement-fields">
                <label>Height<div className="unit-input"><input type="number" inputMode="decimal" min="80" max="250" step="0.1" placeholder="175" value={measurements.fields.centimeters} onChange={event => setMeasurements(current => editMeasurement(current, 'centimeters', event.target.value))} required={!preview} /><span>cm</span></div></label>
                <label>Weight<div className="unit-input"><input type="number" inputMode="decimal" min="25" max="300" step="0.1" placeholder="70" value={measurements.fields.kilograms} onChange={event => setMeasurements(current => editMeasurement(current, 'kilograms', event.target.value))} required={!preview} /><span>kg</span></div></label>
              </div>}
              <div className="info-note"><Icon name="user" size={19} /><p>These details belong to your profile. Try-on images are a visual preview, not a measurement or fit guarantee.</p></div><button type="submit" className="button button-ink full-width" disabled={!preview && (!measurementValid || busy)}>{busy ? 'Saving details…' : 'Next: your recent selfie'} <Icon name="arrow" /></button>
            </form><p className="form-footnote">Switch units at any time. Your measurements stay the same.</p>
          </>}
          {screen === 'photos' && <>
            <div className="step-kicker">STEP 01 <span>/ 04</span></div><div className="photo-title"><h2>Eight photos.<br />Entirely you.</h2><span className="photo-count">{photos.length}<span> / 8</span></span></div>
            <p className="section-description">Mix front and side views. Keep your face clear, use varied lighting, and leave filters out. Each image needs at least 384 pixels on both sides.</p><div className="photo-tips"><span><Icon name="check" size={13} /> One person</span><span><Icon name="check" size={13} /> Clear face</span><span><Icon name="check" size={13} /> Different angles</span></div>
            <div className="photo-grid" onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); addPhotos(event.dataTransfer.files); }}>{Array.from({ length: 8 }, (_, index) => <div className={photos[index] ? 'photo-slot has-photo' : 'photo-slot'} key={index}>{photos[index] ? <><img src={photoUrls[index]} alt={'Your selected photo ' + (index + 1)} /><span className="photo-number">{String(index + 1).padStart(2, '0')}</span><button disabled={uploading} onClick={() => setPhotos(current => current.filter((_, i) => i !== index))} aria-label={'Remove photo ' + (index + 1)}><Icon name="close" size={13} /></button></> : <button disabled={preview || uploading} onClick={() => photoInput.current?.click()} aria-label={'Add photo ' + (index + 1)}><Icon name="plus" size={24} /><span>{String(index + 1).padStart(2, '0')}</span></button>}</div>)}</div>
            <input ref={photoInput} type="file" accept="image/jpeg,image/png,image/webp" multiple hidden disabled={preview || uploading} onChange={event => addPhotos(event.target.files)} /><div className="photo-toolbar"><button className="text-button" disabled={preview || uploading || photos.length === 8} onClick={() => photoInput.current?.click()}><Icon name="upload" size={17} />{photos.length ? 'Add more photos' : 'Choose your photos'}</button><span>JPG, PNG, WebP · 20 MB max</span></div>
            <div className="selection-note"><Icon name="spark" size={18} /><p>We’ll select the best <strong>five of your eight</strong> and start training your identity right away. You can finish your details and recent selfie while training runs in the background.</p></div>
            <label className="consent-check"><input type="checkbox" checked={trainingConsent} disabled={uploading} onChange={event => setTrainingConsent(event.target.checked)} /><span>These eight photos are of me. I agree to use them to train my personal identity for my try-ons.</span></label>
            {uploading && <ProgressBar value={uploadProgress} label="Uploading your eight photos" />}
            <button className="button button-ink full-width next-selfie" onClick={beginTraining} disabled={!preview && (photos.length !== 8 || !trainingConsent || uploading)}>{uploading ? 'Uploading your photos…' : preview ? 'Preview the next step' : 'Start training & continue'} <Icon name="arrow" size={18} /></button><p className="form-footnote">Your recent selfie comes later and stays separate from these training photos.</p>
          </>}
          {screen === 'selfie' && <>
            <div className="step-kicker"><button disabled={uploading} onClick={() => navigate('measurements')} aria-label="Back to measurements"><Icon name="back" size={16} /></button> STEP 03 <span>/ 04</span></div><h2>A recent selfie.<br />Your current look.</h2><p className="section-description">Take one now, or choose a recent selfie that shows your current haircut and facial hair. We’ll use this photo as the face reference for your try-ons.</p>
            <fieldset className="selfie-fields" disabled={uploading}><SelfieCapture value={preview ? null : selfie} onChange={next => { if (!uploading) { setSelfie(next); setConsent(false); } }} preview={preview} />
            <label className="consent-check"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} /><span>This selfie is of me and matches my current haircut and facial hair. I agree to use it as my try-on face reference.</span></label></fieldset>
            {uploading && <ProgressBar value={uploadProgress} label="Saving your reference selfie" />}
            <button className="button button-ink full-width" onClick={finishReference} disabled={!preview && (!selfie || !consent || !setup.measurementsSaved || uploading || trainingFailed)}>{preview ? 'Preview profile preparation' : uploading ? 'Saving your selfie…' : 'Finish my setup'}<Icon name="arrow" /></button><p className="form-footnote">A recent selfie is required. Choose the photo that best represents how you look today.</p>
          </>}
        </section>
      </div>}

      {screen === 'preparing' && <div className="preparation-layout fade-in"><div className="preparation-art"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="profile-sculpture"><Icon name="user" size={68} /></div><span className="floating-chip chip-one"><Icon name="check" size={14} /> YOUR PERSPECTIVE</span><span className="floating-chip chip-two"><Icon name="spark" size={14} /> PERSONALLY YOURS</span></div><section className="preparation-copy">
        <p className="eyebrow">STEP 04 / YOUR PERSONAL PROFILE</p>
        <h1>{(trainingFailed || finalizationFailed) && !preview ? <>A little pause.<br />Let’s try <span className="serif-emphasis">again.</span></> : <>Your details are in.<br />Your look is <span className="serif-emphasis">on its way.</span></>}</h1>
        <p className="hero-description">{preview ? 'This is the preparation screen preview. No photos have been uploaded and no training is running.' : trainingFailed ? trainJob?.error || profile?.identity?.error || 'Training could not finish. Please choose your eight photos again.' : finalizationFailed ? finalizeJob?.error || 'Your reference could not be attached. You can retry or choose another selfie without training again.' : trainJob?.status === 'completed' ? finalizeJob?.message || 'Your identity is trained. We’re attaching your current-look reference to finish your fitting room.' : trainJob?.message || 'Your details and selfie are saved. Your identity training is continuing in the background.'}</p>
        {!preview && !trainingFailed && !finalizationFailed && <ProgressBar value={trainJob?.status === 'completed' ? finalizeJob?.progress : trainJob?.progress} label={trainJob?.status === 'completed' ? finalizeJob?.stage?.replaceAll('_', ' ') || 'Finishing your fitting room' : trainJob?.stage?.replaceAll('_', ' ') || 'Training your identity'} />}
        <div className="preparation-list"><div className="done"><span><Icon name="check" size={14} /></span><p>Your photos and details<small>{preview ? 'Saved details appear here' : 'Saved to your private account'}</small></p></div><div className={trainJob?.status === 'completed' ? 'done' : 'current'}><span>{trainJob?.status === 'completed' ? <Icon name="check" size={14} /> : '02'}</span><p>Your personal identity<small>{preview ? 'Training progress appears here' : trainJob?.status === 'completed' ? 'Training complete' : 'Training from your strongest five photos'}</small></p></div><div className={finalizeJob?.status === 'running' ? 'current' : ''}><span>03</span><p>Your current look<small>{preview ? 'Reference status appears here' : setup.referenceSaved ? 'Reference selfie saved for your try-ons' : 'Preparing your try-on reference'}</small></p></div></div>
        {!preview && profile?.identity?.selectedPhotos?.length ? <div className="selected-photo-review"><p className="eyebrow">PHOTO SELECTION</p><div>{profile.identity.selectedPhotos.map(photo => <span key={photo.index} className={photo.selected ? 'chosen' : ''} title={photo.reason}>{photo.selected && <Icon name="check" size={12} />}Photo {photo.index + 1}</span>)}</div></div> : null}
        {trainingFailed && !preview && <button className="button button-ink" onClick={restartPhotos}>Choose photos & retry <Icon name="retry" size={17} /></button>}
        {!preview && !trainingFailed && (finalizationFailed || (setup.canFinalize && finalizationError)) && <><button className="button button-ink" disabled={finalizing} onClick={() => { setError(''); setFinalizationRetry(current => current + 1); }}>{finalizing ? 'Retrying…' : 'Retry finishing setup'} <Icon name="retry" size={17} /></button><button className="text-button reference-retry" disabled={finalizing} onClick={() => { setPhase('selfie'); setSelfie(null); setConsent(false); setError(''); }}>Choose another selfie <Icon name="camera" size={17} /></button></>}
        {preview && <button className="button button-ink" onClick={() => setPreviewScreen('home')}>Preview the fitting room <Icon name="arrow" /></button>}
        <p className="microcopy">{preview ? 'Design preview only. Progress is not simulated.' : 'Your progress is saved. You can come back to this page while training finishes.'}</p>
      </section></div>}

      {screen === 'home' && <div className="home-layout fade-in"><section className="home-primary"><div className="hello-line"><span className="status-dot" />{preview ? 'YOUR FITTING ROOM, PREVIEWED' : `YOUR FITTING ROOM IS READY`}</div><h1>{preview ? 'A new way to' : `Hey ${firstName}.`}<br />{preview ? <span className="serif-emphasis">see yourself.</span> : <>Make it <span className="serif-emphasis">yours.</span></>}</h1><p className="hero-description">Your next favorite piece is out there.<br />Let’s see it on you.</p><button className="scan-cta" onClick={() => navigate('scanner')}><span className="scan-cta-icon"><Icon name="scan" size={28} /></span><span><strong>{generations.length && !preview ? 'Scan your next piece' : 'Scan your first piece'}</strong><small>Find a THREAD QR. Start your try-on.</small></span><Icon name="arrow" size={23} /></button>{pendingGarment && !preview && <div className="pending-garment"><Icon name="scan" /><div><strong>{garmentLoading ? 'Finding your piece…' : garment?.name || 'Scanned piece'}</strong><p>{requesting ? 'Starting your try-on automatically…' : error ? 'We couldn’t start this try-on yet.' : 'Ready to enter your fitting room.'}</p>{error && <button className="text-button" onClick={() => retryGarment(pendingGarment)}>Try again <Icon name="retry" size={14} /></button>}</div></div>}{showGenerationProgress && <div className="job-card"><div className="job-card-heading"><span className="spinner" /><div><strong>{activeJob?.status === 'queued' ? 'Your piece is in line' : 'Your look is taking shape'}</strong><p>{activeJob?.message || 'We’re creating your personalized try-on. This can take a few minutes.'}</p></div></div><ProgressBar value={activeJob?.progress} label={activeJob?.stage?.replaceAll('_', ' ') || (requesting ? 'Starting your try-on' : 'Preparing your look')} /><button className="text-button" onClick={() => navigate('gallery')}>View your looks <Icon name="arrow" size={16} /></button></div>}<div className="home-note"><span>SCAN. TRY. DISCOVER.</span><p>No changing room queue.<br />Just a different point of view.</p></div></section><aside className="home-look"><div className="section-heading compact"><div><p className="eyebrow">{preview || !generations.length ? 'A LITTLE INSPIRATION' : 'YOUR LATEST LOOK'}</p><h2>{preview || !generations.length ? 'The possibilities look good.' : 'Made for your mood.'}</h2></div></div>{preview ? lookCard(demoGeneration, 0) : generations.length ? lookCard(generations[0], 0) : <div className="inspiration-card"><img src={demoImage} alt="Example generated try-on with a photographic T-shirt" /><span className="example-label">GENERATED EXAMPLE</span><div><p>Your first look<br />starts with a scan.</p><span>This example is not your profile.</span></div></div>}</aside></div>}

      {screen === 'scanner' && <div className="scanner-layout"><aside className="scanner-copy"><p className="eyebrow">FROM THE RACK TO YOUR REALITY</p><h1>{generations.length && !preview ? 'Scan your next piece.' : 'Scan your first piece.'}<br /><span className="serif-emphasis">See it on you.</span></h1><p>Scan a registered garment’s QR code and we’ll start your personalized try-on automatically.</p><div className="scanner-instructions"><div><span>01</span><p>Find the THREAD QR<br /><small>On the garment’s tag or display.</small></p></div><div><span>02</span><p>Give it a quick scan<br /><small>Keep the full code inside the frame.</small></p></div><div><span>03</span><p>We’ll take it from here<br /><small>Your look will appear in Your looks.</small></p></div></div></aside><Scanner onDetected={scan} onClose={() => navigate('home')} /></div>}

      {screen === 'gallery' && <section className="gallery-page fade-in"><div className="gallery-heading"><div><p className="eyebrow">YOUR PERSONAL EDIT</p><h1>Your looks<span className="count-sup">{generationList.length.toString().padStart(2, '0')}</span></h1><p>Every piece. Every possibility. All in one place.</p></div><button className="button button-ink" onClick={() => navigate('scanner')}><Icon name="scan" size={18} /> Scan a piece</button></div>{generationList.length ? <div className="gallery-grid">{generationList.map(lookCard)}</div> : <div className="empty-gallery"><div className="empty-look-frame"><Icon name="grid" size={34} /></div><h2>A little empty.<br />A lot of possibility.</h2><p>Scan your first piece to start building your personal collection of looks.</p><button className="button button-ink" onClick={() => navigate('scanner')}>Find your first look <Icon name="arrow" /></button></div>}</section>}

      {screen === 'detail' && <section className="detail-page fade-in"><button className="text-button back-link" onClick={() => navigate('gallery')}><Icon name="back" size={17} /> Back to your looks</button>{chosenGeneration ? <div className="detail-layout"><div className="detail-image"><ProtectedImage path={chosenGeneration.imagePath} alt={`${chosenGeneration.garmentName} personalized try-on`} preview={preview} />{preview && <span className="example-label">GENERATED DEMO · NOT YOUR PROFILE</span>}</div><div className="detail-copy"><p className="eyebrow">{preview ? 'THE DEMO EDIT' : 'YOUR PERSONAL TRY-ON'}</p><h1>{chosenGeneration.garmentName}</h1><div className="detail-divider" /><p className="detail-description">{chosenGeneration.status === 'failed' ? chosenGeneration.error || 'This look could not finish. Try generating it again.' : chosenGeneration.status === 'completed' ? 'A new perspective on a piece you love. Take a closer look, save it, and keep exploring.' : 'Your personalized look is on its way. We’ll keep the progress in your fitting room.'}</p><dl className="detail-facts"><div><dt>THE EXPERIENCE</dt><dd>{preview ? 'Curated example' : 'Personalized virtual try-on'}</dd></div><div><dt>STATUS</dt><dd><span className={`status-pill ${chosenGeneration.status === 'failed' ? 'failed' : ''}`}>{preview ? 'Preview' : chosenGeneration.status.replaceAll('_', ' ')}</span></dd></div>{dateText(chosenGeneration.createdAt) && <div><dt>CREATED</dt><dd>{dateText(chosenGeneration.createdAt)}</dd></div>}</dl>{chosenGeneration.status === 'completed' && <button className="button button-ink full-width" onClick={downloadLook} disabled={downloading}>{downloading ? <span className="spinner" /> : <Icon name="download" size={18} />}{preview ? 'Download demo image' : chosenGeneration.image4kPath ? 'Save 4K image' : 'Save your look'}</button>}{(chosenGeneration.status === 'failed' || chosenGeneration.status === 'completed') && <button className="button button-outline full-width" onClick={() => retryGarment(chosenGeneration.garmentId)}><Icon name="retry" size={17} />{chosenGeneration.status === 'failed' ? 'Try again' : 'Generate another look'}</button>}<p className="detail-disclaimer">An AI visual preview, not a physical fit prediction. Identity, garment prints, and fine details may vary.</p><button className="text-button" onClick={() => navigate('scanner')}>On to the next piece <Icon name="arrow" size={17} /></button></div></div> : <div className="empty-gallery"><h2>Choose a look first.</h2><button className="button button-ink" onClick={() => navigate('gallery')}>Your looks <Icon name="arrow" /></button></div>}</section>}

      {screen === 'account' && <section className="account-page fade-in"><p className="eyebrow">THE PERSON BEHIND THE LOOKS</p><h1>Your profile.</h1><div className="account-card"><div className="account-identity"><span className="avatar large-avatar"><Icon name="user" size={26} /></span><div><h2>{preview ? 'Design preview' : user?.displayName || 'Your account'}</h2><p>{preview ? 'No signed-in account or trained identity' : user?.email}</p></div><span className="status-pill">{preview ? 'Preview' : ready ? 'Ready to try on' : 'Setting up'}</span></div><dl className="account-measurements"><div><dt>Height</dt><dd>{preview ? '—' : formatHeight(profile?.heightCm, profile?.measurementSystem || 'us')}</dd></div><div><dt>Weight</dt><dd>{preview ? '—' : formatWeight(profile?.weightKg, profile?.measurementSystem || 'us')}</dd></div><div><dt>Your looks</dt><dd>{preview ? '—' : generations.length}</dd></div></dl><p className="account-note">Your photos and trained profile belong to your account. Measurements provide profile context; the current try-on uses a fixed pose and body.</p><button className="text-button" onClick={() => { if (preview) setPreviewScreen('photos'); else restartPhotos(); }}>Update profile & retrain <Icon name="arrow" size={16} /></button></div><button className="text-button logout-button" onClick={logout} disabled={busy}><Icon name="logout" size={17} />{preview ? 'Leave design preview' : 'Sign out'}</button></section>}
    </main>
    {isMain && <nav className="mobile-nav" aria-label="Main navigation"><button className={screen === 'home' ? 'active' : ''} onClick={() => navigate('home')}><Icon name="spark" size={21} /><span>For you</span></button><button className={`nav-scan ${screen === 'scanner' ? 'active' : ''}`} onClick={() => navigate('scanner')}><Icon name="scan" size={23} /><span>Scan</span></button><button className={screen === 'gallery' || screen === 'detail' ? 'active' : ''} onClick={() => navigate('gallery')}><Icon name="grid" size={20} /><span>Your looks</span></button></nav>}
    <footer className="site-footer"><span>THREAD<span className="brand-asterisk">✳</span></span><p>YOUR STYLE. YOUR PERSPECTIVE.</p><span>BUILT FOR POSSIBILITY ↗</span></footer>
  </div>;
}

export default App;
