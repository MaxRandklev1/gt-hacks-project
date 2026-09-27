import { useState } from 'react';
import { BrandMark } from './Icon';

const customVideoSource = import.meta.env.VITE_HOMEPAGE_VIDEO_URL?.trim();
const videoSource = customVideoSource || '/media/thread-reel.mp4';

export function HomepageVideo() {
  const [unavailable, setUnavailable] = useState(false);
  return <aside className="homepage-video" aria-label="THREAD in action">
    {!unavailable
      ? <video src={videoSource} poster={customVideoSource ? undefined : '/media/thread-reel-poster.jpg'} autoPlay muted loop playsInline controls preload="metadata" aria-label="How THREAD works" onError={() => setUnavailable(true)} />
      : <div className="homepage-video-placeholder">
        <span className="video-placeholder-mark" aria-hidden="true"><BrandMark /></span>
        <p className="eyebrow">THREAD IN ACTION</p>
        <h2>Your fitting room,<br />in motion.</h2>
        <p className="video-placeholder-caption">The video is temporarily unavailable.</p>
      </div>}
  </aside>;
}
