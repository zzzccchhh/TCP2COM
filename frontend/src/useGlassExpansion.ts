import { useLayoutEffect, useRef } from 'react';
import type { RefObject } from 'react';

export type ExpansionOrigin = { x: number; upwards: boolean };
const clamp = (value: number, low = 0, high = 1) => Math.max(low, Math.min(high, value));
const mix = (a: number, b: number, amount: number) => a + (b - a) * amount;

/** The card reveals directly from its nearest edge. The cached optical sheet and native-size text stay at their final allocation. */
export function useGlassExpansion(element: RefObject<HTMLElement | null>, origin: RefObject<ExpansionOrigin>,
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
        const p = progress.current, { x, upwards } = origin.current;
        const width = node.clientWidth, height = node.clientHeight;
        if (!width || !height) return;
        // Reveal only inside the final card. No seed, lift, or second glass under the trigger.
        const edgeX = clamp(x, 0, width);
        const w = width * p, h = height * p;
        const left = edgeX * (1 - p), top = upwards ? height - h : 0;
        const radius = Math.min(26, w / 2, h / 2);
        const clip = `${top / height * 100}% ${(width - left - w) / width * 100}% ${(height - top - h) / height * 100}% ${left / width * 100}%`;
        const content = node.querySelector<HTMLElement>('.glass-expansion-content');
        const bounds = node.getBoundingClientRect(), text = content?.getBoundingClientRect();
        const baseX = bounds.left + node.clientLeft, baseY = bounds.top + node.clientTop;
        const textClip = text ? `inset(${Math.max(0, baseY + top - text.top)}px ${Math.max(0, text.right - baseX - left - w)}px ${Math.max(0, text.bottom - baseY - top - h)}px ${Math.max(0, baseX + left - text.left)}px)` : 'inset(0)';
        const values: Record<string, string> = {
            '--expansion-progress': String(p), '--expansion-shape-x': `${left}px`, '--expansion-shape-y': `${top}px`,
            '--expansion-shape-width': `${w}px`, '--expansion-shape-height': `${h}px`, '--expansion-shape-radius': `${radius}px`,
            '--expansion-sheet-clip': `inset(${clip} round ${radius}px)`, '--expansion-content-clip': p === 1 ? 'inset(0)' : textClip,
            '--expansion-sheet-opacity': String(p),
            '--expansion-text-opacity': String(clamp(p / .8)),
        };
        Object.entries(values).forEach(([key, value]) => node.style.setProperty(key, value));
        const glass = node.matches('.glass-surface') ? node : node.querySelector('.picker-window');
        glass?.dispatchEvent(new CustomEvent('glass-expansion-frame', { detail: { x: left, y: top, width: w, height: h, progress: p } }));
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
        const clock = element.current!.querySelector<HTMLElement>('.glass-expansion-clock')!;
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
