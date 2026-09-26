import { useEffect, useRef, useState } from 'react';
import { Icon } from './Icon';

export type CapturedSelfie = { file: File; capturedAt: number };
export function SelfieCapture({ value, onChange, preview = false }: { value: CapturedSelfie | null; onChange(value: CapturedSelfie | null): void; preview?: boolean }) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const generation = useRef(0);
  const capturing = useRef(false);
  const onChangeRef = useRef(onChange);
  const [phase, setPhase] = useState<'idle' | 'starting' | 'live' | 'capturing'>('idle');
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
    if (preview) return;
    stop(); const run = generation.current;
    setError(''); setPhase('starting');
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error('A live selfie needs camera access on HTTPS. Open this secure page in Safari or Chrome on your phone.');
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
      setError(name === 'NotAllowedError' ? 'Allow camera access in your browser’s site settings, then try again. If you opened this inside another app, open the link in Safari or Chrome.' : name === 'NotFoundError' ? 'No camera was found. Open this page on a phone or a device with a camera to take your live selfie.' : name === 'NotReadableError' ? 'Your camera is busy. Close other camera apps and try again.' : cause instanceof Error ? cause.message : 'The camera could not start. Open this page in Safari or Chrome and allow camera access.');
    }
  }
  async function capture() {
    if (preview || phase !== 'live' || capturing.current) return;
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
      onChangeRef.current({ file: new File([blob], `live-selfie-${capturedAt}.jpg`, { type: 'image/jpeg', lastModified: capturedAt }), capturedAt });
      setPhase('idle');
    } catch (cause) { if (generation.current === run) { stop(); setPhase('idle'); setError(cause instanceof Error ? cause.message : 'Please retake your selfie.'); } }
  }
  function retake() { stop(); setPhase('idle'); setError(''); onChangeRef.current(null); }

  return <section className="selfie-capture" aria-label="Required live selfie">
    <div className={`selfie-view ${phase === 'live' ? 'is-live' : ''} ${value ? 'has-selfie' : ''}`}>
      <video ref={video} playsInline muted aria-label="Live front camera preview" aria-hidden={phase !== 'live'} />
      {value && url ? <img src={url} alt="Your captured live selfie" /> : <>
        <div className="selfie-guide" aria-hidden="true" />
        {phase !== 'live' && <div className="selfie-placeholder"><Icon name="camera" size={34} /><strong>{preview ? 'Your live selfie goes here' : 'A fresh photo. The real you.'}</strong><span>{preview ? 'Camera is disabled in design preview.' : 'Use your front camera in good light.'}</span></div>}
      </>}
      <span className="selfie-badge"><span className="status-dot" />{value ? 'SELFIE CAPTURED' : phase === 'live' ? 'LIVE CAMERA' : 'LIVE SELFIE REQUIRED'}</span>
    </div>
    {error && <p className="error-message" role="alert">{error}</p>}
    <div className="selfie-actions">{value ? <><span className="selfie-success"><Icon name="check" size={17} /> Your try-on reference is ready</span><button type="button" className="text-button" onClick={retake}><Icon name="retry" size={17} /> Retake selfie</button></> : phase === 'live' ? <><button type="button" className="button button-ink" onClick={capture}><Icon name="camera" size={19} /> Take selfie</button><button type="button" className="text-button" onClick={() => { stop(); setPhase('idle'); }}>Stop camera</button></> : <button type="button" className="button button-ink" disabled={preview || phase === 'starting' || phase === 'capturing'} onClick={start}>{phase === 'starting' || phase === 'capturing' ? <span className="spinner" /> : <Icon name="camera" size={19} />}{phase === 'starting' ? 'Starting camera…' : phase === 'capturing' ? 'Saving selfie…' : 'Enable front camera'}</button>}</div>
    <p className="selfie-footnote">Keep your face clear and look straight ahead. The live preview is mirrored; your saved selfie is not. We use this selfie as the face reference for your try-ons, separate from your eight training photos.</p>
  </section>;
}
