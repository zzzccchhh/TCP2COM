import { useEffect, useLayoutEffect, useRef } from 'react';
import type { ComponentPropsWithRef } from 'react';
import GlassSurface from './GlassSurface';
import { useLiquidLens } from './useLiquidLens';

/** Native button semantics; only its decorative glass responds to contact. */
export default function LiquidButton({ children, className = '', disabled, ref, onClick, onPointerDown,
    onPointerMove, onPointerUp, onPointerCancel, onPointerLeave, onKeyDown, onKeyUp, onBlur, ...props }: ComponentPropsWithRef<'button'>) {
    const lens = useRef<HTMLElement | null>(null);
    const motion = useLiquidLens(lens);
    const keyboardClick = useRef(false);
    const gesture = useRef({ id: -1, x: 0, y: 0, lastX: 0, time: 0, cancelled: false, key: '' });
    const subtle = /(?:^|\s)(?:text-button|page-back)(?:\s|$)/.test(className);

    function release(cancelled = false) {
        const current = gesture.current;
        if (current.id === -1 && !current.key) return;
        current.id = -1; current.key = ''; current.cancelled ||= cancelled;
        motion.release(cancelled);
    }
    function light(button: HTMLButtonElement, clientX: number, clientY: number) {
        const box = button.getBoundingClientRect();
        lens.current?.style.setProperty('--glass-light-x', `${Math.max(0, Math.min(box.width, clientX - box.left))}px`);
        lens.current?.style.setProperty('--glass-light-y', `${Math.max(0, Math.min(box.height, clientY - box.top))}px`);
    }
    useEffect(() => {
        const up = (event: PointerEvent) => { if (gesture.current.id === event.pointerId) release(event.type === 'pointercancel'); };
        const blur = () => release(true);
        const visibility = () => { if (document.hidden) release(true); };
        window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up);
        window.addEventListener('blur', blur); document.addEventListener('visibilitychange', visibility);
        return () => {
            window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up);
            window.removeEventListener('blur', blur); document.removeEventListener('visibilitychange', visibility);
        };
    }, [motion]);
    useLayoutEffect(() => { if (disabled) release(true); }, [disabled]);

    return <button {...props} ref={ref} disabled={disabled} className={`${className} liquid-button`}
        onPointerDown={event => {
            onPointerDown?.(event);
            if (event.defaultPrevented || event.button !== 0 || !event.isPrimary || event.currentTarget.matches(':disabled') || gesture.current.id !== -1) return;
            keyboardClick.current = false;
            gesture.current = { id: event.pointerId, x: event.clientX, y: event.clientY,
                lastX: event.clientX, time: performance.now(), cancelled: false, key: '' };
            light(event.currentTarget, event.clientX, event.clientY); motion.press();
        }}
        onPointerMove={event => {
            onPointerMove?.(event);
            const current = gesture.current;
            if (current.id !== event.pointerId) return;
            const dx = event.clientX - current.x, dy = event.clientY - current.y;
            if (event.pointerType !== 'mouse' && Math.abs(dy) > 8 && Math.abs(dy) > Math.abs(dx)) { release(true); return; }
            const box = event.currentTarget.getBoundingClientRect();
            if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) { release(true); return; }
            const now = performance.now(), distance = event.clientX - current.lastX;
            light(event.currentTarget, event.clientX, event.clientY);
            motion.move(distance / Math.max(8, now - current.time), 0, distance);
            current.lastX = event.clientX; current.time = now;
        }}
        onPointerUp={event => { onPointerUp?.(event); if (gesture.current.id === event.pointerId) release(); }}
        onPointerCancel={event => { onPointerCancel?.(event); release(true); }}
        onPointerLeave={event => { onPointerLeave?.(event); if (gesture.current.id === event.pointerId) release(true); }}
        onKeyDown={event => {
            onKeyDown?.(event);
            if (event.key === 'Escape') { release(true); return; }
            if (event.repeat && [' ', 'Enter'].includes(event.key)) { event.preventDefault(); return; }
            if (event.defaultPrevented || event.repeat || ![' ', 'Enter'].includes(event.key) || event.currentTarget.matches(':disabled')) return;
            keyboardClick.current = true;
            gesture.current.cancelled = false; gesture.current.key = event.key;
            lens.current?.style.setProperty('--glass-light-x', '50%'); lens.current?.style.setProperty('--glass-light-y', '50%');
            motion.press();
        }}
        onKeyUp={event => { onKeyUp?.(event); if (gesture.current.key === event.key) release(); }}
        onBlur={event => { onBlur?.(event); keyboardClick.current = false; release(true); }}
        onClick={event => {
            const keyboard = keyboardClick.current; keyboardClick.current = false;
            if (gesture.current.cancelled && (event.detail > 0 || keyboard)) { gesture.current.cancelled = false; event.preventDefault(); event.stopPropagation(); return; }
            gesture.current.cancelled = false;
            if (event.detail === 0 && !keyboard && !event.currentTarget.matches(':disabled')) motion.pulse();
            onClick?.(event);
        }}>
        <GlassSurface as="span" surfaceRef={lens} aria-hidden="true" opticsEnabled={!subtle}
            className="liquid-button-material liquid-elastic-lens">
            <span className="liquid-raised-optics" /><span className="liquid-lens-sheen" />
        </GlassSurface>
        {children}
    </button>;
}
