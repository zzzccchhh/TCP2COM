import { useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent, ReactNode } from 'react';
import { Check } from 'lucide-react';
import GlassSurface from './GlassSurface';

type Props = {
    checked: boolean; onChange: (value: boolean) => void; children: ReactNode;
    disabled?: boolean; confirmation?: boolean;
};

/** Native input owns keyboard/form semantics; the lens is only a visual drag surface. */
export default function LiquidToggle({ checked, onChange, children, disabled = false, confirmation = false }: Props) {
    const input = useRef<HTMLInputElement>(null);
    const track = useRef<HTMLSpanElement>(null);
    const gesture = useRef<{ id: number; start: number; progress: number; moved: boolean } | null>(null);
    const [progress, setProgress] = useState<number | null>(null);

    function cancel() {
        const current = gesture.current;
        gesture.current = null;
        setProgress(null);
        if (current && track.current?.hasPointerCapture(current.id)) track.current.releasePointerCapture(current.id);
    }
    useLayoutEffect(() => {
        if (gesture.current && input.current?.matches(':disabled')) cancel();
    });
    useLayoutEffect(() => { cancel(); }, [checked]);

    function finish(event: PointerEvent<HTMLSpanElement>, cancelled = false) {
        const current = gesture.current;
        if (!current || event.pointerId !== current.id) return;
        const next = current.moved ? current.progress >= .5 : !checked;
        cancel();
        if (!cancelled && !input.current?.matches(':disabled') && next !== checked) onChange(next);
    }

    return <label className={`check liquid-toggle ${confirmation ? 'confirmation' : ''}`}
        data-checked={checked} data-dragging={progress !== null || undefined}>
        <input ref={input} type="checkbox" role={confirmation ? undefined : 'switch'} checked={checked}
            disabled={disabled} onChange={event => onChange(event.target.checked)} onKeyDown={event => {
                if (event.key === 'Escape' && gesture.current) { event.preventDefault(); event.stopPropagation(); cancel(); }
            }} />
        {confirmation ? <GlassSurface as="span" className="liquid-check-mark" aria-hidden="true"><Check size={15} /></GlassSurface>
            : <span ref={track} className="liquid-toggle-track" aria-hidden="true"
                style={{ '--toggle-progress': progress ?? Number(checked) } as CSSProperties}
                onClick={event => event.preventDefault()}
                onPointerDown={event => {
                    if (event.button !== 0 || input.current?.matches(':disabled')) return;
                    event.preventDefault();
                    input.current?.focus({ preventScroll: true });
                    event.currentTarget.setPointerCapture(event.pointerId);
                    gesture.current = { id: event.pointerId, start: event.clientX, progress: Number(checked), moved: false };
                    setProgress(Number(checked));
                }} onPointerMove={event => {
                    const current = gesture.current;
                    if (!current || current.id !== event.pointerId) return;
                    const delta = event.clientX - current.start;
                    current.moved ||= Math.abs(delta) > 3;
                    current.progress = Math.max(0, Math.min(1, Number(checked) + delta / 22));
                    setProgress(current.progress);
                }} onPointerUp={event => finish(event)} onPointerCancel={event => finish(event, true)}
                onLostPointerCapture={event => { if (gesture.current) finish(event, true); }}>
                <span className="toggle-state-on" /><span className="toggle-state-off" />
                <GlassSurface as="span" className="liquid-toggle-thumb" />
            </span>}
        <span className="toggle-label">{children}</span>
    </label>;
}
