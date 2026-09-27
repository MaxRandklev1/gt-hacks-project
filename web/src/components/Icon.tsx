import type { CSSProperties } from 'react';

export type IconName = 'arrow' | 'back' | 'scan' | 'grid' | 'user' | 'plus' | 'close' | 'check' | 'camera' | 'upload' | 'spark' | 'clock' | 'retry' | 'logout' | 'download' | 'chevron' | 'shield' | 'like' | 'dislike' | 'bookmark' | 'down';

const paths: Record<IconName, string[]> = {
  like: ['M7 10v11H3V10z', 'M7 10l5-8c2 0 3 2 2 5l-1 3h6a2 2 0 0 1 2 2l-2 7a3 3 0 0 1-3 2H7'],
  dislike: ['M7 14V3H3v11z', 'M7 14l5 8c2 0 3-2 2-5l-1-3h6a2 2 0 0 0 2-2l-2-7a3 3 0 0 0-3-2H7'],
  bookmark: ['M6 3h12v18l-6-4-6 4z'],
  down: ['m5 9 7 7 7-7'],
  arrow: ['M4 12h16', 'm13 5 7 7-7 7'],
  back: ['M20 12H4', 'm11 5-7 7 7 7'],
  scan: ['M8 3H3v5', 'M16 3h5v5', 'M21 16v5h-5', 'M8 21H3v-5', 'M7 12h10'],
  grid: ['M3 3h7v7H3z', 'M14 3h7v7h-7z', 'M3 14h7v7H3z', 'M14 14h7v7h-7z'],
  user: ['M20 21v-2a7 7 0 0 0-14 0v2', 'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0'],
  plus: ['M12 5v14', 'M5 12h14'],
  close: ['m6 6 12 12', 'M18 6 6 18'],
  check: ['m5 12 4 4L19 6'],
  camera: ['M8 5 6 8H3v12h18V8h-3l-2-3z', 'M16 13a4 4 0 1 1-8 0 4 4 0 0 1 8 0'],
  upload: ['M12 16V3', 'm7 8 5-5 5 5', 'M4 15v6h16v-6'],
  spark: ['m12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6z'],
  clock: ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0', 'M12 7v5l3 2'],
  retry: ['M3 10a9 9 0 1 1 2 8', 'M3 3v7h7'],
  logout: ['M10 4H4v16h6', 'M10 12h11', 'm17 8 4 4-4 4'],
  download: ['M12 3v13', 'm7 11 5 5 5-5', 'M4 17v4h16v-4'],
  chevron: ['m9 5 7 7-7 7'],
  shield: ['m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z', 'm8 12 3 3 5-6'],
};

export function Icon({ name, size = 20, className, style }: { name: IconName; size?: number; className?: string; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className} style={style}>{paths[name].map((d, i) => <path d={d} key={i} />)}</svg>;
}

/** Fixed geometry keeps the brand independent of system fonts and emoji rendering. */
export function BrandMark() {
  return <svg className="brand-asterisk" width="25" height="25" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" focusable="false"><path d="M12 2v20M2 12h20M4.93 4.93l14.14 14.14M4.93 19.07 19.07 4.93" /></svg>;
}

export function GoogleMark() {
  return <svg width="19" height="19" viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285F4" d="M21.8 12.2c0-.7-.1-1.5-.2-2.2H12v4.2h5.5a4.7 4.7 0 0 1-2 3.1v2.6h3.3c1.9-1.8 3-4.4 3-7.7Z" /><path fill="#34A853" d="M12 22c2.7 0 5-.9 6.8-2.5l-3.3-2.6c-.9.6-2.1 1-3.5 1-2.6 0-4.8-1.8-5.6-4.1H3v2.7A10.3 10.3 0 0 0 12 22Z" /><path fill="#FBBC05" d="M6.4 13.8a6 6 0 0 1 0-3.6V7.5H3a10 10 0 0 0 0 9l3.4-2.7Z" /><path fill="#EA4335" d="M12 6.1c1.5 0 2.8.5 3.8 1.5l2.9-2.9A9.8 9.8 0 0 0 12 2a10.3 10.3 0 0 0-9 5.5l3.4 2.7C7.2 7.9 9.4 6.1 12 6.1Z" /></svg>;
}
