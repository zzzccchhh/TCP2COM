import { useLayoutEffect, useRef } from 'react';
import type { RefObject } from 'react';

export type SegmentRect = { left: number; top: number; width: number; height: number };

/** Allocate optics at the destination once; interpolate the visible box with a FLIP transform. */
export function useSegmentGeometry(root: RefObject<HTMLElement | null>, anchor: RefObject<HTMLDivElement | null>, target: SegmentRect, direct: boolean) {
    const animation = useRef<Animation | null>(null);

    function read(): SegmentRect {
        const node = anchor.current, container = root.current;
        if (!node || !container) return target;
        const box = node.getBoundingClientRect(), bounds = container.getBoundingClientRect();
        return { left: box.left - bounds.left - container.clientLeft, top: box.top - bounds.top - container.clientTop,
            width: box.width, height: box.height };
    }

    useLayoutEffect(() => {
        const node = anchor.current;
        if (!node || target.width < 1 || target.height < 1) return;
        // Read before changing the allocation, including any interrupted animation's current frame.
        const from = read();
        animation.current?.cancel();
        const destination = `translate3d(${target.left}px, ${target.top}px, 0)`;
        node.style.width = `${target.width}px`; node.style.height = `${target.height}px`;
        node.style.transform = destination;
        if (direct || !root.current?.dataset.positioned || from.width < 1 || from.height < 1
            || document.hidden || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
        if (Math.abs(from.left - target.left) + Math.abs(from.top - target.top)
            + Math.abs(from.width - target.width) + Math.abs(from.height - target.height) < .1) return;
        animation.current = node.animate([
            { transform: `translate3d(${from.left}px, ${from.top}px, 0) scale(${from.width / target.width}, ${from.height / target.height})` },
            { transform: destination },
        ], { duration: 280, easing: 'cubic-bezier(.22, 1, .36, 1)' });
    }, [target, direct]);

    useLayoutEffect(() => {
        const reduced = matchMedia('(prefers-reduced-motion: reduce)');
        const finish = () => { animation.current?.cancel(); animation.current = null; };
        reduced.addEventListener('change', finish);
        return () => { reduced.removeEventListener('change', finish); finish(); };
    }, []);
    return read;
}
