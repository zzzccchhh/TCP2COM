import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent, ReactNode } from 'react';
import GlassSurface from './GlassSurface';

type Item = { value: string; label: ReactNode; disabled?: boolean };
type Rect = { left: number; top: number; width: number; height: number };
type Props = { label: string; value: string; items: Item[]; onChange: (value: string) => void; disabled?: boolean; className?: string };

export default function LiquidSegments({ label, value, items, onChange, disabled = false, className = '' }: Props) {
    const root = useRef<HTMLDivElement>(null);
    const buttons = useRef<(HTMLButtonElement | null)[]>([]);
    const rects = useRef<Rect[]>([]);
    const drag = useRef<{ id: number; index: number; startX: number; startY: number; origin: Rect; moved: boolean } | null>(null);
    const sample = useRef({ x: 0, time: 0, velocity: 0 });
    const lens = useRef<HTMLElement | null>(null);
    const motion = useRef({ frame: 0, bend: 0, speed: 0 });
    const [visual, setVisual] = useState<Rect>({ left: 3, top: 3, width: 0, height: 0 });
    const [preview, setPreview] = useState(-1);
    const [dragging, setDragging] = useState(false);
    const [pressed, setPressed] = useState(false);
    const selected = items.findIndex(item => item.value === value);
    const selectedRef = useRef(selected);
    selectedRef.current = selected;

    function restore() {
        const rect = rects.current[selectedRef.current];
        if (rect) setVisual(rect);
        setPreview(-1);
    }

    // Only the local lens changes shape. Its sampling bitmap and anchor stay stable.
    function relax() {
        if (motion.current.frame) return;
        let last = performance.now();
        function tick(now: number) {
            const state = motion.current;
            const dt = Math.min(.032, Math.max(.001, (now - last) / 1000));
            last = now;
            const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
            const target = !reduced && drag.current?.moved
                ? Math.max(-1, Math.min(1, sample.current.velocity / 1.3)) * Math.exp(-(now - sample.current.time) / 85) : 0;
            state.speed += ((target - state.bend) * 260 - state.speed * 25) * dt;
            state.bend += state.speed * dt;
            if (reduced) { state.bend = 0; state.speed = 0; }
            const bend = Math.max(-1, Math.min(1, state.bend));
            lens.current?.style.setProperty('--liquid-bend', String(bend));
            lens.current?.style.setProperty('--liquid-strain', String(Math.abs(bend) * .13));
            if (Math.abs(state.bend) + Math.abs(state.speed) + Math.abs(target) < .002) {
                lens.current?.style.setProperty('--liquid-bend', '0');
                lens.current?.style.setProperty('--liquid-strain', '0');
                state.bend = 0; state.speed = 0; state.frame = 0;
            } else state.frame = requestAnimationFrame(tick);
        }
        motion.current.frame = requestAnimationFrame(tick);
    }
    function cancel() {
        const current = drag.current;
        drag.current = null;
        setDragging(false); setPressed(false); restore(); relax();
        if (current && root.current?.hasPointerCapture(current.id)) root.current.releasePointerCapture(current.id);
    }
    useEffect(() => () => cancelAnimationFrame(motion.current.frame), []);

    useLayoutEffect(() => {
        function measure() {
            if (drag.current) cancel();
            rects.current = buttons.current.slice(0, items.length).map(button => button
                ? { left: button.offsetLeft, top: button.offsetTop, width: button.offsetWidth, height: button.offsetHeight }
                : { left: 0, top: 0, width: 0, height: 0 });
            if (!drag.current) restore();
        }
        const observer = new ResizeObserver(measure);
        observer.observe(root.current!);
        buttons.current.forEach(button => { if (button) observer.observe(button); });
        measure();
        // First layout appears at the selected item, without flying in from the origin.
        const node = root.current!;
        let entry = requestAnimationFrame(() => { entry = requestAnimationFrame(() => { node.dataset.positioned = 'true'; }); });
        return () => { observer.disconnect(); cancelAnimationFrame(entry); delete node.dataset.positioned; };
    }, [items.length]);
    useLayoutEffect(() => { cancel(); }, [value, disabled]);

    function available(index: number) {
        return !!buttons.current[index] && !buttons.current[index]!.matches(':disabled');
    }
    function nearest(x: number) {
        let index = -1, distance = Infinity;
        rects.current.forEach((rect, i) => {
            const delta = Math.abs(x - rect.left - rect.width / 2);
            if (available(i) && delta < distance) { distance = delta; index = i; }
        });
        return index;
    }
    function follow(event: PointerEvent<HTMLDivElement>) {
        if (disabled || !drag.current || drag.current.id !== event.pointerId) return;
        const node = root.current!;
        const x = event.clientX - node.getBoundingClientRect().left - node.clientLeft;
        const current = drag.current;
        const dx = event.clientX - current.startX;
        const dy = event.clientY - current.startY;
        if (!current.moved) {
            const threshold = event.pointerType === 'touch' ? 8 : 5;
            if (Math.abs(dy) > threshold && Math.abs(dy) > Math.abs(dx)) { cancel(); return; }
            if (Math.abs(dx) < threshold) return;
            current.moved = true;
            setDragging(true);
        }
        const index = nearest(x);
        if (index < 0) return;
        // Keep the original grab offset and lens size, even across unequal-width options.
        const rect = current.origin;
        const first = rects.current.find((_, i) => available(i))!;
        const last = rects.current.filter((_, i) => available(i)).at(-1)!;
        const left = Math.max(first.left, Math.min(last.left + last.width - rect.width, rect.left + dx));
        setPreview(index);
        setVisual({ ...rect, left });
        const now = performance.now();
        const velocity = sample.current.time ? (x - sample.current.x) / Math.max(8, now - sample.current.time) : 0;
        sample.current = { x, time: now, velocity: sample.current.velocity * .35 + velocity * .65 };
        relax();
        if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
            node.style.setProperty('--glass-light-x', `${Math.max(0, Math.min(rect.width, x - left))}px`);
        }
        if (drag.current) drag.current.index = index;
    }
    function finish(event: PointerEvent<HTMLDivElement>, cancelled = false) {
        const current = drag.current;
        if (!current || current.id !== event.pointerId) return;
        drag.current = null;
        setDragging(false); setPressed(false); setPreview(-1); relax();
        if (root.current?.hasPointerCapture(event.pointerId)) root.current.releasePointerCapture(event.pointerId);
        const rect = rects.current[cancelled ? selected : current.index];
        if (rect) setVisual(rect);
        if (!cancelled) buttons.current[current.index]?.focus({ preventScroll: true });
        if (!cancelled && available(current.index) && items[current.index].value !== value) onChange(items[current.index].value);
    }
    return <div ref={root} className={`segmented liquid-segments ${className}`} role="group" aria-label={label}
        data-dragging={dragging || undefined} data-pressed={pressed || undefined} onPointerMove={follow}
        onPointerLeave={() => { if (!drag.current) restore(); }}
        onPointerDown={event => {
            if (drag.current || event.button !== 0 || disabled || !(event.target as Element).closest('button')?.matches(':enabled')) return;
            const x = event.clientX - root.current!.getBoundingClientRect().left - root.current!.clientLeft;
            const index = nearest(x);
            if (index < 0) return;
            root.current!.setPointerCapture(event.pointerId);
            drag.current = { id: event.pointerId, index, startX: event.clientX, startY: event.clientY, origin: rects.current[index], moved: false };
            sample.current = { x, time: performance.now(), velocity: 0 };
            setPressed(true); setPreview(index); setVisual(rects.current[index]);
        }} onPointerUp={event => finish(event)} onPointerCancel={event => finish(event, true)}
        onLostPointerCapture={event => { if (drag.current) finish(event, true); }}
        onKeyDown={event => {
            if (event.key === 'Escape' && drag.current) {
                event.preventDefault(); event.stopPropagation();
                cancel();
                return;
            }
            const index = buttons.current.indexOf(event.target as HTMLButtonElement);
            if (index < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const enabled = items.map((_, i) => i).filter(available);
            const cursor = enabled.indexOf(index);
            const next = event.key === 'Home' ? enabled[0] : event.key === 'End' ? enabled.at(-1)!
                : enabled[(cursor + (event.key === 'ArrowRight' ? 1 : enabled.length - 1)) % enabled.length];
            if (next !== undefined) { buttons.current[next]?.focus(); if (items[next].value !== value) onChange(items[next].value); }
        }}>
        <div aria-hidden="true" className="liquid-anchor" style={{
            width: visual.width, height: visual.height,
            '--liquid-x': `${visual.left}px`, '--liquid-y': `${visual.top}px`,
            '--lens-radius': `${visual.height / 2}px`,
        } as CSSProperties}>
            <GlassSurface as="div" surfaceRef={lens} className="liquid-indicator" />
        </div>
        {items.map((item, index) => <button type="button" key={item.value} ref={button => { buttons.current[index] = button; }}
            disabled={disabled || item.disabled} aria-pressed={item.value === value}
            className={`${item.value === value ? 'selected' : ''} ${index === preview ? 'is-preview' : ''}`}
            onClick={event => { if (event.detail === 0 && item.value !== value) onChange(item.value); }}>
            {item.label}
        </button>)}
    </div>;
}
