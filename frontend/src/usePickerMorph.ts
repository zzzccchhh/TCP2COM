import { useLayoutEffect, useRef } from 'react';
import type { RefObject } from 'react';

export type PickerOrigin = { x: number; y: number; size: number; upwards: boolean };
const clamp = (value: number, low = 0, high = 1) => Math.max(low, Math.min(high, value));
const mix = (a: number, b: number, amount: number) => a + (b - a) * amount;
const smooth = (value: number) => value * value * (3 - 2 * value);

/** Only the contour moves. The cached optical sheet and native-size text stay at their final allocation. */
export function usePickerMorph(element: RefObject<HTMLElement | null>, origin: RefObject<PickerOrigin>,
    present: boolean, entered: boolean, requested: boolean, layout: unknown, finishExit: () => void) {
    const progress = useRef(0);
    const driver = useRef<{ animation: Animation; from: number; to: number } | null>(null);
    const frame = useRef(0);
    const initialized = useRef(false);
    const desired = useRef(false);
    const complete = useRef(finishExit);
    complete.current = finishExit;

    function sample() {
        const current = driver.current;
        if (!current) return progress.current;
        const time = current.animation.effect?.getComputedTiming().progress ?? 0;
        return mix(current.from, current.to, Number(time));
    }
    function paint(amount: number) {
        const node = element.current;
        if (!node) return;
        progress.current = clamp(amount);
        const p = progress.current, { x, y, size, upwards } = origin.current;
        const width = node.offsetWidth, height = node.offsetHeight;
        if (!width || !height) return;
        const seed = Math.min(size, width, height);
        // A small contraction, a lift clear of the data, then a rounded expansion.
        const lift = smooth(clamp((p - .1) / .2));
        const growth = smooth(clamp((p - .3) / .7));
        const small = seed * (p < .1 ? mix(1, .88, p / .1) : mix(.88, 1, lift));
        const edgeX = clamp(x, seed / 2, width - seed / 2);
        const edgeY = upwards ? height - seed / 2 : seed / 2;
        const w = mix(small, width, growth), h = mix(small, height, growth);
        const left = mix(mix(x, edgeX, lift) - small / 2, 0, growth);
        const top = mix(mix(y, edgeY, lift) - small / 2, 0, growth);
        const radius = mix(small / 2, 26, growth);
        const clip = `${clamp(top / height) * 100}% ${clamp((width - left - w) / width) * 100}% ${clamp((height - top - h) / height) * 100}% ${clamp(left / width) * 100}%`;
        const values: Record<string, string> = {
            '--picker-progress': String(p), '--picker-shape-x': `${left}px`, '--picker-shape-y': `${top}px`,
            '--picker-shape-width': `${w}px`, '--picker-shape-height': `${h}px`, '--picker-shape-radius': `${radius}px`,
            '--picker-sheet-clip': `inset(${clip} round ${radius}px)`, '--picker-content-clip': `inset(${clip})`,
            '--picker-sheet-opacity': String(clamp((p - .2) / .8)),
            '--picker-text-opacity': String(clamp((p - .28) / .6)),
        };
        Object.entries(values).forEach(([key, value]) => node.style.setProperty(key, value));
    }
    function stop() {
        cancelAnimationFrame(frame.current); frame.current = 0;
        driver.current?.animation.cancel(); driver.current = null;
    }
    function settle() {
        stop(); paint(desired.current ? 1 : 0);
        if (!desired.current && initialized.current) complete.current();
    }
    useLayoutEffect(() => {
        if (!present) { stop(); progress.current = 0; initialized.current = false; return; }
        // Reverse the last painted frame, rather than advancing a closing timeline during React's entry gate.
        const current = progress.current;
        stop();
        desired.current = entered || (requested && initialized.current);
        const target = desired.current ? 1 : 0;
        paint(current);
        if (!desired.current && !initialized.current) return;
        if (desired.current) initialized.current = true;
        if (Math.abs(current - target) < .001 || document.hidden || matchMedia('(prefers-reduced-motion: reduce)').matches) {
            settle(); return;
        }
        const clock = element.current!.querySelector<HTMLElement>('.picker-motion-clock')!;
        // The zero-size clock supplies the browser's easing/timeline and participates in animation waits.
        const animation = clock.animate([{ opacity: 0 }, { opacity: 1 }], {
            duration: 380 * Math.abs(target - current), easing: 'cubic-bezier(.4, 0, .2, 1)', fill: 'both',
        });
        driver.current = { animation, from: current, to: target };
        function tick() { paint(sample()); if (driver.current?.animation === animation) frame.current = requestAnimationFrame(tick); }
        frame.current = requestAnimationFrame(tick);
        animation.finished.then(() => { if (driver.current?.animation === animation) settle(); }).catch(() => {});
    }, [present, entered, requested, layout]);

    useLayoutEffect(() => {
        const reduced = matchMedia('(prefers-reduced-motion: reduce)');
        const changed = () => { if (reduced.matches || document.hidden) settle(); };
        reduced.addEventListener('change', changed); document.addEventListener('visibilitychange', changed);
        return () => { reduced.removeEventListener('change', changed); document.removeEventListener('visibilitychange', changed); };
    }, [entered, requested]);
    useLayoutEffect(() => stop, []);
}
