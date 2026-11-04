import { useLayoutEffect, useRef } from 'react';
import type { RefObject } from 'react';

/** One numeric progress paints both thumb position and track colour, even during reversals. */
export function useToggleProgress(track: RefObject<HTMLElement | null>, target: number, immediate: boolean) {
    const value = useRef(target);
    const initialized = useRef(false);
    useLayoutEffect(() => {
        const reduced = matchMedia('(prefers-reduced-motion: reduce)');
        let frame = 0;
        function paint(next: number) { value.current = next; track.current?.style.setProperty('--toggle-progress', String(next)); }
        function finish() { if (reduced.matches) { cancelAnimationFrame(frame); paint(target); } }
        if (!initialized.current || immediate || reduced.matches) paint(target);
        else {
            const from = value.current, start = performance.now();
            function tick(now: number) {
                const time = Math.max(0, Math.min(1, (now - start) / 240));
                paint(from + (target - from) * (1 - (1 - time) ** 3));
                if (time < 1) frame = requestAnimationFrame(tick);
            }
            frame = requestAnimationFrame(tick);
        }
        initialized.current = true;
        reduced.addEventListener('change', finish);
        return () => { cancelAnimationFrame(frame); reduced.removeEventListener('change', finish); };
    }, [track, target, immediate]);
    return value;
}
