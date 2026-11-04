/**
 * Adapted from xiaofangstudio/liquid-glass-web, MIT, commit
 * 3094add905e60ee5aeb45e806751beb94425dd73 (liquid-glass.js / showcase.html).
 * Original refraction engine: Copyright (c) 2025 Shu Ding, MIT.
 * See public/third-party-licenses.txt and vendor/README.md.
 */

const SVG_NS = 'http://www.w3.org/2000/svg';
let sequence = 0;

// Upstream's smoothStep and rounded-rectangle signed distance function.
function smoothStep(a: number, b: number, t: number) {
    t = Math.max(0, Math.min(1, (t - a) / (b - a)));
    return t * t * (3 - 2 * t);
}

function roundedRectSDF(x: number, y: number, width: number, height: number, radius: number) {
    const qx = Math.abs(x) - width + radius;
    const qy = Math.abs(y) - height + radius;
    return Math.min(Math.max(qx, qy), 0) + Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) - radius;
}

/** Attach upstream's SDF -> canvas -> SVG displacement pipeline to an existing surface. */
export function attachLiquidGlass(element: HTMLElement, onReady?: () => void): () => void {
    const fine = matchMedia('(hover: hover) and (pointer: fine)');
    const compact = matchMedia('(max-width: 760px)');
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    // CSS.supports alone also returns true in engines that cannot render SVG backdrop filters.
    const supported = /Chrome|Chromium|Edg\//.test(navigator.userAgent)
        && CSS.supports('backdrop-filter', 'url(#liquid-glass-probe)');
    const id = `liquid-glass-${++sequence}`;
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.classList.add('liquid-glass-defs');
    const control = element.matches('.liquid-indicator, .liquid-toggle-thumb, .liquid-check-mark');
    const elastic = element.classList.contains('liquid-elastic-lens');
    svg.dataset.glassKind = control ? 'control' : 'surface';
    svg.setAttribute('width', '0');
    svg.setAttribute('height', '0');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    const defs = document.createElementNS(SVG_NS, 'defs');
    const filter = document.createElementNS(SVG_NS, 'filter');
    filter.id = id;
    filter.setAttribute('filterUnits', 'userSpaceOnUse');
    filter.setAttribute('color-interpolation-filters', 'sRGB');
    filter.setAttribute('x', '0');
    filter.setAttribute('y', '0');
    const image = document.createElementNS(SVG_NS, 'feImage');
    image.setAttribute('result', `${id}-map`);
    image.setAttribute('preserveAspectRatio', 'none');
    const displacement = document.createElementNS(SVG_NS, 'feDisplacementMap');
    displacement.setAttribute('in', 'SourceGraphic');
    displacement.setAttribute('in2', `${id}-map`);
    displacement.setAttribute('xChannelSelector', 'R');
    displacement.setAttribute('yChannelSelector', 'G');
    filter.append(image, displacement);
    defs.append(filter);
    // Optical states are prepared once. Touch/shape frames only crossfade local layers.
    const raised = elastic ? filter.cloneNode(true) as SVGFilterElement : null;
    if (raised) { raised.id = `${id}-raised`; defs.append(raised); }
    const flow = elastic ? filter.cloneNode(true) as SVGFilterElement : null;
    if (flow) { flow.id = `${id}-flow`; defs.append(flow); }
    svg.append(defs);
    const canvas = document.createElement('canvas');
    const context = supported ? canvas.getContext('2d') : null;
    let frame = 0;
    let disposed = false;
    let revision = 0;
    let lastSize = '';
    let pointerFrame = 0;
    let pointerX = '50%';
    let pointerY = '0%';

    function reset() {
        revision++;
        svg.remove();
        element.style.removeProperty('--glass-refraction');
        element.style.removeProperty('--glass-refraction-raised');
        element.style.removeProperty('--glass-refraction-flow');
        element.dataset.glass = 'frosted';
        lastSize = '';
    }

    function update() {
        frame = 0;
        if (disposed) return;
        // Content heights may tween, while the large background keeps its existing map.
        // The final geometry is sampled once after the local slide has finished.
        if (!control && element.querySelector('[data-sliding]')) return;
        // Small moving controls retain refraction on touch screens; large panels use the lighter fallback.
        // Touch menu highlights are static glass; avoid allocating global SVG resources for each popup.
        if (!context || (!control && (compact.matches || !fine.matches))
            || (!fine.matches && element.classList.contains('picker-highlight'))) { reset(); onReady?.(); return; }
        const width = element.offsetWidth;
        const height = element.offsetHeight;
        if (width < 2 || height < 2) { reset(); return; }
        // The moving capsule deforms locally; its optical map is allocated only for its resting size.
        const radius = elastic ? Math.min(width, height) / 2
            : Math.min(parseFloat(getComputedStyle(element).borderTopLeftRadius) || 20, width / 2, height / 2);
        const key = `${width}:${height}:${radius}`;
        if (key === lastSize) return;
        try {
            // Bound CPU and memory cost even on large/high-DPI panels. The filter stays in CSS pixels.
            const ratio = Math.min(elastic ? 1.55 : 1, 224 / Math.max(width, height));
            const w = Math.max(2, Math.round(width * ratio));
            const h = Math.max(2, Math.round(height * ratio));
            canvas.width = w;
            canvas.height = h;
            const data = context.createImageData(w, h);
            const raw = new Float32Array(w * h * 2);
            const edge = Math.min(22, Math.min(width, height) * (control ? .24 : .22));
            let extent = 0;
            for (let i = 0; i < w * h; i++) {
                const x = ((i % w) + .5) / w * width - width / 2;
                const y = (Math.floor(i / w) + .5) / h * height - height / 2;
                const distance = roundedRectSDF(x, y, width / 2, height / 2, radius);
                const field = smoothStep(-edge, 0, distance);
                // Small lenses bend perpendicular to their rim, including the long, flat sides.
                // A radial vector makes those sides almost horizontal on a wide segmented control.
                const nx = control ? roundedRectSDF(x + .5, y, width / 2, height / 2, radius)
                    - roundedRectSDF(x - .5, y, width / 2, height / 2, radius) : x;
                const ny = control ? roundedRectSDF(x, y + .5, width / 2, height / 2, radius)
                    - roundedRectSDF(x, y - .5, width / 2, height / 2, radius) : y;
                const strength = elastic ? Math.min(8, height * .19) : control ? Math.min(6, height * .16) : 7;
                const bend = smoothStep(0, 1, field) * strength / Math.max(.001, Math.hypot(nx, ny));
                const centre = elastic ? (1 - smoothStep(-edge * 1.8, -edge * .3, distance)) * .025 : 0;
                const dx = -nx * bend - x * centre;
                const dy = -ny * bend - y * centre;
                raw[i * 2] = dx;
                raw[i * 2 + 1] = dy;
                extent = Math.max(extent, Math.abs(dx), Math.abs(dy));
            }
            // Map signed displacement to [0, 1]; guard flat/tiny surfaces against division by zero.
            extent = Math.max(.001, extent);
            for (let i = 0; i < w * h; i++) {
                data.data[i * 4] = (raw[i * 2] / (2 * extent) + .5) * 255;
                data.data[i * 4 + 1] = (raw[i * 2 + 1] / (2 * extent) + .5) * 255;
                data.data[i * 4 + 2] = 128;
                data.data[i * 4 + 3] = 255;
            }
            context.putImageData(data, 0, 0);
            for (const node of [filter, image]) {
                node.setAttribute('width', String(width));
                node.setAttribute('height', String(height));
            }
            const map = canvas.toDataURL();
            image.setAttribute('href', map);
            displacement.setAttribute('scale', String(extent * 2));
            if (raised) {
                raised.setAttribute('width', String(width)); raised.setAttribute('height', String(height));
                raised.querySelector('feImage')!.setAttribute('width', String(width));
                raised.querySelector('feImage')!.setAttribute('height', String(height));
                raised.querySelector('feImage')!.setAttribute('href', map);
                raised.querySelector('feDisplacementMap')!.setAttribute('scale', String(extent * 3.1));
                element.style.setProperty('--glass-refraction-raised', `url("#${id}-raised") blur(.08px) saturate(1.4)`);
            }
            if (flow) {
                flow.setAttribute('width', String(width)); flow.setAttribute('height', String(height));
                flow.querySelector('feImage')!.setAttribute('width', String(width));
                flow.querySelector('feImage')!.setAttribute('height', String(height));
                flow.querySelector('feImage')!.setAttribute('href', map);
                flow.querySelector('feDisplacementMap')!.setAttribute('scale', String(extent * 4));
                element.style.setProperty('--glass-refraction-flow', `url("#${id}-flow") blur(.08px) saturate(1.4)`);
            }
            if (!svg.isConnected) document.body.append(svg);
            element.style.setProperty('--glass-refraction', `url("#${id}") blur(${control ? .15 : 12}px) saturate(1.35)`);
            element.dataset.glass = 'refractive';
            lastSize = key;
            if (onReady) {
                // Menus stay hidden until their final-size bitmap is decoded, including on first open.
                const current = ++revision;
                const bitmap = new Image();
                bitmap.src = map;
                bitmap.decode().then(() => {
                    if (!disposed && current === revision) onReady();
                }).catch(() => {
                    if (!disposed && current === revision) { reset(); onReady(); }
                });
            }
        } catch {
            // Canvas restrictions or unavailable resources must never prevent using the workspace.
            reset();
            onReady?.();
        }
    }

    function schedule() {
        if (!control && element.querySelector('[data-sliding]')) return;
        if (!disposed && !frame) frame = requestAnimationFrame(update);
    }
    function transitionEnd(event: TransitionEvent) {
        if (event.target === element && event.propertyName === 'border-radius') schedule();
    }

    function move(event: PointerEvent) {
        if (!fine.matches || reduced.matches) return;
        const rect = element.getBoundingClientRect();
        pointerX = `${event.clientX - rect.left}px`;
        pointerY = `${event.clientY - rect.top}px`;
        if (!pointerFrame) pointerFrame = requestAnimationFrame(() => {
            pointerFrame = 0;
            element.style.setProperty('--glass-light-x', pointerX);
            element.style.setProperty('--glass-light-y', pointerY);
        });
    }

    function leave() {
        cancelAnimationFrame(pointerFrame);
        pointerFrame = 0;
        element.style.removeProperty('--glass-light-x');
        element.style.removeProperty('--glass-light-y');
    }

    function preferenceChanged() { leave(); schedule(); }
    const observer = new ResizeObserver(schedule);
    observer.observe(element);
    compact.addEventListener('change', preferenceChanged);
    fine.addEventListener('change', preferenceChanged);
    reduced.addEventListener('change', preferenceChanged);
    element.addEventListener('pointermove', move);
    element.addEventListener('pointerleave', leave);
    element.addEventListener('transitionend', transitionEnd);
    element.addEventListener('content-slide-end', schedule);
    // Hidden WebView2 windows suspend animation frames. Initialize once even before showing.
    update();

    return () => {
        disposed = true;
        cancelAnimationFrame(frame);
        leave();
        observer.disconnect();
        compact.removeEventListener('change', preferenceChanged);
        fine.removeEventListener('change', preferenceChanged);
        reduced.removeEventListener('change', preferenceChanged);
        element.removeEventListener('pointermove', move);
        element.removeEventListener('pointerleave', leave);
        element.removeEventListener('transitionend', transitionEnd);
        element.removeEventListener('content-slide-end', schedule);
        reset();
        delete element.dataset.glass;
    };
}
