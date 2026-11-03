import { useEffect, useMemo } from 'react';
import type { RefObject } from 'react';

type Spring = { value: number; velocity: number };
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

/** Exact damped-spring step: the feel does not depend on a 60/120 Hz display. */
function advance(spring: Spring, target: number, dt: number, stiffness: number, damping: number) {
    const decay = damping / 2;
    const frequency = Math.sqrt(stiffness - decay * decay);
    const offset = spring.value - target;
    const sin = Math.sin(frequency * dt), cos = Math.cos(frequency * dt), envelope = Math.exp(-decay * dt);
    spring.value = target + envelope * (offset * cos + (spring.velocity + decay * offset) / frequency * sin);
    spring.velocity = envelope * (spring.velocity * cos - (decay * spring.velocity + stiffness * offset) / frequency * sin);
}

/** A gesture may finish immediately; its lens keeps its own energy until it settles. */
export function useLiquidLens(element: RefObject<HTMLElement | null>) {
    const motion = useMemo(() => {
        const reduced = matchMedia('(prefers-reduced-motion: reduce)');
        const lift: Spring = { value: 0, velocity: 0 }, bend: Spring = { value: 0, velocity: 0 };
        let held = false, peakUntil = 0, inputTime = 0, inputVelocity = 0, edge = 0, frame = 0, last = 0;

        function paint() {
            const node = element.current;
            if (!node) return;
            node.style.setProperty('--liquid-lift', String(clamp(lift.value, -.22, 1.34)));
            node.style.setProperty('--liquid-bend', String(clamp(bend.value, -1.15, 1.15)));
            node.style.setProperty('--liquid-strain', String(Math.min(.22, Math.abs(bend.value) * .19 + edge * .06)));
            node.style.setProperty('--liquid-thickness', String(clamp(lift.value, 0, 1)));
            if (held || Math.abs(lift.value) + Math.abs(bend.value) > .002) node.dataset.lensActive = 'true';
            else delete node.dataset.lensActive;
        }
        function reset() {
            cancelAnimationFrame(frame); frame = 0;
            held = false; peakUntil = 0; inputVelocity = 0; edge = 0;
            lift.value = lift.velocity = bend.value = bend.velocity = 0;
            paint();
        }
        function tick(now: number) {
            frame = 0;
            if (reduced.matches) { reset(); return; }
            // A suspended tab should not replay a stale touch animation on return.
            if (now - last > 250 && !held) { reset(); return; }
            const dt = Math.min(.1, Math.max(.001, (now - last) / 1000)); last = now;
            const target = held || now < peakUntil ? 1 : 0;
            const age = Math.exp(-(now - inputTime) / 85);
            const bendTarget = held ? clamp(inputVelocity / 1.1, -1, 1) * age : 0;
            // Keep the lively press, then settle close to critical damping on release.
            // Preserve position/velocity; changing the resistance avoids a visible snap.
            advance(lift, target, dt, 850, target ? 22 : 52);
            advance(bend, bendTarget, dt, 360, held ? 16 : 32);
            if (!held) edge *= Math.exp(-dt * 18);
            const energy = Math.abs(lift.value - target) + Math.abs(lift.velocity) + Math.abs(bend.value) + Math.abs(bend.velocity) + Math.abs(bendTarget) + edge;
            if (!held && !target && energy < .004) { reset(); return; }
            paint();
            // Held, settled lenses do not spend frames until the next pointer sample/release.
            if (energy > .004 || (!held && target)) frame = requestAnimationFrame(tick);
        }
        function wake() {
            if (reduced.matches) { reset(); return; }
            if (!frame) { last = performance.now(); frame = requestAnimationFrame(tick); }
        }
        function press() {
            held = true; peakUntil = performance.now() + 125;
            inputVelocity = 0; edge = 0; wake();
        }
        function release(cancelled = false) {
            held = false;
            if (cancelled) peakUntil = 0;
            wake();
        }
        return {
            press, release, reset,
            pulse() { press(); release(); },
            move(velocity: number, overscroll = 0) {
                inputTime = performance.now(); inputVelocity = velocity; edge = clamp(overscroll, 0, 1); wake();
            },
            subscribe() { reduced.addEventListener('change', reset); return () => { reduced.removeEventListener('change', reset); reset(); }; },
        };
    }, [element]);
    useEffect(() => motion.subscribe(), [motion]);
    return motion;
}
