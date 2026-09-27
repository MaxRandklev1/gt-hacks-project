import { useState } from 'react';

const customVideoSource = import.meta.env.VITE_HOMEPAGE_VIDEO_URL?.trim();
// Version the URL so returning phones fetch the mobile-compatible encode.
const videoSource = customVideoSource || '/media/thread-reel.mp4?v=720p30-20260927';

export function HomepageVideo() {
  const [unavailable, setUnavailable] = useState(false);
  const [attempt, setAttempt] = useState(0);
  return <aside className="homepage-video" aria-label="THREAD in action">
    {!unavailable
      ? <video key={attempt} src={videoSource} poster={customVideoSource ? undefined : '/media/thread-reel-poster.jpg'} autoPlay muted loop playsInline controls preload="metadata" aria-label="How THREAD works" onError={() => setUnavailable(true)} />
      : <div className="homepage-video-placeholder">
        <h2>Watch Thread in action.</h2>
        <p className="video-placeholder-caption">The video is temporarily unavailable.</p>
        <button className="button button-outline" type="button" onClick={() => { setAttempt((value) => value + 1); setUnavailable(false); }}>Retry video</button>
      </div>}
  </aside>;
}
