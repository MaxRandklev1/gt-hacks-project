import { useEffect, useRef, useState } from 'react';
import {
  firebaseConfigured, signInWithGoogle, signOut, subscribeAuth, subscribeProfile,
  subscribeJobs, subscribeGenerations, getGarment, getPrivateImage,
  startEnrollment, saveMeasurements, requestGeneration,
  type SessionUser, type UserProfile, type Job, type Garment, type Generation,
} from './lib/client';
import { parseGarmentCode, readPendingGarment, rememberGarment, clearPendingGarment } from './lib/qr';
import { GoogleMark, Icon } from './components/Icon';
import { Scanner, type ScannerHandle } from './components/Scanner';
import { GarmentCatalog } from './components/GarmentCatalog';
import { SelfieCapture, type CapturedSelfie } from './components/SelfieCapture';
import { EnrollmentProgress } from './components/EnrollmentProgress';
import { createMeasurements, editMeasurement, formatHeight, formatWeight, measurementsValid, switchMeasurementSystem } from './lib/measurements';
import { validateReferenceSelfie } from './lib/validation';
import { onboardingProgress, replacementSetupScreen, needsPersonalLookReview, readOnboardingChoices, saveOnboardingChoices, type OnboardingChoices } from './lib/onboarding';
import './styles.css';

