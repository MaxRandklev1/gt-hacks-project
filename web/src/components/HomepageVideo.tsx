import { useState } from 'react';

// Supply a hosted video URL when the product walkthrough is ready.
const videoSource = import.meta.env.VITE_HOMEPAGE_VIDEO_URL?.trim();

export function HomepageVideo() {
  const [unavailable, setUnavailable] = useState(false);
  return <aside className="homepage-video" aria-label="THREAD in action">
    {videoSource && !unavailable
      ? <video src={videoSource} autoPlay muted loop playsInline controls preload="metadata" aria-label="How THREAD works" onError={() => setUnavailable(true)} />
      : <div className="homepage-video-placeholder">
        <span className="video-placeholder-mark" aria-hidden="true">✳</span>
        <p className="eyebrow">THREAD IN ACTION</p>
        <h2>Your fitting room,<br />in motion.</h2>
        <p className="video-placeholder-caption">Video coming soon</p>
      </div>}
  </aside>;
}
