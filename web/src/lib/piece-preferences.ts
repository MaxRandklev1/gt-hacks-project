import type { Generation } from './client';

export type Reaction = 'like' | 'dislike' | 'none';
export type PiecePreference = { garmentId: string; reaction: Reaction; wishlist: boolean; generationId: string };
export type PieceAction = 'like' | 'dislike' | 'wishlist';
export type LooksFilter = 'all' | 'liked' | 'disliked' | 'wishlist';

export const looksFilters: { id: LooksFilter; label: string; empty: string }[] = [
  { id: 'all', label: 'Your looks', empty: 'Choose your first piece to start building your personal collection of looks.' },
  { id: 'liked', label: 'Liked pieces', empty: 'Like a piece after trying it on and you’ll find it here.' },
  { id: 'disliked', label: 'Disliked pieces', empty: 'Pieces you dislike will appear here. You can change your mind anytime.' },
  { id: 'wishlist', label: 'Wish list', empty: 'Save a piece to your wish list after trying it on.' },
];

export function applyPieceAction(current: Pick<PiecePreference, 'reaction' | 'wishlist'> | undefined, action: PieceAction) {
  const reaction = current?.reaction || 'none';
  const wishlist = current?.wishlist || false;
  return action === 'wishlist'
    ? { reaction, wishlist: !wishlist }
    : { reaction: reaction === action ? 'none' as const : action, wishlist };
}

/** One entry per saved piece; its referenced look can be older than the recent-history window. */
export function filterLooks(generations: Generation[], savedGenerations: Generation[], preferences: PiecePreference[], filter: LooksFilter): Generation[] {
  if (filter === 'all') return generations;
  const byId = new Map([...savedGenerations, ...generations].map(item => [item.id, item]));
  return preferences.filter(item => filter === 'wishlist' ? item.wishlist : item.reaction === (filter === 'liked' ? 'like' : 'dislike'))
    .flatMap(preference => {
      const look = byId.get(preference.generationId);
      return look?.status === 'completed' && look.garmentId === preference.garmentId ? [look] : [];
    });
}
