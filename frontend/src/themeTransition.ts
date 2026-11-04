const root = document.documentElement;
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const registered = new Set<string>();
let stop: (() => void) | undefined;

function palette() {
    const style = getComputedStyle(root);
    return Object.fromEntries([...style].filter(name => name.startsWith('--') && CSS.supports('color', style.getPropertyValue(name).trim()))
        .map(name => [name, style.getPropertyValue(name).trim()]));
}

/** Animate the shared palette once, so portals and gradient materials share the same clock. */
export function transitionTheme(commit: () => void) {
    if (reduced.matches || document.hidden || matchMedia('(forced-colors: active)').matches) {
        stop?.(); commit(); return;
    }
    const before = palette(), background = getComputedStyle(root).backgroundColor;
    stop?.();
    root.dataset.themeTransition = 'true';
    commit();
    const after = palette(), finalBackground = getComputedStyle(root).backgroundColor;
    let animation: Animation | undefined, frame = 0;
    const previous = new Map<string, string>();
    const finish = () => {
        if (stop !== finish) return;
        stop = undefined; cancelAnimationFrame(frame); animation?.cancel();
        previous.forEach((value, name) => { if (value) root.style.setProperty(name, value); else root.style.removeProperty(name); });
        delete root.dataset.themeTransition;
    };
    stop = finish;
    const names = Object.keys(before).filter(name => name in after);
    if (CSS.registerProperty) {
        names.forEach(name => {
            if (!registered.has(name)) {
                try { CSS.registerProperty({ name, syntax: '<color>', inherits: true, initialValue: 'transparent' }); } catch { /* Already registered by the host. */ }
                registered.add(name);
            }
        });
        animation = root.animate([{ ...before, backgroundColor: background }, { ...after, backgroundColor: finalBackground }],
            { duration: 300, easing: 'cubic-bezier(.4, 0, .2, 1)' });
        animation.finished.then(finish).catch(() => {});
    } else {
        animation = root.animate([{ backgroundColor: background }, { backgroundColor: finalBackground }], { duration: 300 });
        // Older engines still get a continuous palette; only the small root colour table changes.
        const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
        const context = canvas.getContext('2d')!;
        const rgba = (value: string) => {
            context.clearRect(0, 0, 1, 1); context.fillStyle = value; context.fillRect(0, 0, 1, 1);
            return [...context.getImageData(0, 0, 1, 1).data];
        };
        const colours = names.map(name => ({ name, from: rgba(before[name]), to: rgba(after[name]) }));
        names.forEach(name => previous.set(name, root.style.getPropertyValue(name)));
        const start = performance.now();
        function tick(now: number) {
            const time = Math.max(0, Math.min(1, (now - start) / 300)), p = time * time * (3 - 2 * time);
            colours.forEach(({ name, from, to }) => {
                const c = from.map((value, index) => value + (to[index] - value) * p);
                root.style.setProperty(name, `rgba(${c[0]},${c[1]},${c[2]},${c[3] / 255})`);
            });
            if (time < 1) frame = requestAnimationFrame(tick); else finish();
        }
        tick(start);
    }
}

reduced.addEventListener('change', () => { if (reduced.matches) stop?.(); });
document.addEventListener('visibilitychange', () => { if (document.hidden) stop?.(); });
