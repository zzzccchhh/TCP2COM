import { useId, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown } from 'lucide-react';
import GlassSurface from './GlassSurface';
import { usePresence } from './usePresence';
import { useExitBounds } from './useExitBounds';
import { useGlassExpansion } from './useGlassExpansion';
import type { ExpansionOrigin } from './useGlassExpansion';

type Props = {
    label: string; value: string | number; onChange: (value: string) => void;
    options?: [string | number, string][]; list?: string; disabled?: boolean;
};

/** Keep the native form value and keyboard focus; render the options in a glass portal. */
export default function GlassPicker({ label, value, onChange, options, list, disabled = false }: Props) {
    const id = useId();
    const trigger = useRef<HTMLSelectElement | HTMLInputElement | null>(null);
    const popup = useRef<HTMLElement | null>(null);
    const listbox = useRef<HTMLDivElement>(null);
    const wrap = useRef<HTMLSpanElement>(null);
    const toggle = useRef<HTMLButtonElement>(null);
    const origin = useRef<ExpansionOrigin>({ x: 0, upwards: false });
    const optionNodes = useRef<(HTMLButtonElement | null)[]>([]);
    const [open, setOpen] = useState(false);
    const [positioned, setPositioned] = useState(false);
    const [opticsReady, setOpticsReady] = useState(false);
    const prepared = positioned && opticsReady;
    const presence = usePresence(open ? true : null, 380, prepared);
    useExitBounds(open, presence.present, popup);
    const [active, setActive] = useState(0);
    const [query, setQuery] = useState('');
    const [editing, setEditing] = useState(false);
    const editMode = useRef(false);
    const browsingFromEdit = useRef(false);
    const navigated = useRef(false);
    const [position, setPosition] = useState<CSSProperties>({ visibility: 'hidden' });
    const [highlight, setHighlight] = useState({ top: 0, height: 0 });
    const editable = !!list;
    useGlassExpansion(popup, origin, presence.present, presence.entered, open && prepared, position, presence.finishExit);
    const entries: [string | number, string][] = options ?? (list
        ? Array.from((document.getElementById(list) as HTMLDataListElement | null)?.options ?? []).map(option => [option.value,
            option.label && option.label !== option.value ? `${option.value} · ${option.label}` : option.value])
        : []);
    const shown = query ? entries.filter(([key, text]) => `${key} ${text}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())) : entries;

    function stopEditing() {
        editMode.current = false; setEditing(false);
        // Set the native flag before focus changes, so touch engines cannot reopen the keyboard.
        if (trigger.current instanceof HTMLInputElement) { trigger.current.readOnly = true; trigger.current.inputMode = 'none'; }
    }
    function focusBrowser(inList = false) {
        stopEditing();
        (editable ? inList && presence.entered ? listbox.current : toggle.current : trigger.current)?.focus({ preventScroll: true });
    }
    function close() { stopEditing(); setOpen(false); }
    function reveal(input = false) {
        if (disabled || trigger.current?.matches(':disabled')) return;
        window.dispatchEvent(new CustomEvent('glass-picker-open', { detail: id }));
        if (!open || !input) {
            setQuery('');
            navigated.current = false;
            setActive(Math.max(0, entries.findIndex(([key]) => String(key) === String(value))));
        }
        if (input && editable && trigger.current instanceof HTMLInputElement) {
            navigated.current = false;
            editMode.current = true; setEditing(true); trigger.current.readOnly = false;
            trigger.current.inputMode = 'text';
            trigger.current.focus({ preventScroll: true });
        } else focusBrowser(true);
        setOpen(true);
    }
    function choose(index: number) {
        if (disabled || trigger.current?.matches(':disabled')) { close(); return; }
        const entry = shown[index];
        if (!entry) return;
        if (String(entry[0]) !== String(value)) onChange(String(entry[0]));
        close();
        focusBrowser();
    }
    useLayoutEffect(() => { if (open && (disabled || trigger.current?.matches(':disabled'))) close(); });
    useLayoutEffect(() => {
        if (!presence.present) { setPositioned(false); setOpticsReady(false); }
    }, [presence.present]);
    useLayoutEffect(() => {
        // Browsing focus belongs to a listbox, not an editable field or an unsupported button active-descendant.
        if (editable && open && presence.entered && !editing) listbox.current?.focus({ preventScroll: true });
    }, [editable, open, presence.entered, editing]);
    useLayoutEffect(() => {
        if (!presence.present) return;
        function place() {
            if (!open) return;
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
            const width = Math.min(Math.max(rect.width, 240), viewportWidth - 24);
            const maxHeight = Math.min(330, Math.max(90, upwards ? above : below));
            const left = Math.max(leftEdge + 12, Math.min(rect.left, leftEdge + viewportWidth - width - 12));
            const height = Math.min(popup.current?.offsetHeight || maxHeight, maxHeight);
            const top = upwards ? rect.top - height - 7 : rect.bottom + 7;
            const source = editable ? toggle.current?.getBoundingClientRect() : null;
            origin.current = { x: (source ? source.left + source.width / 2 : rect.right - rect.height / 2) - left,
                upwards };
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
            if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); close(); focusBrowser(); }
        }
        function other(event: Event) { if ((event as CustomEvent).detail !== id) close(); }
        function navigationBack(event: Event) { if (open) { event.preventDefault(); close(); focusBrowser(); } }
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
        window.addEventListener('mobile-page-back', navigationBack);
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
            window.removeEventListener('mobile-page-back', navigationBack);
        };
    }, [presence.present, open, id]);
    useLayoutEffect(() => {
        if (!open) return;
        const button = optionNodes.current[Math.min(active, shown.length - 1)];
        if (button) {
            setHighlight({ top: button.offsetTop, height: button.offsetHeight });
            const listbox = button.parentElement!;
            if (button.offsetTop < listbox.scrollTop) listbox.scrollTop = button.offsetTop;
            else if (button.offsetTop + button.offsetHeight > listbox.scrollTop + listbox.clientHeight) {
                listbox.scrollTop = button.offsetTop + button.offsetHeight - listbox.clientHeight;
            }
        }
    }, [open, active, query, shown.length]);

    function keyboard(event: KeyboardEvent) {
        if (event.key === 'Tab') { close(); if (event.currentTarget === listbox.current) focusBrowser(); return; }
        if (editable && event.key === 'F2') { event.preventDefault(); reveal(true); return; }
        if (event.key === 'Escape') { if (open) { event.preventDefault(); event.stopPropagation(); close(); } return; }
        if (event.key === 'F4') { event.preventDefault(); if (open) close(); else reveal(); return; }
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) && (!editable || !editMode.current || event.key.startsWith('Arrow'))) {
            event.preventDefault();
            if (!open) { reveal(); return; }
            navigated.current = true;
            setActive(index => event.key === 'Home' ? 0 : event.key === 'End' ? Math.max(0, shown.length - 1)
                : (index + (event.key === 'ArrowDown' ? 1 : Math.max(0, shown.length - 1))) % Math.max(1, shown.length));
        } else if (event.key === 'Enter' || (!editable && event.key === ' ')) {
            event.preventDefault();
            if (open) {
                if (editable && editMode.current && !navigated.current) { close(); focusBrowser(); }
                else if (shown[active]) choose(active); else { close(); focusBrowser(); }
            } else reveal();
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
    return <span ref={wrap} data-open={open || undefined} data-editing={editing || undefined} className={`select-wrap glass-picker ${editable ? 'editable' : ''}`}
        onPointerDownCapture={() => { wrap.current!.dataset.pointerFocus = 'true'; }}
        onKeyDownCapture={() => { delete wrap.current!.dataset.pointerFocus; }}
        onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) delete event.currentTarget.dataset.pointerFocus; }}>
        {editable ? <input ref={element => { trigger.current = element; }} aria-label={label} role="combobox" {...accessibility}
            aria-autocomplete="list" autoComplete="off" spellCheck={false} value={value ?? ''} disabled={disabled}
            readOnly={!editing} inputMode={editing ? 'text' : 'none'} aria-keyshortcuts="F2" onKeyDown={keyboard}
            onPointerDown={event => { if (event.button === 0) reveal(true); }}
            onBlur={() => stopEditing()}
            onChange={event => { if (!open) reveal(true); navigated.current = false; onChange(event.target.value); setQuery(event.target.value); setActive(0); }} />
            : <select ref={element => { trigger.current = element; }} aria-label={label} {...accessibility}
                value={value ?? ''} disabled={disabled} onChange={event => { onChange(event.target.value); close(); }}
                onKeyDown={keyboard} onPointerDown={event => { event.preventDefault(); if (open) close(); else reveal(); }}>
                {entries.map(([key, text]) => <option key={key} value={key} aria-hidden={open || undefined}>{text}</option>)}
            </select>}
        {editable ? <button ref={toggle} className="picker-toggle" type="button" aria-label={`选择${label}`} disabled={disabled}
            aria-expanded={open} aria-controls={presence.present ? id : undefined} aria-haspopup="listbox" onKeyDown={keyboard}
            onPointerDown={event => { browsingFromEdit.current = editMode.current; event.preventDefault(); focusBrowser(); }}
            onClick={() => {
                if (open && !browsingFromEdit.current && !editMode.current) close();
                else { setQuery(''); reveal(); }
                browsingFromEdit.current = false;
            }}><ChevronDown size={13} /></button>
            : <ChevronDown size={13} />}
        {presence.present && createPortal(<div ref={element => { popup.current = element; }} className="picker-position"
            style={position} data-state={presence.entered ? 'open' : 'closed'} data-ready={prepared || undefined}
            aria-hidden={!open || undefined} inert={!open}>
            <span className="glass-expansion-clock" aria-hidden="true" />
            <div className="glass-expansion-outline" aria-hidden="true" />
            <GlassSurface className="picker-window" aria-label={`${label}选项`} opticsEnabled={positioned}
                onGlassReady={() => setOpticsReady(true)}>
                <div className="picker-content glass-expansion-content">
                    <div className="picker-heading">{label}</div>
                    <div ref={listbox} className="picker-list" role="listbox" id={id} aria-label={`${label}选项`}
                        tabIndex={editable ? -1 : undefined} aria-activedescendant={open && shown[active] ? `${id}-${active}` : undefined} onKeyDown={keyboard}>
                        {shown.length > 0 && <GlassSurface as="div" className="liquid-indicator picker-highlight" aria-hidden="true" opticsEnabled={false} data-glass="frosted"
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
