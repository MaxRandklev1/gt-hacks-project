import { useEffect, useState } from 'react';
import { getPrivateImage, listActiveGarments, type Garment } from '../lib/client';
import printedTags from '../../../docs/tags/printed-threads.json';
import { Icon } from './Icon';
import './GarmentCatalog.css';

const previewGarments: Garment[] = printedTags.garments.map(item => ({
  id: item.id, name: `${item.label} — ${item.description}`, active: true,
  imagePath: '', baseImagePath: '',
}));

function GarmentImage({ path, name, preview }: { path: string; name: string; preview: boolean }) {
  const [url, setUrl] = useState<string>();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setUrl(undefined); setFailed(false);
    if (preview || !path) return;
    let cancelled = false;
    let ownedUrl: string | undefined;
    getPrivateImage(path).then(result => {
      ownedUrl = result;
      if (cancelled) URL.revokeObjectURL(result); else setUrl(result);
    }).catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; if (ownedUrl) URL.revokeObjectURL(ownedUrl); };
  }, [path, preview]);
  return <div className="garment-card-image">
    {url && !failed ? <img src={url} alt={name} loading="lazy" onError={() => setFailed(true)} />
      : <div className="garment-image-placeholder"><Icon name="grid" size={28} /><span>{preview ? 'Collection preview' : failed || !path ? 'Photo unavailable' : 'Loading photo…'}</span></div>}
  </div>;
}

export function GarmentCatalog({ onSelect, preview = false, disabled = false }: {
  onSelect(id: string): void; preview?: boolean; disabled?: boolean;
}) {
  const [garments, setGarments] = useState<Garment[]>([]);
  const [loading, setLoading] = useState(!preview);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (preview) return;
    let current = true;
    setLoading(true); setError('');
    listActiveGarments().then(items => { if (current) setGarments(items); })
      .catch(() => { if (current) setError('We couldn’t load the collection. Check your connection and try again.'); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [preview, retry]);
  const items = preview ? previewGarments : garments;

  return <section id="garment-collection" className="garment-collection" aria-labelledby="collection-title" aria-busy={loading}>
    <div className="collection-heading"><div><p className="eyebrow">NO QR CODE NEEDED</p><h2 id="collection-title">Choose from the collection.</h2><p>Select a piece to start your personalized try-on.</p></div>{!loading && !error && <span className="collection-count">{items.length} pieces</span>}</div>
    {preview && <p className="collection-note">Demo preview. Sign in and create your look to see garment photos and try these pieces on yourself.</p>}
    {disabled && <p className="collection-note" role="status">Your current try-on is still processing. You can choose another piece when it finishes.</p>}
    {loading ? <div className="collection-state" role="status"><span className="spinner" /> Loading the collection…</div>
      : error ? <div className="collection-state"><p role="alert">{error}</p><button className="button button-outline" onClick={() => setRetry(value => value + 1)}>Try again <Icon name="retry" size={16} /></button></div>
      : !items.length ? <div className="collection-state"><Icon name="grid" /><p>No pieces are available right now. Check back shortly.</p><button className="text-button" onClick={() => setRetry(value => value + 1)}>Refresh collection <Icon name="retry" size={16} /></button></div>
      : <div className="garment-grid">{items.map(item => <button key={item.id} className="garment-card" disabled={disabled} onClick={() => onSelect(item.id)} aria-label={`Try on ${item.name}`}>
        <GarmentImage path={item.thumbnailPath || item.imagePath} name={item.name} preview={preview} />
        <span className="garment-card-copy"><strong>{item.name}</strong><span>{preview ? 'Preview experience' : 'Try it on'}<Icon name="arrow" size={17} /></span></span>
      </button>)}</div>}
  </section>;
}
