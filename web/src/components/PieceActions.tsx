import { Icon } from './Icon';
import type { PieceAction, PiecePreference } from '../lib/piece-preferences';

export function PieceActions({ preference, disabled, saving, onAction }: {
  preference?: PiecePreference; disabled?: boolean; saving?: boolean; onAction: (action: PieceAction) => void;
}) {
  return <div className="piece-actions" aria-label="Save and rate this piece" aria-busy={saving || undefined}>
    <div className="piece-reactions">
      <button type="button" className="piece-action" aria-pressed={preference?.reaction === 'like'} disabled={disabled || saving} onClick={() => onAction('like')}><Icon name="like" size={18} /><span>Like</span></button>
      <button type="button" className="piece-action" aria-pressed={preference?.reaction === 'dislike'} disabled={disabled || saving} onClick={() => onAction('dislike')}><Icon name="dislike" size={18} /><span>Dislike</span></button>
    </div>
    <button type="button" className="piece-action piece-wishlist" aria-pressed={preference?.wishlist || false} disabled={disabled || saving} onClick={() => onAction('wishlist')}><Icon name={preference?.wishlist ? 'check' : 'bookmark'} size={18} /><span>{preference?.wishlist ? 'In your wish list' : 'Save to wish list'}</span></button>
  </div>;
}