type View = 'home' | 'scanner' | 'gallery' | 'detail' | 'account';
type Screen = View | 'welcome' | 'measurements' | 'selfie' | 'preparing' | 'loading';
const demoImage = '/demo/try-on-final-1024.png';
const demoGeneration: Generation = { id: 'design-preview', garmentId: 'preview-piece', garmentName: 'The photographic tee', status: 'completed', imagePath: undefined };
const previewScreens: { id: Screen; label: string }[] = [
  { id: 'welcome', label: 'Welcome' },
  { id: 'measurements', label: 'Your profile' }, { id: 'selfie', label: 'Your selfie' }, { id: 'preparing', label: 'Setting up' },
  { id: 'home', label: 'Fitting room' }, { id: 'scanner', label: 'Choose a piece' },
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

function ProgressBar({ value, label }: { value?: number | null; label: string }) {
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
  const [generations, setGenerations] = useState<Generation[]>([]);
  const [view, setView] = useState<View>('scanner');
  const scannerRef = useRef<ScannerHandle>(null);
  const [preview, setPreview] = useState(false);
  const [previewScreen, setPreviewScreen] = useState<Screen>('welcome');
  const [phase, setPhase] = useState<'measurements' | 'selfie' | null>(null);
  const [editingProfile, setEditingProfile] = useState(false);
  const [measurements, setMeasurements] = useState(createMeasurements);
  const [selfie, setSelfie] = useState<CapturedSelfie | null>(null);
  const [submittedSelfieUrl, setSubmittedSelfieUrl] = useState<string>();
  useEffect(() => () => { if (submittedSelfieUrl) URL.revokeObjectURL(submittedSelfieUrl); }, [submittedSelfieUrl]);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number>();
  const [uploading, setUploading] = useState(false);
  const [enrollmentRequestId, setEnrollmentRequestId] = useState<string>();
  const [onboardingChoices, setOnboardingChoices] = useState<OnboardingChoices>({});
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pendingGarment, setPendingGarment] = useState<string | null>(initialPending);
  const [garment, setGarment] = useState<Garment | null>(null);
  const [garmentLoading, setGarmentLoading] = useState(false);
  const [garmentLookupAttempt, setGarmentLookupAttempt] = useState(0);
  const [requesting, setRequesting] = useState(false);
  const [requestedJob, setRequestedJob] = useState<string>();
  const [selectedGeneration, setSelectedGeneration] = useState<Generation | null>(null);
  const [downloading, setDownloading] = useState(false);
  const requested = useRef(new Set<string>());
  const sessionUid = useRef<string | null>(null);
  const sessionEpoch = useRef(0);

  useEffect(() => {
    if (!firebaseConfigured || preview) return;
    setAuthLoading(true);
    const stop = subscribeAuth(next => {
      sessionUid.current = next?.uid || null;
      setUser(next); setAuthLoading(false); setProfileLoading(Boolean(next)); setJobsLoading(Boolean(next));
      setAccountError(''); setError(''); setNotice('');
      setProfile(null); setJobs([]); setGenerations([]); setSelectedGeneration(null);
      setConsent(false); setMeasurements(createMeasurements()); setBodyStyle(undefined); setSelfie(null);
      setSubmittedSelfieUrl(undefined);
      setOnboardingChoices(next ? readOnboardingChoices(next.uid) : {});
      setEditingProfile(false); setPhase(null); setEnrollmentRequestId(undefined);
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
    }).catch(() => { if (current) setError('We couldn’t load this piece. Check your connection, choose another piece, or try again.'); }).finally(() => { if (current) setGarmentLoading(false); });
    return () => { current = false; };
  }, [pendingGarment, user?.uid, preview, garmentLookupAttempt]);

  const identityStatus = profile?.identity?.status;
  const setup = onboardingProgress(profile, jobs, enrollmentRequestId, onboardingChoices.keptPreviousEnrollmentId);
  const ready = setup.ready;
  const reviewingLook = ready && needsPersonalLookReview(profile, onboardingChoices.pendingLookReview);
  const enrollJob = setup.enrollment;
  const enrollmentFailed = setup.enrollmentFailed;
  const setupLoaded = !authLoading && !profileLoading && !jobsLoading && !accountError;
  useEffect(() => {
    if (ready && enrollmentRequestId && profile?.identity?.version === enrollmentRequestId) setEnrollmentRequestId(undefined);
  }, [ready, enrollmentRequestId, profile?.identity?.version]);
  useEffect(() => {
    if (preview || !setupLoaded || !user || !ready || reviewingLook || !garment || garment.id !== pendingGarment || editingProfile) return;
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
  }, [garment, pendingGarment, user?.uid, ready, reviewingLook, preview, editingProfile, setupLoaded]);

  const activeJob = jobs.find(job => job.kind === 'generate' && (job.status === 'running' || job.status === 'queued'));
  const jobFromRequest = jobs.find(job => job.id === requestedJob);
  const awaitingJob = Boolean(requestedJob && !jobFromRequest);
  const showGenerationProgress = !preview && (requesting || activeJob || awaitingJob);
  const generationList = preview ? [demoGeneration] : generations;
  const chosenGeneration = preview ? demoGeneration : (generations.find(item => item.id === selectedGeneration?.id) || selectedGeneration);
  const screen: Screen = preview ? previewScreen : authLoading ? 'loading' : !user ? 'welcome' : !setupLoaded ? 'loading' : editingProfile && phase ? phase : reviewingLook ? 'home' : ready ? view : phase || (setup.screen === 'ready' ? view : setup.screen);
  const firstName = user?.displayName?.trim().split(/\s+/)[0] || 'there';
  const isMain = ['home', 'scanner', 'gallery', 'detail', 'account'].includes(screen);
  const [bodyStyle, setBodyStyle] = useState<'male' | 'female' | undefined>();
  useEffect(() => { setBodyStyle(profile?.bodyStyle); }, [profile?.bodyStyle]);
  const measurementValid = measurementsValid(measurements) && Boolean(bodyStyle);
  const accountBodyNote = preview
    ? 'Height and weight help select the closest of five body templates. This is an approximate appearance preview, not an exact fit prediction.'
    : profile?.identity?.bodyTemplate?.policyVersion === 'bmi-visual-v2'
      ? 'Your saved look uses one of five body templates selected from your height and weight. Updated measurements take effect after you rebuild your look; this is an appearance preview, not exact fit.'
      : profile?.identity?.bodyTemplate?.policyVersion === 'bmi-visual-v1'
        ? 'Your saved look uses the earlier body ranges; update your details and rebuild it to use the revised sizing. This is an approximate appearance preview, not a clothing-fit prediction.'
        : 'Your saved look still uses the earlier shared body. Update your details and rebuild your look to select from five body templates; the preview does not predict exact fit.';

  function navigate(next: Screen) {
    setError(''); setNotice('');
    if (preview) setPreviewScreen(next);
    else if (next === 'measurements' || next === 'selfie') { setPhase(next); }
    else if (next !== 'welcome' && next !== 'loading' && next !== 'preparing') setView(next);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  function openScanTab() {
    if (screen === 'scanner') scannerRef.current?.start();
    else navigate('scanner');
  }
  async function login() {
    if (preview || !firebaseConfigured) return;
    setBusy(true); setError('');
    try { await signInWithGoogle(); } catch (cause) { setError(errorText(cause)); } finally { setBusy(false); }
  }
  async function logout() {
    if (preview) { setPreview(false); setPreviewScreen('welcome'); return; }
    setBusy(true); setError('');
    try { await signOut(); setEditingProfile(false); setSelfie(null); setConsent(false); } catch (cause) { setError(errorText(cause)); } finally { setBusy(false); }
  }
  async function continueMeasurements() {
    if (preview) { setPreviewScreen('selfie'); return; }
    if (busy || !measurementValid) return;
    const epoch = sessionEpoch.current;
    setBusy(true); setError('');
    try {
      const saved = { heightCm: measurements.heightCm!, weightKg: measurements.weightKg!, measurementSystem: measurements.system, bodyStyle: bodyStyle! };
      await saveMeasurements(saved);
      if (sessionEpoch.current !== epoch) return;
      setProfile(current => current ? { ...current, ...saved } : current);
      navigate('selfie');
    } catch (cause) { if (sessionEpoch.current === epoch) setError(errorText(cause)); }
    finally { if (sessionEpoch.current === epoch) setBusy(false); }
  }
  async function finishSetup() {
    if (preview) { setPreviewScreen('preparing'); return; }
    if (uploading) return;
    if (!selfie || !consent) { setError('Take or choose a selfie and confirm that it is you.'); return; }
    try { validateReferenceSelfie(selfie.file, { source: selfie.source, selectedAt: selfie.selectedAt, capturedAt: selfie.capturedAt }); } catch (cause) { setError(errorText(cause)); return; }
    const epoch = sessionEpoch.current;
    setError(''); setUploading(true); setUploadProgress(0);
    try {
      const jobId = await startEnrollment({ selfie: selfie.file, selfieSource: selfie.source, selfieSelectedAt: selfie.selectedAt, selfieCapturedAt: selfie.capturedAt, consent }, progress => { if (sessionEpoch.current === epoch) setUploadProgress(progress); });
      if (sessionEpoch.current !== epoch) return;
      setSubmittedSelfieUrl(URL.createObjectURL(selfie.file));
      updateOnboardingChoices({ pendingLookReview: jobId });
      setEnrollmentRequestId(jobId); setPhase(null); setEditingProfile(false); setSelfie(null); setConsent(false);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (cause) { if (sessionEpoch.current === epoch) setError(errorText(cause)); }
    finally { if (sessionEpoch.current === epoch) setUploading(false); }
  }
  function retakeSelfie() {
    setEditingProfile(true); setPhase(replacementSetupScreen(profile)); setSelfie(null); setConsent(false); setEnrollmentRequestId(undefined); setError('');
  }
  function updateDetailsAndLook() {
    if (preview) { setPreviewScreen('measurements'); return; }
    retakeSelfie(); setPhase('measurements');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  function updateOnboardingChoices(choices: OnboardingChoices) {
    setOnboardingChoices(choices);
    if (user) saveOnboardingChoices(user.uid, choices);
  }
  function keepPreviousLook() {
    if (!setup.canKeepPreviousLook || !enrollJob) return;
    updateOnboardingChoices({ keptPreviousEnrollmentId: enrollJob.id });
    setEnrollmentRequestId(undefined); setSubmittedSelfieUrl(undefined); setPhase(null); setView('home');
    setNotice('Your replacement selfie was not used. You chose to keep your previous look.');
  }
  function approvePersonalLook() {
    updateOnboardingChoices({});
    setSubmittedSelfieUrl(undefined); setView(pendingGarment ? 'home' : 'scanner');
  }
  function scan(value: string) {
    try {
      const id = parseGarmentCode(value);
      if (preview) { setNotice('Preview result — no garment request was sent.'); setPreviewScreen('detail'); return; }
      retryGarment(id);
    } catch (cause) { setError(errorText(cause)); }
  }
  function retryGarment(id: string) {
    if (preview) { setPreviewScreen('detail'); setNotice('Preview only — no generation was started.'); return; }
    requested.current.delete(`${user?.uid}:${id}`);
    rememberGarment(id); setGarment(null); setPendingGarment(id); setView('home'); setError('');
    // Retry the lookup even when the same pending piece previously failed.
    setGarmentLookupAttempt(value => value + 1);
  }
  function openLook(item: Generation) { setSelectedGeneration(item); navigate('detail'); }
  async function downloadLook() {
    if (!chosenGeneration) return;
    if (preview) { const link = document.createElement('a'); link.href = '/demo/try-on-final-4k.png'; link.download = 'thread-demo-4k.png'; link.click(); return; }
    const path = chosenGeneration.image2kPath || chosenGeneration.image4kPath || chosenGeneration.imagePath;
    if (!path) return;
    setDownloading(true); setError('');
    const epoch = sessionEpoch.current;
    try {
      const url = await getPrivateImage(path);
      if (sessionEpoch.current !== epoch) { URL.revokeObjectURL(url); return; }
      const link = document.createElement('a'); link.href = url; link.download = `thread-${chosenGeneration.id}.${path.endsWith('.jpg') ? 'jpg' : 'png'}`; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 3000);
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
    <header className="site-header"><button className="wordmark" aria-label="THREAD home" onClick={() => navigate(isMain ? 'scanner' : 'welcome')}>THREAD<span className="brand-asterisk">✳</span></button><span className="header-caption">YOUR FITTING ROOM.</span><div className="header-actions">{(user || preview) && isMain && <><button className={`desktop-nav ${screen === 'gallery' ? 'selected' : ''}`} onClick={() => navigate('gallery')}>Your looks <span>{generationList.length}</span></button><button className="avatar" aria-label="Your profile" onClick={() => navigate('account')}>{!preview && user?.photoURL ? <img src={user.photoURL} alt="" referrerPolicy="no-referrer" /> : <Icon name="user" size={18} />}</button></>}</div></header>
    {error && <div className="global-message error-message" role="alert"><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss error"><Icon name="close" size={17} /></button></div>}
    {notice && <div className="global-message notice-message" role="status"><span>{notice}</span><button onClick={() => setNotice('')} aria-label="Dismiss message"><Icon name="close" size={17} /></button></div>}
    {pendingGarment && !isMain && screen !== 'loading' && <div className="intent-banner"><Icon name="scan" size={17} /><span>Your scanned piece is waiting. Finish your profile to try it on.</span></div>}

    <main className={`main-content screen-${screen}`}>
      {screen === 'loading' && <div className="loading-screen">{accountError && !authLoading ? <><Icon name="retry" size={30} /><h2>Let’s reconnect your profile.</h2><p role="alert">{accountError}</p><button className="button button-ink" onClick={() => { setAccountError(''); setError(''); setAuthLoading(true); setAuthRetry(value => value + 1); }}>Retry connection <Icon name="retry" size={17} /></button><button className="text-button" disabled={busy} onClick={logout}>Sign out</button></> : <><span className="spinner large" /><p>Opening your fitting room…</p></>}</div>}

      {screen === 'welcome' && <div className="welcome-layout fade-in"><section className="welcome-copy"><div className="editorial-tag"><span className="status-dot" /> PERSONAL STYLE, PERSONALLY YOURS</div><h1>A fitting room.<br />Made for <span className="serif-emphasis">you.</span></h1><p className="hero-description">Found a piece you love?<br />See yourself in it before you decide.</p><div className="welcome-cta"><button className="button button-ink google-button" onClick={login} disabled={busy || !firebaseConfigured || preview}>{busy ? <span className="spinner" /> : <GoogleMark />}Continue with Google<Icon name="arrow" size={18} /></button>{preview ? <button className="text-button" onClick={() => setPreviewScreen('measurements')}>Preview the experience <Icon name="arrow" size={17} /></button> : <button className="text-button" onClick={() => { setPreview(true); setPreviewScreen('home'); }}>Explore the demo <Icon name="arrow" size={17} /></button>}</div>{!firebaseConfigured && !preview && <p className="config-note">Live sign-in is not configured on this deployment. Explore the clearly labeled demo instead.</p>}<p className="privacy-line"><Icon name="shield" size={15} /> Your photos. Your private fitting room.</p><div className="how-it-works"><div><span>01</span><p>Make it yours<small>One selfie. Two details.</small></p></div><div><span>02</span><p>Scan a piece<small>Look for a THREAD QR.</small></p></div><div><span>03</span><p>See the possibility<small>Your own virtual try-on.</small></p></div></div></section></div>}

      {(['measurements', 'selfie'] as Screen[]).includes(screen) && <div className="onboarding-layout fade-in">
        <aside className="onboarding-aside"><p className="eyebrow">LET’S MAKE THIS PERSONAL</p><h1>Your style.<br /> Your <span className="serif-emphasis">perspective.</span></h1><p>Add your details and a recent selfie. We’ll create your look before your first try-on.</p>
          <div className="setup-steps">
            <div className={screen === 'measurements' ? 'active' : 'done'}><span>{screen !== 'measurements' ? <Icon name="check" size={15} /> : '01'}</span><div>Your details<small>Height & weight</small></div></div>
            <div className={screen === 'selfie' ? 'active' : ''}><span>02</span><div>One selfie<small>Your face for every look</small></div></div>
            <div><span>03</span><div>Your fitting room<small>Review your look</small></div></div>
          </div>
          <div className="setup-reassurance"><Icon name="shield" size={21} /><p>Your selfie stays in your private account. It isn’t shown in the public demo.</p></div>
        </aside>
        <section className="onboarding-card">
          {screen === 'measurements' && <>
            <div className="step-kicker">STEP 01 <span>/ 03</span></div><h2>First, a little<br />about you.</h2><p className="section-description">Choose a body style and add your height and weight.</p>
            <div className="body-style" role="group" aria-label="Body style"><span className="body-style-label">Body style</span><div className="measurement-system"><button type="button" aria-pressed={bodyStyle === 'male'} onClick={() => setBodyStyle('male')}>Male</button><button type="button" aria-pressed={bodyStyle === 'female'} onClick={() => setBodyStyle('female')}>Female</button></div></div><div className="measurement-system" role="group" aria-label="Measurement units"><button type="button" aria-pressed={measurements.system === 'us'} onClick={() => setMeasurements(current => switchMeasurementSystem(current, 'us'))}>US <span>ft / lb</span></button><button type="button" aria-pressed={measurements.system === 'metric'} onClick={() => setMeasurements(current => switchMeasurementSystem(current, 'metric'))}>Metric <span>cm / kg</span></button></div>
            <form onSubmit={event => { event.preventDefault(); void continueMeasurements(); }}>
              {measurements.system === 'us' ? <div className="measurement-fields us-measurements">
                <fieldset className="height-field"><legend>Height</legend><div className="height-inputs"><label className="unit-input"><span className="sr-only">Height in feet</span><input type="number" inputMode="numeric" min="0" max="8" step="1" placeholder="5" aria-label="Height in feet" value={measurements.fields.feet} onChange={event => setMeasurements(current => editMeasurement(current, 'feet', event.target.value))} required={!preview} /><span>ft</span></label><label className="unit-input"><span className="sr-only">Additional inches</span><input type="number" inputMode="decimal" min="0" max="11.9" step="0.1" placeholder="10" aria-label="Additional inches" value={measurements.fields.inches} onChange={event => setMeasurements(current => editMeasurement(current, 'inches', event.target.value))} /><span>in</span></label></div></fieldset>
                <label>Weight<div className="unit-input"><input type="number" inputMode="decimal" min="55" max="662" step="0.1" placeholder="150" value={measurements.fields.pounds} onChange={event => setMeasurements(current => editMeasurement(current, 'pounds', event.target.value))} required={!preview} /><span>lb</span></div></label>
              </div> : <div className="measurement-fields">
                <label>Height<div className="unit-input"><input type="number" inputMode="decimal" min="80" max="250" step="0.1" placeholder="175" value={measurements.fields.centimeters} onChange={event => setMeasurements(current => editMeasurement(current, 'centimeters', event.target.value))} required={!preview} /><span>cm</span></div></label>
                <label>Weight<div className="unit-input"><input type="number" inputMode="decimal" min="25" max="300" step="0.1" placeholder="70" value={measurements.fields.kilograms} onChange={event => setMeasurements(current => editMeasurement(current, 'kilograms', event.target.value))} required={!preview} /><span>kg</span></div></label>
              </div>}
              <div className="info-note"><Icon name="user" size={19} /><p>Body style, height and weight select the closest of five body templates. Your try-on is an approximate appearance preview, not an exact body measurement or clothing-fit prediction.</p></div><button type="submit" className="button button-ink full-width" disabled={!preview && (!measurementValid || busy)}>{busy ? 'Saving details…' : 'Next: your selfie'} <Icon name="arrow" /></button>
            </form><p className="form-footnote">Switch units at any time. Your measurements stay the same.</p>
          </>}
          {screen === 'selfie' && <>
            <div className="step-kicker"><button disabled={uploading} onClick={() => navigate('measurements')} aria-label="Back to measurements"><Icon name="back" size={16} /></button> STEP 02 <span>/ 03</span></div><h2>One selfie.<br />That’s it.</h2><p className="section-description">Face the camera in good light, with your whole head visible. We’ll aim for a neutral expression while keeping your current hair, facial hair, and head covering.</p>
            <fieldset className="selfie-fields" disabled={uploading}><SelfieCapture value={preview ? null : selfie} onChange={next => { if (!uploading) { setSelfie(next); setConsent(false); } }} preview={preview} />
            <label className="consent-check"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} /><span>This selfie is of me. I agree to use it as the face in my try-ons.</span></label></fieldset>
            {uploading && <ProgressBar value={uploadProgress} label="Saving your selfie" />}
            <button className="button button-ink full-width" onClick={finishSetup} disabled={!preview && (!selfie || !consent || !setup.measurementsSaved || uploading)}>{preview ? 'Preview profile preparation' : uploading ? 'Saving your selfie…' : 'Open my fitting room'}<Icon name="arrow" /></button><p className="form-footnote">You can retake your selfie any time from your profile.</p>
          </>}
        </section>
      </div>}

      {screen === 'preparing' && <div className="preparation-layout fade-in"><div className="preparation-art"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="profile-sculpture">{submittedSelfieUrl && !preview ? <img src={submittedSelfieUrl} alt="Your selfie" className="submitted-selfie" /> : <Icon name="user" size={68} />}</div><span className="floating-chip chip-one"><Icon name="check" size={14} /> YOUR PERSPECTIVE</span><span className="floating-chip chip-two"><Icon name="spark" size={14} /> PERSONALLY YOURS</span></div><section className="preparation-copy">
        <p className="eyebrow">STEP 03 / YOUR FITTING ROOM</p>
        <h1>{enrollmentFailed && !preview ? <>A little pause.<br />Let’s try <span className="serif-emphasis">again.</span></> : !preview && (!enrollJob || enrollJob.status === 'queued') ? <>You’re in line.<br />We’ll keep you <span className="serif-emphasis">posted.</span></> : <>Your look,<br /><span className="serif-emphasis">step by step.</span></>}</h1>
        <p className="hero-description">{preview ? 'A preview of the setup screen. No selfie has been uploaded.' : enrollmentFailed ? 'We stopped this attempt before completing your new look. See the reason below.' : 'Your selfie guides your face, hair and current features. We’ll show each stage as your personal look takes shape.'}</p>
        <EnrollmentProgress key={preview ? 'preview' : enrollJob?.id || 'waiting'} job={preview ? undefined : enrollJob} awaitingIdentity={setup.awaitingIdentity} preview={preview} />
        {enrollmentFailed && !preview && <><button className="button button-ink" onClick={retakeSelfie}>Take another selfie <Icon name="camera" size={17} /></button>{setup.canKeepPreviousLook && <><p>Your replacement selfie was not used. Your previous look is still saved.</p><button className="text-button" onClick={keepPreviousLook}>Keep my previous look <Icon name="arrow" size={17} /></button></>}</>}
        {preview && <button className="button button-ink" onClick={() => setPreviewScreen('home')}>Preview the fitting room <Icon name="arrow" /></button>}
        {(preview || enrollmentFailed) && <p className="microcopy">{preview ? 'Design preview only. Progress is not simulated.' : 'Your original selfie and account are still saved.'}</p>}
      </section></div>}

      {screen === 'home' && <div className="home-layout fade-in"><section className="home-primary"><div className="hello-line"><span className="status-dot" />{preview ? 'YOUR FITTING ROOM, PREVIEWED' : `YOUR FITTING ROOM IS READY`}</div><h1>{preview ? 'A new way to' : `Hey ${firstName}.`}<br />{preview ? <span className="serif-emphasis">see yourself.</span> : <>Make it <span className="serif-emphasis">yours.</span></>}</h1><p className="hero-description">Your next favorite piece is out there.<br />Let’s see it on you.</p><button className="scan-cta" disabled={reviewingLook} onClick={() => navigate('scanner')}><span className="scan-cta-icon"><Icon name="scan" size={28} /></span><span><strong>{generations.length && !preview ? 'Choose your next piece' : 'Choose your first piece'}</strong><small>{reviewingLook ? 'Confirm your look below to continue.' : 'Browse the collection or scan a THREAD QR.'}</small></span><Icon name="arrow" size={23} /></button>{pendingGarment && !preview && <div className="pending-garment"><Icon name="scan" /><div><strong>{garmentLoading ? 'Finding your piece…' : garment?.name || 'Scanned piece'}</strong><p>{reviewingLook ? 'Your scanned piece is saved. Confirm your look below to start.' : requesting ? 'Starting your try-on automatically…' : error ? 'We couldn’t start this try-on yet.' : 'Ready to enter your fitting room.'}</p>{error && <button className="text-button" onClick={() => retryGarment(pendingGarment)}>Try again <Icon name="retry" size={14} /></button>}</div></div>}{showGenerationProgress && <div className="job-card"><div className="job-card-heading"><span className="spinner" /><div><strong>{activeJob?.status === 'queued' ? 'Your piece is in line' : 'Your look is taking shape'}</strong><p>{activeJob?.message || 'We’re creating your personalized try-on. This takes a few seconds.'}</p></div></div><ProgressBar value={activeJob?.progress} label={activeJob?.stage?.replaceAll('_', ' ') || (requesting ? 'Starting your try-on' : 'Preparing your look')} /><button className="text-button" onClick={() => navigate('gallery')}>View your looks <Icon name="arrow" size={16} /></button></div>}{!preview && profile?.identity?.previewPath && <div className={`your-look ${reviewingLook ? 'is-review' : ''}`}><ProtectedImage path={profile.identity.previewPath} alt="Your look in the fitting room" className="your-look-image" /><div><strong>{reviewingLook ? 'Does this look like you?' : 'Your saved fitting-room look.'}</strong><p>{reviewingLook ? 'Check your face, hair, and skin tone. Every piece you scan will use this appearance.' : 'Every piece you scan goes on this look.'}</p>{reviewingLook && <button className="button button-ink" onClick={approvePersonalLook}>Yes, use this look <Icon name="check" size={17} /></button>}<button className="text-button" onClick={retakeSelfie}>Not quite you? Retake your selfie <Icon name="camera" size={15} /></button></div></div>}</section><aside className="home-look"><div className="section-heading compact"><div><p className="eyebrow">{preview || !generations.length ? 'A LITTLE INSPIRATION' : 'YOUR LATEST LOOK'}</p><h2>{preview || !generations.length ? 'The possibilities look good.' : 'Made for your mood.'}</h2></div></div>{preview ? lookCard(demoGeneration, 0) : generations.length ? lookCard(generations[0], 0) : <div className="inspiration-card"><img src={demoImage} alt="Example generated try-on with a photographic T-shirt" /><span className="example-label">GENERATED EXAMPLE</span><div><p>Your first look<br />starts with a scan.</p><span>This example is not your profile.</span></div></div>}</aside></div>}

      {screen === 'scanner' && <section className="piece-picker fade-in">
        <header className="piece-picker-heading"><div><p className="eyebrow">YOUR NEXT FIND</p><h1>Pick your next piece.<br /><span className="serif-emphasis">See it on you.</span></h1><p>Browse the collection or scan a garment’s tag. Either way, your next look starts here.</p></div><a className="button button-outline" href="#garment-scanner"><Icon name="scan" size={18} /> Have a QR code? Scan it</a></header>
        <div className="piece-picker-layout"><GarmentCatalog key={preview ? 'preview' : user?.uid} onSelect={scan} preview={preview} disabled={!preview && Boolean(showGenerationProgress)} /><div id="garment-scanner" className="piece-picker-camera"><Scanner ref={scannerRef} onDetected={scan} onClose={() => navigate('home')} /></div></div>
      </section>}

      {screen === 'gallery' && <section className="gallery-page fade-in"><div className="gallery-heading"><div><p className="eyebrow">YOUR PERSONAL EDIT</p><h1>Your looks<span className="count-sup">{generationList.length.toString().padStart(2, '0')}</span></h1><p>Every piece. Every possibility. All in one place.</p></div><button className="button button-ink" onClick={() => navigate('scanner')}><Icon name="scan" size={18} /> Choose a piece</button></div>{generationList.length ? <div className="gallery-grid">{generationList.map(lookCard)}</div> : <div className="empty-gallery"><div className="empty-look-frame"><Icon name="grid" size={34} /></div><h2>A little empty.<br />A lot of possibility.</h2><p>Choose your first piece to start building your personal collection of looks.</p><button className="button button-ink" onClick={() => navigate('scanner')}>Find your first look <Icon name="arrow" /></button></div>}</section>}

      {screen === 'detail' && <section className="detail-page fade-in"><button className="text-button back-link" onClick={() => navigate('gallery')}><Icon name="back" size={17} /> Back to your looks</button>{chosenGeneration ? <div className="detail-layout"><div className="detail-image"><ProtectedImage path={chosenGeneration.imagePath} alt={`${chosenGeneration.garmentName} personalized try-on`} preview={preview} />{preview && <span className="example-label">GENERATED DEMO · NOT YOUR PROFILE</span>}</div><div className="detail-copy"><p className="eyebrow">{preview ? 'THE DEMO EDIT' : 'YOUR PERSONAL TRY-ON'}</p><h1>{chosenGeneration.garmentName}</h1><div className="detail-divider" /><p className="detail-description">{chosenGeneration.status === 'failed' ? chosenGeneration.error || 'This look could not finish. Try generating it again.' : chosenGeneration.status === 'completed' ? 'A new perspective on a piece you love. Take a closer look, save it, and keep exploring.' : 'Your personalized look is on its way. We’ll keep the progress in your fitting room.'}</p><dl className="detail-facts"><div><dt>THE EXPERIENCE</dt><dd>{preview ? 'Curated example' : 'Personalized virtual try-on'}</dd></div><div><dt>STATUS</dt><dd><span className={`status-pill ${chosenGeneration.status === 'failed' ? 'failed' : ''}`}>{preview ? 'Preview' : chosenGeneration.status.replaceAll('_', ' ')}</span></dd></div>{dateText(chosenGeneration.createdAt) && <div><dt>CREATED</dt><dd>{dateText(chosenGeneration.createdAt)}</dd></div>}</dl>{chosenGeneration.status === 'completed' && <button className="button button-ink full-width" onClick={downloadLook} disabled={downloading}>{downloading ? <span className="spinner" /> : <Icon name="download" size={18} />}{preview ? 'Download demo image' : chosenGeneration.image2kPath ? 'Save 2K image' : chosenGeneration.image4kPath ? 'Save 4K image' : 'Save your look'}</button>}{(chosenGeneration.status === 'failed' || chosenGeneration.status === 'completed') && <button className="button button-outline full-width" onClick={() => retryGarment(chosenGeneration.garmentId)}><Icon name="retry" size={17} />{chosenGeneration.status === 'failed' ? 'Try again' : 'Generate another look'}</button>}<p className="detail-disclaimer">An AI visual preview, not a physical fit prediction. Identity, garment prints, and fine details may vary.</p><button className="text-button" onClick={() => navigate('scanner')}>On to the next piece <Icon name="arrow" size={17} /></button></div></div> : <div className="empty-gallery"><h2>Choose a look first.</h2><button className="button button-ink" onClick={() => navigate('gallery')}>Your looks <Icon name="arrow" /></button></div>}</section>}

      {screen === 'account' && <section className="account-page fade-in"><p className="eyebrow">THE PERSON BEHIND THE LOOKS</p><h1>Your profile.</h1><div className="account-card"><div className="account-identity"><span className="avatar large-avatar"><Icon name="user" size={26} /></span><div><h2>{preview ? 'Design preview' : user?.displayName || 'Your account'}</h2><p>{preview ? 'No signed-in account or saved selfie' : user?.email}</p></div><span className="status-pill">{preview ? 'Preview' : ready ? 'Ready to try on' : 'Setting up'}</span></div><dl className="account-measurements"><div><dt>Height</dt><dd>{preview ? '—' : formatHeight(profile?.heightCm, profile?.measurementSystem || 'us')}</dd></div><div><dt>Weight</dt><dd>{preview ? '—' : formatWeight(profile?.weightKg, profile?.measurementSystem || 'us')}</dd></div><div><dt>Your looks</dt><dd>{preview ? '—' : generations.length}</dd></div></dl><p className="account-note">{accountBodyNote}</p><button className="text-button" onClick={updateDetailsAndLook}>Update details and rebuild look <Icon name="arrow" size={16} /></button><button className="text-button" onClick={() => { if (preview) setPreviewScreen('selfie'); else retakeSelfie(); }}>Retake your selfie <Icon name="arrow" size={16} /></button></div><button className="text-button logout-button" onClick={logout} disabled={busy}><Icon name="logout" size={17} />{preview ? 'Leave design preview' : 'Sign out'}</button></section>}
    </main>
    {isMain && <nav className="mobile-nav" aria-label="Main navigation"><button className={screen === 'home' ? 'active' : ''} aria-current={screen === 'home' ? 'page' : undefined} onClick={() => navigate('home')}><Icon name="spark" size={21} /><span>For you</span></button><button className={screen === 'scanner' ? 'active' : ''} aria-current={screen === 'scanner' ? 'page' : undefined} onClick={openScanTab}><Icon name="scan" size={23} /><span>Scan</span></button><button className={screen === 'gallery' || screen === 'detail' ? 'active' : ''} aria-current={screen === 'gallery' || screen === 'detail' ? 'page' : undefined} onClick={() => navigate('gallery')}><Icon name="grid" size={20} /><span>Your looks</span></button></nav>}
    <footer className="site-footer"><span>THREAD<span className="brand-asterisk">✳</span></span></footer>
  </div>;
}

export default App;
