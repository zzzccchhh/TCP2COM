import { useId, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown } from 'lucide-react';
import GlassSurface from './GlassSurface';
import { usePresence } from './usePresence';

type Props = {
    label: string; value: string | number; onChange: (value: string) => void;
    options?: [string | number, string][]; list?: string; disabled?: boolean;
};

/** Keep the native form value and keyboard focus; render the options in a glass portal. */
export default function GlassPicker({ label, value, onChange, options, list, disabled = false }: Props) {
    const id = useId();
    const trigger = useRef<HTMLSelectElement | HTMLInputElement | null>(null);
    const popup = useRef<HTMLElement | null>(null);
    const wrap = useRef<HTMLSpanElement>(null);
    const optionNodes = useRef<(HTMLButtonElement | null)[]>([]);
    const [open, setOpen] = useState(false);
    const [positioned, setPositioned] = useState(false);
    const [opticsReady, setOpticsReady] = useState(false);
    const prepared = positioned && opticsReady;
    const presence = usePresence(open ? true : null, 380, prepared);
    const [active, setActive] = useState(0);
    const [query, setQuery] = useState('');
    const [position, setPosition] = useState<CSSProperties>({ visibility: 'hidden' });
    const [highlight, setHighlight] = useState({ top: 0, height: 0 });
    const editable = !!list;
    const entries: [string | number, string][] = options ?? (list
        ? Array.from((document.getElementById(list) as HTMLDataListElement | null)?.options ?? []).map(option => [option.value,
            option.label && option.label !== option.value ? `${option.value} · ${option.label}` : option.value])
        : []);
    const shown = query ? entries.filter(([key, text]) => `${key} ${text}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())) : entries;

    function close() { setOpen(false); }
    function reveal() {
        if (disabled || trigger.current?.matches(':disabled')) return;
        window.dispatchEvent(new CustomEvent('glass-picker-open', { detail: id }));
        setQuery('');
        setActive(Math.max(0, entries.findIndex(([key]) => String(key) === String(value))));
        trigger.current?.focus({ preventScroll: true });
        setOpen(true);
    }
    function choose(index: number) {
        if (disabled || trigger.current?.matches(':disabled')) { close(); return; }
        const entry = shown[index];
        if (!entry) return;
        if (String(entry[0]) !== String(value)) onChange(String(entry[0]));
        close();
        trigger.current?.focus({ preventScroll: true });
    }
    useLayoutEffect(() => { if (open && (disabled || trigger.current?.matches(':disabled'))) close(); });
    useLayoutEffect(() => {
        if (!presence.present) { setPositioned(false); setOpticsReady(false); }
    }, [presence.present]);
    useLayoutEffect(() => {
        if (!presence.present) return;
        function place() {
            const rect = trigger.current?.getBoundingClientRect();
            if (!rect) return;
            const viewport = window.visualViewport;
            const leftEdge = viewport?.offsetLeft ?? 0;
            const topEdge = viewport?.offsetTop ?? 0;
            const viewportWidth = viewport?.width ?? innerWidth;
            const viewportHeight = viewport?.height ?? innerHeight;
            const bottom = topEdge + viewportHeight;
            const below = bottom - rect.bottom - 12;
            const above = rect.top - topEdge - 12;
            const upwards = below < 210 && above > below;
            const width = Math.min(Math.max(rect.width, 228), viewportWidth - 24);
            const maxHeight = Math.min(330, Math.max(90, upwards ? above : below));
            const left = Math.max(leftEdge + 12, Math.min(rect.left, leftEdge + viewportWidth - width - 12));
            const height = Math.min(popup.current?.offsetHeight || maxHeight, maxHeight);
            const top = upwards ? rect.top - height - 7 : rect.bottom + 7;
            setPosition({ left, top, width, maxHeight, '--picker-edge': upwards ? '100%' : '0%',
                '--picker-clip-top': upwards ? '100%' : '0%', '--picker-clip-bottom': upwards ? '0%' : '100%',
            } as CSSProperties);
            // The first measurement can precede the constrained width. Prepare optics only after layout settles.
            if (Math.abs((popup.current?.offsetWidth ?? 0) - width) <= 1) setPositioned(true);
        }
        function outside(event: Event) {
            const target = event.target as Node;
            if (!wrap.current?.contains(target) && !popup.current?.contains(target)) close();
        }
        function escape(event: globalThis.KeyboardEvent) {
            if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); close(); }
        }
        function other(event: Event) { if ((event as CustomEvent).detail !== id) close(); }
        function scroll(event: Event) { if (!popup.current?.contains(event.target as Node)) place(); }
        place();
        const observer = new ResizeObserver(place);
        if (popup.current) observer.observe(popup.current);
        if (trigger.current) observer.observe(trigger.current);
        window.addEventListener('resize', place);
        window.visualViewport?.addEventListener('resize', place);
        window.visualViewport?.addEventListener('scroll', place);
        document.addEventListener('scroll', scroll, true);
        document.addEventListener('pointerdown', outside, true);
        document.addEventListener('keydown', escape, true);
        window.addEventListener('glass-picker-open', other);
        window.addEventListener('workspace-overlay-change', close);
        return () => {
            observer.disconnect();
            window.removeEventListener('resize', place);
            window.visualViewport?.removeEventListener('resize', place);
            window.visualViewport?.removeEventListener('scroll', place);
            document.removeEventListener('scroll', scroll, true);
            document.removeEventListener('pointerdown', outside, true);
            document.removeEventListener('keydown', escape, true);
            window.removeEventListener('glass-picker-open', other);
            window.removeEventListener('workspace-overlay-change', close);
        };
    }, [presence.present, open, id]);
    useLayoutEffect(() => {
        if (!open) return;
        const button = optionNodes.current[Math.min(active, shown.length - 1)];
        if (button) {
            setHighlight({ top: button.offsetTop, height: button.offsetHeight });
            button.scrollIntoView({ block: 'nearest' });
        }
    }, [open, active, query, shown.length]);

    function keyboard(event: KeyboardEvent) {
        if (event.key === 'Tab') { close(); return; }
        if (event.key === 'Escape') { if (open) { event.preventDefault(); event.stopPropagation(); close(); } return; }
        if (event.key === 'F4') { event.preventDefault(); if (open) close(); else reveal(); return; }
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) && (!editable || open || event.key.startsWith('Arrow'))) {
            event.preventDefault();
            if (!open) { reveal(); return; }
            setActive(index => event.key === 'Home' ? 0 : event.key === 'End' ? Math.max(0, shown.length - 1)
                : (index + (event.key === 'ArrowDown' ? 1 : Math.max(0, shown.length - 1))) % Math.max(1, shown.length));
        } else if (event.key === 'Enter' || (!editable && event.key === ' ')) {
            event.preventDefault();
            if (open) { if (shown[active]) choose(active); else close(); } else reveal();
        } else if (!editable && event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
            event.preventDefault();
            if (!open) reveal();
            const index = entries.findIndex(([, text]) => text.toLocaleLowerCase().startsWith(event.key.toLocaleLowerCase()));
            if (index >= 0) setActive(index);
        }
    }
    const accessibility = {
        'aria-expanded': open, 'aria-controls': presence.present ? id : undefined,
        'aria-activedescendant': open && shown[active] ? `${id}-${active}` : undefined,
        'aria-haspopup': 'listbox' as const,
    };
    return <span ref={wrap} data-open={open || undefined} className={`select-wrap glass-picker ${editable ? 'editable' : ''}`}>
        {editable ? <input ref={element => { trigger.current = element; }} aria-label={label} role="combobox" {...accessibility}
            aria-autocomplete="list" autoComplete="off" spellCheck={false} value={value ?? ''} disabled={disabled}
            onKeyDown={keyboard} onClick={() => { if (!open) reveal(); }}
            onChange={event => { if (!open) reveal(); onChange(event.target.value); setQuery(event.target.value); setActive(0); }} />
            : <select ref={element => { trigger.current = element; }} aria-label={label} {...accessibility}
                value={value ?? ''} disabled={disabled} onChange={event => { onChange(event.target.value); close(); }}
                onKeyDown={keyboard} onPointerDown={event => { event.preventDefault(); if (open) close(); else reveal(); }}>
                {entries.map(([key, text]) => <option key={key} value={key} aria-hidden={open || undefined}>{text}</option>)}
            </select>}
        {editable ? <button className="picker-toggle" type="button" aria-label={`选择${label}`} disabled={disabled}
            onPointerDown={event => event.preventDefault()} onClick={() => { if (open) close(); else reveal(); }}><ChevronDown size={13} /></button>
            : <ChevronDown size={13} />}
        {presence.present && createPortal(<div ref={element => { popup.current = element; }} className="picker-position"
            style={position} data-state={presence.entered ? 'open' : 'closed'} data-ready={prepared || undefined}
            aria-hidden={!open || undefined} inert={!open}>
            <GlassSurface className="picker-window" aria-label={`${label}选项`} opticsEnabled={positioned}
                onGlassReady={() => setOpticsReady(true)}>
                <div className="picker-content">
                    <div className="picker-heading">{label}</div>
                    <div className="picker-list" role="listbox" id={id} aria-label={`${label}选项`}>
                        {shown.length > 0 && <GlassSurface as="div" className="liquid-indicator picker-highlight" aria-hidden="true"
                            style={{ '--liquid-y': `${highlight.top}px`, height: highlight.height } as CSSProperties} />}
                        {shown.map(([key, text], index) => <button type="button" role="option" tabIndex={-1} id={`${id}-${index}`} key={key}
                            ref={element => { optionNodes.current[index] = element; }} aria-selected={String(key) === String(value)}
                            className={index === active ? 'is-active' : ''}
                            onPointerDown={event => { event.preventDefault(); if (event.button === 0) setActive(index); }} onClick={() => choose(index)}>
                            <span>{text}</span>{String(key) === String(value) && <Check size={14} />}
                        </button>)}
                        {!shown.length && <p className="picker-empty">没有匹配的选项，可直接输入。</p>}
                    </div>
                </div>
            </GlassSurface>
        </div>, document.body)}
    </span>;
}
