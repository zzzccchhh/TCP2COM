/** One continuous light position per surface; entry, retargeting and return share the current frame. */
export function attachGlassLight(element: HTMLElement) {
    const host = element.classList.contains('liquid-button-material') ? element.parentElement! : element;
    const fine = matchMedia('(hover: hover) and (pointer: fine)');
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    let inside = false, contact = false, keyboard = false, frame = 0, last = 0, disposed = false;
    let pointer = { x: .5, y: 0 };
    let size = { width: element.offsetWidth, height: element.offsetHeight };
    function readHome() {
        const style = getComputedStyle(element);
        const coordinate = (value: string, size: number) => value.trim().endsWith('%') ? parseFloat(value) / 100 * size : parseFloat(value);
        return { x: coordinate(style.getPropertyValue('--glass-light-rest-x') || '50%', size.width),
            y: coordinate(style.getPropertyValue('--glass-light-rest-y') || '0%', size.height) };
    }
    let resting = readHome();
    function home() { return { ...resting }; }
    let current = home();
    function paint() {
        element.style.setProperty('--glass-light-x', `${current.x}px`);
        element.style.setProperty('--glass-light-y', `${current.y}px`);
    }
    function target() {
        if (keyboard) return { x: size.width / 2, y: size.height / 2 };
        return inside || contact ? { x: pointer.x * size.width, y: pointer.y * size.height } : home();
    }
    function tick(now: number) {
        frame = 0;
        if (disposed) return;
        const wanted = target();
        const amount = 1 - Math.exp(-Math.min(32, last ? now - last : 16) / (inside || contact || keyboard ? 55 : 75));
        last = now;
        current.x += (wanted.x - current.x) * amount; current.y += (wanted.y - current.y) * amount;
        if (Math.hypot(wanted.x - current.x, wanted.y - current.y) < .15) { current = wanted; last = 0; }
        else frame = requestAnimationFrame(tick);
        paint();
    }
    function wake() { if (!disposed && !frame) frame = requestAnimationFrame(tick); }
    function settle() {
        cancelAnimationFrame(frame); frame = 0; last = 0; inside = false; contact = false; keyboard = false;
        current = home(); paint();
    }
    function move(event: PointerEvent) {
        if (reduced.matches || host.matches(':disabled') || (!contact && (!fine.matches || event.pointerType === 'touch'))) return;
        const box = host.getBoundingClientRect();
        if (!box.width || !box.height) return;
        inside = fine.matches && event.pointerType !== 'touch'; keyboard = false;
        pointer = { x: Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)),
            y: Math.max(0, Math.min(1, (event.clientY - box.top) / box.height)) };
        wake();
    }
    function leave() { inside = false; contact = false; keyboard = false; wake(); }
    function down(event: PointerEvent) {
        if (host === element || host.matches(':disabled') || reduced.matches || event.button !== 0) return;
        contact = true; move(event);
    }
    function up() { contact = false; wake(); }
    function key(event: KeyboardEvent) {
        if (host !== element && !reduced.matches && [' ', 'Enter'].includes(event.key)) { keyboard = event.type === 'keydown'; wake(); }
    }
    function preference() { settle(); }
    function visibility() { if (document.hidden) settle(); }
    // Read layout only on resize, never between per-frame light writes.
    const observer = new ResizeObserver(() => {
        size = { width: element.offsetWidth, height: element.offsetHeight }; resting = readHome();
        if (reduced.matches) settle(); else wake();
    });
    observer.observe(element);
    host.addEventListener('pointerenter', move); host.addEventListener('pointermove', move);
    host.addEventListener('pointerleave', leave); host.addEventListener('pointercancel', leave);
    host.addEventListener('pointerdown', down); host.addEventListener('pointerup', up);
    host.addEventListener('keydown', key); host.addEventListener('keyup', key); host.addEventListener('blur', leave);
    window.addEventListener('blur', leave); document.addEventListener('visibilitychange', visibility);
    fine.addEventListener('change', preference); reduced.addEventListener('change', preference);
    paint();
    return () => {
        disposed = true; cancelAnimationFrame(frame); observer.disconnect();
        host.removeEventListener('pointerenter', move); host.removeEventListener('pointermove', move);
        host.removeEventListener('pointerleave', leave); host.removeEventListener('pointercancel', leave);
        host.removeEventListener('pointerdown', down); host.removeEventListener('pointerup', up);
        host.removeEventListener('keydown', key); host.removeEventListener('keyup', key); host.removeEventListener('blur', leave);
        window.removeEventListener('blur', leave); document.removeEventListener('visibilitychange', visibility);
        fine.removeEventListener('change', preference); reduced.removeEventListener('change', preference);
        element.style.removeProperty('--glass-light-x'); element.style.removeProperty('--glass-light-y');
    };
}
