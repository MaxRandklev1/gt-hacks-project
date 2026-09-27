import { useEffect, useRef, useState } from 'react';
import { Icon } from './Icon';

export type CapturedSelfie = { file: File; source: 'camera' | 'upload'; selectedAt: number; capturedAt?: number };
export function SelfieCapture({ value, onChange }: { value: CapturedSelfie | null; onChange(value: CapturedSelfie | null): void }) {
  const video = useRef<HTMLVideoElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const generation = useRef(0);
  const capturing = useRef(false);
  const onChangeRef = useRef(onChange);
  const [phase, setPhase] = useState<'idle' | 'starting' | 'live' | 'capturing' | 'reading'>('idle');
  const [error, setError] = useState('');
  const [url, setUrl] = useState<string>();
  onChangeRef.current = onChange;

  function stop() {
    generation.current++;
    capturing.current = false;
    stream.current?.getTracks().forEach(track => { track.onended = null; track.stop(); });
    stream.current = null;
    if (video.current) video.current.srcObject = null;
  }
  useEffect(() => {
    const hidden = () => { if (document.hidden) { stop(); setPhase('idle'); } };
    document.addEventListener('visibilitychange', hidden);
    return () => { stop(); document.removeEventListener('visibilitychange', hidden); };
  }, []);
  useEffect(() => {
    if (!value) { setUrl(undefined); return; }
    const next = URL.createObjectURL(value.file); setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [value]);

  async function start() {
    stop(); const run = generation.current;
    setError(''); setPhase('starting');
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error('The camera needs HTTPS and browser camera access. Open this secure page in Safari or Chrome, or choose a recent selfie.');
      const acquired = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'user' }, width: { ideal: 1280 }, height: { ideal: 1280 } }, audio: false });
      if (generation.current !== run || !video.current) { acquired.getTracks().forEach(track => track.stop()); return; }
      stream.current = acquired;
      acquired.getVideoTracks().forEach(track => { track.onended = () => { stop(); setPhase('idle'); setError('The camera stopped. Enable it again to take your selfie.'); }; });
      video.current.srcObject = acquired;
      await video.current.play();
      if (generation.current === run) setPhase('live');
    } catch (cause) {
      if (generation.current !== run) return;
      stop(); setPhase('idle');
      const name = cause instanceof DOMException ? cause.name : '';
      setError(name === 'NotAllowedError' ? 'Allow camera access in your browser’s site settings, or choose a recent selfie. If you opened this inside another app, try Safari or Chrome.' : name === 'NotFoundError' ? 'No camera was found. Choose a recent selfie, or open this page on a device with a camera.' : name === 'NotReadableError' ? 'Your camera is busy. Close other camera apps and try again, or choose a recent selfie.' : cause instanceof Error ? cause.message : 'The camera could not start. Open this page in Safari or Chrome and allow camera access, or choose a recent selfie.');
    }
  }
  async function capture() {
    if (phase !== 'live' || capturing.current) return;
    const source = video.current;
    if (!source || source.readyState < 2 || Math.min(source.videoWidth, source.videoHeight) < 384) { stop(); setPhase('idle'); setError('The camera did not provide a clear image. Enable it again; it needs at least 384 pixels on each side.'); return; }
    capturing.current = true; setPhase('capturing'); setError('');
    const capturedAt = Date.now();
    let run = generation.current;
    try {
      const canvas = document.createElement('canvas');
      const scale = Math.min(1, 2048 / Math.max(source.videoWidth, source.videoHeight));
      canvas.width = Math.round(source.videoWidth * scale); canvas.height = Math.round(source.videoHeight * scale);
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Your browser could not capture the selfie. Try Safari or Chrome.');
      // CSS mirrors only the live preview. Capture the entire original frame without a transform or crop.
      context.drawImage(source, 0, 0, canvas.width, canvas.height);
      stop(); run = generation.current;
      const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(result => result ? resolve(result) : reject(new Error('The selfie could not be saved. Please retake it.')), 'image/jpeg', 0.95));
      if (generation.current !== run) return;
      onChangeRef.current({ file: new File([blob], `live-selfie-${capturedAt}.jpg`, { type: 'image/jpeg', lastModified: capturedAt }), source: 'camera', selectedAt: capturedAt, capturedAt });
      setPhase('idle');
    } catch (cause) { if (generation.current === run) { stop(); setPhase('idle'); setError(cause instanceof Error ? cause.message : 'Please retake your selfie.'); } }
  }
  function choose() {
    stop(); setPhase('idle'); setError('');
    if (fileInput.current) { fileInput.current.value = ''; fileInput.current.click(); }
  }
  async function select(file?: File) {
    if (!file) return;
    stop(); const run = generation.current;
    setPhase('reading'); setError('');
    const selectedAt = Date.now();
    try {
      if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) throw new Error('Choose a JPG, PNG, or WebP selfie. Export HEIC photos as JPG first.');
      if (file.size <= 0 || file.size > 20 * 1024 * 1024) throw new Error('Choose a selfie smaller than 20 MB that is not empty.');
      let bitmap: ImageBitmap;
      try { bitmap = await createImageBitmap(file); } catch { throw new Error('This image could not be opened. Choose another JPG, PNG, or WebP selfie.'); }
      try {
        if (generation.current !== run) return;
        if (Math.min(bitmap.width, bitmap.height) < 384) throw new Error('Choose a clear selfie with at least 384 pixels on each side.');
      } finally { bitmap.close(); }
      // File modification dates do not reliably tell us when a photo was taken.
      onChangeRef.current({ file, source: 'upload', selectedAt });
      setPhase('idle');
    } catch (cause) {
      if (generation.current === run) { setPhase('idle'); setError(cause instanceof Error ? cause.message : 'Please choose another recent selfie.'); }
    }
  }
  function change() { stop(); setPhase('idle'); setError(''); onChangeRef.current(null); }
  const busy = phase === 'starting' || phase === 'capturing' || phase === 'reading';

  return <section className="selfie-capture" aria-label="Required recent selfie">
    <div className={`selfie-view ${phase === 'live' ? 'is-live' : ''} ${value ? 'has-selfie' : ''}`}>
      <video ref={video} playsInline muted aria-label="Live front camera preview" aria-hidden={phase !== 'live'} />
      {value && url ? <img src={url} alt={value.source === 'camera' ? 'Your captured selfie' : 'Your selected recent selfie'} /> : <>
        <div className="selfie-guide" aria-hidden="true" />
        {phase !== 'live' && <div className="selfie-placeholder"><Icon name="camera" size={34} /><strong>Your look, right now.</strong><span>Take a selfie or choose a recent one.</span></div>}
      </>}
      <span className="selfie-badge"><span className="status-dot" />{value ? value.source === 'camera' ? 'SELFIE CAPTURED' : 'RECENT SELFIE SELECTED' : phase === 'live' ? 'LIVE CAMERA' : 'RECENT SELFIE REQUIRED'}</span>
    </div>
    {error && <p className="error-message" role="alert">{error}</p>}
    <input ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp" aria-label="Choose a recent selfie file" hidden onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; void select(file); }} />
    <div className="selfie-actions">{value ? <><span className="selfie-success"><Icon name="check" size={17} /> Your try-on reference is ready</span><button type="button" className="text-button" onClick={change}><Icon name="retry" size={17} /> Change selfie</button></> : <>
      {phase === 'live' ? <button type="button" className="button button-ink" onClick={capture}><Icon name="camera" size={19} /> Take selfie</button> : <button type="button" className="button button-ink" disabled={busy} onClick={start}>{busy ? <span className="spinner" /> : <Icon name="camera" size={19} />}{phase === 'starting' ? 'Starting camera…' : phase === 'capturing' ? 'Saving selfie…' : phase === 'reading' ? 'Checking photo…' : 'Take a selfie now'}</button>}
      <button type="button" className="button button-outline" disabled={busy} onClick={choose}><Icon name="upload" size={17} /> Choose a recent selfie</button>
      {phase === 'live' && <button type="button" className="text-button" onClick={() => { stop(); setPhase('idle'); }}>Stop camera</button>}
    </>}</div>
    <p className="selfie-footnote">Use a clear, unfiltered photo with your current haircut, facial hair, or usual head covering. Keep your face unobstructed and include your whole head and shoulders. Avoid sunglasses and filters. This one selfie is your try-on reference. JPG, PNG, or WebP · 20 MB max.</p>
    {phase === 'live' && <p className="selfie-footnote">The live camera preview is mirrored; your saved selfie is not.</p>}
  </section>;
}
