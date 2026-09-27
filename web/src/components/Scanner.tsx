import { useEffect, useImperativeHandle, useRef, useState, type Ref } from 'react';
import { Icon } from './Icon';

type Detector = { detect(source: HTMLVideoElement | ImageBitmap): Promise<{ rawValue: string }[]> };
type DetectorConstructor = new (options: { formats: string[] }) => Detector;
type ScannerControls = { stop(): void };

export type ScannerHandle = { start(): void };

export function Scanner({ onDetected, onClose, ref }: { onDetected(value: string): void; onClose(): void; ref?: Ref<ScannerHandle> }) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const controls = useRef<ScannerControls | null>(null);
  const frame = useRef<number | null>(null);
  const generation = useRef(0);
  const reading = useRef(false);
  const cameraRunning = useRef(false);
  const onDetectedRef = useRef(onDetected);
  const [active, setActive] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const [code, setCode] = useState('');
  const file = useRef<HTMLInputElement>(null);
  onDetectedRef.current = onDetected;

  function stop() {
    generation.current += 1;
    cameraRunning.current = false;
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
    controls.current?.stop();
    controls.current = null;
    stream.current?.getTracks().forEach(track => track.stop());
    stream.current = null;
    if (video.current?.srcObject instanceof MediaStream) video.current.srcObject.getTracks().forEach(track => track.stop());
    if (video.current) video.current.srcObject = null;
  }

  useEffect(() => {
    const hidden = () => { if (document.hidden) { stop(); setActive(false); setStarting(false); } };
    document.addEventListener('visibilitychange', hidden);
    return () => { stop(); document.removeEventListener('visibilitychange', hidden); };
  }, []);

  function accept(value: string) {
    if (!value.trim()) return;
    stop();
    setActive(false);
    setStarting(false);
    onDetectedRef.current(value.trim());
  }

  async function start() {
    video.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (cameraRunning.current) return;
    stop();
    cameraRunning.current = true;
    const run = generation.current;
    setError('');
    setStarting(true);
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('Camera access needs HTTPS and a supported browser. You can also upload a QR image below.');
      const DetectorClass = (window as unknown as { BarcodeDetector?: DetectorConstructor }).BarcodeDetector;
      if (DetectorClass) {
        let detector: Detector | null = null;
        try { detector = new DetectorClass({ formats: ['qr_code'] }); } catch { /* Use the cross-browser reader below. */ }
        if (detector) {
          const acquired = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
          if (generation.current !== run || !video.current) { acquired.getTracks().forEach(track => track.stop()); return; }
          stream.current = acquired;
          video.current.srcObject = acquired;
          await video.current.play();
          if (generation.current !== run) return;
          setActive(true);
          setStarting(false);
          let lastScan = 0;
          const scan = async (now: number) => {
            if (generation.current !== run) return;
            if (video.current && video.current.readyState >= 2 && !reading.current && now - lastScan > 180) {
              lastScan = now;
              reading.current = true;
              try {
                const results = await detector!.detect(video.current);
                if (generation.current === run && results[0]?.rawValue) { accept(results[0].rawValue); return; }
              } catch { /* A moving or partial code is expected while scanning. */ }
              finally { reading.current = false; }
            }
            if (generation.current === run) frame.current = requestAnimationFrame(scan);
          };
          frame.current = requestAnimationFrame(scan);
          return;
        }
      }
      const { BrowserQRCodeReader } = await import('@zxing/browser');
      if (generation.current !== run || !video.current) return;
      const reader = new BrowserQRCodeReader();
      const scanner = await reader.decodeFromConstraints({ video: { facingMode: { ideal: 'environment' } }, audio: false }, video.current, result => {
        if (generation.current === run && result) accept(result.getText());
      });
      if (generation.current !== run) { scanner.stop(); return; }
      controls.current = scanner;
      setActive(true);
      setStarting(false);
    } catch (cause) {
      if (generation.current !== run) return;
      stop();
      setActive(false);
      setStarting(false);
      setError(cause instanceof DOMException && cause.name === 'NotAllowedError' ? 'Camera permission was not granted. Allow access in your browser, or upload a QR image below.' : cause instanceof Error ? cause.message : 'The camera could not start. Try uploading a QR image.');
    }
  }

  useImperativeHandle(ref, () => ({ start }));

  async function scanFile(selected?: File) {
    if (!selected) return;
    stop(); setActive(false); setStarting(false); setError('');
    const run = generation.current;
    const url = URL.createObjectURL(selected);
    try {
      const DetectorClass = (window as unknown as { BarcodeDetector?: DetectorConstructor }).BarcodeDetector;
      let value = '';
      if (DetectorClass && typeof createImageBitmap === 'function') {
        try {
          const bitmap = await createImageBitmap(selected);
          try { value = (await new DetectorClass({ formats: ['qr_code'] }).detect(bitmap))[0]?.rawValue || ''; }
          finally { bitmap.close(); }
        } catch { /* Fall back to ZXing for formats the native reader cannot decode. */ }
      }
      if (!value) {
        const { BrowserQRCodeReader } = await import('@zxing/browser');
        value = (await new BrowserQRCodeReader().decodeFromImageUrl(url)).getText();
      }
      if (generation.current === run) accept(value);
    } catch {
      if (generation.current === run) setError('No QR code was found in that image. Try a clearer photo.');
    } finally { URL.revokeObjectURL(url); if (file.current) file.current.value = ''; }
  }

  return <section className="scanner-panel fade-in" aria-labelledby="scanner-title">
    <div className="section-heading"><div><p className="eyebrow">YOUR NEXT FIND</p><h2 id="scanner-title">Meet it. Scan it.<br />See it on you.</h2></div><button className="icon-button" onClick={onClose} aria-label="Close scanner"><Icon name="close" /></button></div>
    <div className={`camera-window ${active ? 'is-active' : ''}`}>
      <video ref={video} playsInline muted aria-label="Live QR scanner" />
      <div className="scan-corners" aria-hidden="true"><i /><i /><i /><i /></div>
      {!active && <div className="camera-idle"><span className="camera-icon"><Icon name="scan" size={34} /></span><p>Point your camera at<br />a THREAD garment QR.</p><button className="button button-lime" disabled={starting} onClick={start}>{starting ? <span className="spinner" /> : <Icon name="camera" />}{starting ? 'Starting camera…' : 'Enable camera'}</button></div>}
      {active && <div className="camera-live"><span className="status-dot" /> Looking for a garment QR<button onClick={() => { stop(); setActive(false); }}>Stop camera</button></div>}
    </div>
    {error && <p className="error-message" role="alert">{error}</p>}
    <div className="scanner-alternatives"><button className="text-button" onClick={() => file.current?.click()}><Icon name="upload" size={17} /> Upload a QR image</button><span>No camera? No problem.</span><input ref={file} type="file" accept="image/*" hidden onChange={event => scanFile(event.target.files?.[0])} /></div>
    <form className="code-form" onSubmit={event => { event.preventDefault(); accept(code); }}><label htmlFor="garment-code">Or enter the garment code</label><div><input id="garment-code" value={code} onChange={event => setCode(event.target.value)} placeholder="e.g. THREAD-001" autoComplete="off" autoCapitalize="none" /><button type="submit" className="button button-ink" disabled={!code.trim()} aria-label="Find garment"><Icon name="arrow" /></button></div></form>
    <p className="microcopy">Your camera is used to read the code. Camera footage is not uploaded.</p>
  </section>;
}
