import { useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent, ReactNode } from 'react';
import { Check } from 'lucide-react';
import GlassSurface from './GlassSurface';
import { useLiquidLens } from './useLiquidLens';

type Props = {
    checked: boolean; onChange: (value: boolean) => void; children: ReactNode;
    disabled?: boolean; confirmation?: boolean;
};

/** Native input owns keyboard/form semantics; the lens is only a visual drag surface. */
export default function LiquidToggle({ checked, onChange, children, disabled = false, confirmation = false }: Props) {
    const input = useRef<HTMLInputElement>(null);
    const track = useRef<HTMLSpanElement>(null);
    const lens = useRef<HTMLElement | null>(null);
    const motion = useLiquidLens(lens);
    const sample = useRef({ x: 0, time: 0, velocity: 0 });
    const gesture = useRef<{ id: number; start: number; progress: number; moved: boolean } | null>(null);
    const [progress, setProgress] = useState<number | null>(null);

    function cancel(cancelled = true) {
        const current = gesture.current;
        gesture.current = null;
        setProgress(null);
        motion.release(cancelled);
        if (current && track.current?.hasPointerCapture(current.id)) track.current.releasePointerCapture(current.id);
    }
    useLayoutEffect(() => {
        if (input.current?.matches(':disabled')) { if (gesture.current) cancel(); motion.reset(); }
    });
    useLayoutEffect(() => { if (gesture.current) cancel(); }, [checked]);

    function finish(event: PointerEvent<HTMLSpanElement>, cancelled = false) {
        const current = gesture.current;
        if (!current || event.pointerId !== current.id) return;
        const next = current.moved ? current.progress >= .5 : !checked;
        cancel(cancelled);
        if (!cancelled && !input.current?.matches(':disabled') && next !== checked) onChange(next);
    }

    return <label className={`check liquid-toggle ${confirmation ? 'confirmation' : ''}`}
        data-checked={checked} data-dragging={progress !== null || undefined}>
        <input ref={input} type="checkbox" role={confirmation ? undefined : 'switch'} checked={checked}
            disabled={disabled} onChange={event => { if (!confirmation) motion.pulse(); onChange(event.target.checked); }} onKeyDown={event => {
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
                    sample.current = { x: event.clientX, time: performance.now(), velocity: 0 };
                    motion.press();
                }} onPointerMove={event => {
                    const current = gesture.current;
                    if (!current || current.id !== event.pointerId) return;
                    const delta = event.clientX - current.start;
                    current.moved ||= Math.abs(delta) > 3;
                    const thumb = track.current!.querySelector<HTMLElement>('.liquid-toggle-thumb')!;
                    const travel = Math.max(1, track.current!.clientWidth - thumb.offsetWidth - 4);
                    current.progress = Math.max(0, Math.min(1, Number(checked) + delta / travel));
                    setProgress(current.progress);
                    const now = performance.now();
                    const velocity = (event.clientX - sample.current.x) / Math.max(8, now - sample.current.time);
                    sample.current = { x: event.clientX, time: now, velocity: sample.current.velocity * .35 + velocity * .65 };
                    motion.move(sample.current.velocity);
                }} onPointerUp={event => finish(event)} onPointerCancel={event => finish(event, true)}
                onLostPointerCapture={event => { if (gesture.current) finish(event, true); }}>
                <span className="toggle-state-on" /><span className="toggle-state-off" />
                <span className="liquid-toggle-anchor"><GlassSurface as="span" surfaceRef={lens} className="liquid-toggle-thumb liquid-elastic-lens"><span className="liquid-raised-optics" /></GlassSurface></span>
            </span>}
        <span className="toggle-label">{children}</span>
    </label>;
}
