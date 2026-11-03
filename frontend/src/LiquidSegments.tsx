import { useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent, ReactNode } from 'react';
import GlassSurface from './GlassSurface';

type Item = { value: string; label: ReactNode; disabled?: boolean };
type Rect = { left: number; top: number; width: number; height: number };
type Props = { label: string; value: string; items: Item[]; onChange: (value: string) => void; disabled?: boolean; className?: string };

export default function LiquidSegments({ label, value, items, onChange, disabled = false, className = '' }: Props) {
    const root = useRef<HTMLDivElement>(null);
    const buttons = useRef<(HTMLButtonElement | null)[]>([]);
    const rects = useRef<Rect[]>([]);
    const drag = useRef<{ id: number; index: number } | null>(null);
    const [visual, setVisual] = useState<Rect>({ left: 3, top: 3, width: 0, height: 0 });
    const [preview, setPreview] = useState(-1);
    const [dragging, setDragging] = useState(false);
    const [stretch, setStretch] = useState(0);
    const selected = items.findIndex(item => item.value === value);
    const selectedRef = useRef(selected);
    selectedRef.current = selected;

    function restore() {
        const rect = rects.current[selectedRef.current];
        if (rect) setVisual(rect);
        setPreview(-1); setStretch(0);
    }

    useLayoutEffect(() => {
        function measure() {
            rects.current = buttons.current.slice(0, items.length).map(button => button
                ? { left: button.offsetLeft, top: button.offsetTop, width: button.offsetWidth, height: button.offsetHeight }
                : { left: 0, top: 0, width: 0, height: 0 });
            if (!drag.current) restore();
        }
        const observer = new ResizeObserver(measure);
        observer.observe(root.current!);
        buttons.current.forEach(button => { if (button) observer.observe(button); });
        measure();
        return () => observer.disconnect();
    }, [items.length]);
    useLayoutEffect(() => { drag.current = null; setDragging(false); restore(); }, [value, disabled]);

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
        if (disabled || (!drag.current && event.pointerType !== 'mouse')) return;
        const node = root.current!;
        const x = event.clientX - node.getBoundingClientRect().left - node.clientLeft;
        const index = nearest(x);
        if (index < 0) return;
        const rect = rects.current[index];
        const first = rects.current.find((_, i) => available(i))!;
        const last = rects.current.filter((_, i) => available(i)).at(-1)!;
        const left = Math.max(first.left, Math.min(last.left + last.width - rect.width, x - rect.width / 2));
        setPreview(index);
        setVisual({ ...rect, left });
        setStretch(drag.current ? Math.min(.12, Math.abs(x - rect.left - rect.width / 2) / rect.width * .2 + .035) : 0);
        if (drag.current) drag.current.index = index;
    }
    function finish(event: PointerEvent<HTMLDivElement>, cancelled = false) {
        const current = drag.current;
        if (!current || current.id !== event.pointerId) return;
        drag.current = null;
        setDragging(false); setStretch(0); setPreview(-1);
        if (root.current?.hasPointerCapture(event.pointerId)) root.current.releasePointerCapture(event.pointerId);
        const rect = rects.current[cancelled ? selected : current.index];
        if (rect) setVisual(rect);
        if (!cancelled) buttons.current[current.index]?.focus({ preventScroll: true });
        if (!cancelled && available(current.index) && items[current.index].value !== value) onChange(items[current.index].value);
    }
    return <div ref={root} className={`segmented liquid-segments ${className}`} role="group" aria-label={label}
        data-dragging={dragging || undefined} onPointerMove={follow}
        onPointerLeave={() => { if (!drag.current) restore(); }}
        onPointerDown={event => {
            if (event.button !== 0 || disabled || !(event.target as Element).closest('button')?.matches(':enabled')) return;
            const x = event.clientX - root.current!.getBoundingClientRect().left - root.current!.clientLeft;
            const index = nearest(x);
            if (index < 0) return;
            root.current!.setPointerCapture(event.pointerId);
            drag.current = { id: event.pointerId, index };
            setDragging(true); follow(event);
        }} onPointerUp={event => finish(event)} onPointerCancel={event => finish(event, true)}
        onLostPointerCapture={event => { if (drag.current) finish(event, true); }}
        onKeyDown={event => {
            if (event.key === 'Escape' && drag.current) {
                event.preventDefault(); event.stopPropagation();
                const pointer = drag.current.id;
                drag.current = null; setDragging(false); restore();
                if (root.current!.hasPointerCapture(pointer)) root.current!.releasePointerCapture(pointer);
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
        <GlassSurface as="div" aria-hidden="true" className="liquid-indicator" style={{
            width: visual.width, height: visual.height,
            '--liquid-x': `${visual.left}px`, '--liquid-y': `${visual.top}px`,
            '--liquid-stretch': stretch,
        } as CSSProperties} />
        {items.map((item, index) => <button type="button" key={item.value} ref={button => { buttons.current[index] = button; }}
            disabled={disabled || item.disabled} aria-pressed={item.value === value}
            className={`${item.value === value ? 'selected' : ''} ${index === preview ? 'is-preview' : ''}`}
            onClick={event => { if (event.detail === 0 && item.value !== value) onChange(item.value); }}>
            {item.label}
        </button>)}
    </div>;
}
