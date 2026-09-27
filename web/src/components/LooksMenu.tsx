import { useEffect, useId, useRef, useState } from 'react';
import { Icon, type IconName } from './Icon';
import { looksFilters, type LooksFilter } from '../lib/piece-preferences';

const icons: Record<LooksFilter, IconName> = { all: 'grid', liked: 'like', disliked: 'dislike', wishlist: 'bookmark' };

export function LooksMenu({ mobile, active, filter, onSelect }: {
  mobile?: boolean; active: boolean; filter: LooksFilter; onSelect: (filter: LooksFilter) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);
  return <div ref={root} className={`looks-menu ${mobile ? 'looks-menu-mobile' : ''}`} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false); }}>
    <button ref={trigger} type="button" className={mobile ? `looks-menu-trigger ${active ? 'active' : ''}` : `desktop-nav ${active ? 'selected' : ''}`} aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}>
      {mobile && <Icon name="grid" size={20} />}<span>Your looks</span><Icon name="down" size={12} />
    </button>
    {open && <div id={id} className="looks-menu-options" aria-label="Your looks categories">
      {looksFilters.map(item => <button key={item.id} type="button" aria-current={active && filter === item.id ? 'page' : undefined} onClick={() => { setOpen(false); onSelect(item.id); }}><Icon name={icons[item.id]} size={17} /><span>{item.id === 'all' ? 'All looks' : item.label}</span>{active && filter === item.id && <Icon name="check" size={15} />}</button>)}
    </div>}
  </div>;
}
