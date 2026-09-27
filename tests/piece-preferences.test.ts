import { describe, expect, it } from 'vitest';
import { applyPieceAction, filterLooks, type PiecePreference } from '../web/src/lib/piece-preferences';
import type { Generation } from '../web/src/lib/client';

const look = (id: string, garmentId = 'thread-1', status = 'completed'): Generation => ({ id, garmentId, garmentName: garmentId, status });
const preference = (overrides: Partial<PiecePreference> = {}): PiecePreference => ({ garmentId: 'thread-1', generationId: 'saved', reaction: 'like', wishlist: false, ...overrides });

describe('Piece reactions and wish list', () => {
  it('starts with no reaction and toggles a like off again', () => {
    const liked = applyPieceAction(undefined, 'like');
    expect(liked).toEqual({ reaction: 'like', wishlist: false });
    expect(applyPieceAction(liked, 'like')).toEqual({ reaction: 'none', wishlist: false });
  });
  it('keeps like and dislike mutually exclusive and toggleable', () => {
    const disliked = applyPieceAction({ reaction: 'like', wishlist: true }, 'dislike');
    expect(disliked).toEqual({ reaction: 'dislike', wishlist: true });
    expect(applyPieceAction(disliked, 'dislike')).toEqual({ reaction: 'none', wishlist: true });
    expect(applyPieceAction(disliked, 'like')).toEqual({ reaction: 'like', wishlist: true });
  });
  it('toggles wish list independently of a reaction', () => {
    expect(applyPieceAction(undefined, 'wishlist')).toEqual({ reaction: 'none', wishlist: true });
    expect(applyPieceAction({ reaction: 'dislike', wishlist: true }, 'wishlist')).toEqual({ reaction: 'dislike', wishlist: false });
  });
});

describe('Saved piece galleries', () => {
  const recent = [look('new'), look('saved'), look('failed', 'thread-2', 'failed')];
  it('keeps all looks, including repeat garments and unsuccessful generations', () => {
    expect(filterLooks(recent, [], [], 'all')).toEqual(recent);
  });
  it('shows the chosen look once per piece, not every generation of that garment', () => {
    expect(filterLooks(recent, [], [preference()], 'liked').map(item => item.id)).toEqual(['saved']);
  });
  it('retrieves saved pieces outside the latest 100 looks', () => {
    expect(filterLooks([look('new')], [look('saved')], [preference({ wishlist: true })], 'wishlist').map(item => item.id)).toEqual(['saved']);
  });
  it('separates reactions and allows the same item in wish list and disliked pieces', () => {
    const prefs = [preference({ reaction: 'dislike', wishlist: true })];
    expect(filterLooks(recent, [], prefs, 'liked')).toEqual([]);
    expect(filterLooks(recent, [], prefs, 'disliked').map(item => item.id)).toEqual(['saved']);
    expect(filterLooks(recent, [], prefs, 'wishlist').map(item => item.id)).toEqual(['saved']);
  });
  it('removes cleared preferences from each saved category', () => {
    const prefs = [preference({ reaction: 'none', wishlist: false })];
    for (const filter of ['liked', 'disliked', 'wishlist'] as const) expect(filterLooks(recent, [], prefs, filter)).toEqual([]);
  });
  it('omits deleted, unfinished or mismatched saved references', () => {
    for (const saved of [[], [look('saved', 'thread-2')], [look('saved', 'thread-1', 'running')]]) {
      expect(filterLooks([], saved, [preference()], 'liked')).toEqual([]);
    }
  });
  it('uses live history data over an older fetched copy and preserves preference order', () => {
    const prefs = [preference({ garmentId: 'thread-2', generationId: 'second' }), preference()];
    expect(filterLooks([look('saved'), look('second', 'thread-2')], [look('saved', 'thread-1', 'running')], prefs, 'liked').map(item => item.id)).toEqual(['second', 'saved']);
  });
});
