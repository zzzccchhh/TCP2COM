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
export function attachLiquidGlass(element: HTMLElement): () => void {
    const fine = matchMedia('(hover: hover) and (pointer: fine)');
    const compact = matchMedia('(max-width: 760px)');
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    // CSS.supports alone also returns true in engines that cannot render SVG backdrop filters.
    const supported = /Chrome|Chromium|Edg\//.test(navigator.userAgent)
        && CSS.supports('backdrop-filter', 'url(#liquid-glass-probe)');
    const id = `liquid-glass-${++sequence}`;
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.classList.add('liquid-glass-defs');
    const control = element.classList.contains('liquid-indicator');
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
    svg.append(defs);
    const canvas = document.createElement('canvas');
    const context = supported ? canvas.getContext('2d') : null;
    let frame = 0;
    let disposed = false;
    let lastSize = '';
    let pointerFrame = 0;
    let pointerX = '50%';
    let pointerY = '0%';

    function reset() {
        svg.remove();
        element.style.removeProperty('--glass-refraction');
        element.dataset.glass = 'frosted';
        lastSize = '';
    }

    function update() {
        frame = 0;
        if (disposed) return;
        // Small moving controls retain refraction on touch screens; large panels use the lighter fallback.
        if (!context || (!control && (compact.matches || !fine.matches))) { reset(); return; }
        const width = element.offsetWidth;
        const height = element.offsetHeight;
        if (width < 2 || height < 2) { reset(); return; }
        const radius = Math.min(parseFloat(getComputedStyle(element).borderTopLeftRadius) || 20, width / 2, height / 2);
        const key = `${width}:${height}:${radius}`;
        if (key === lastSize) return;
        try {
            // Bound CPU and memory cost even on large/high-DPI panels. The filter stays in CSS pixels.
            const ratio = Math.min(1, 224 / Math.max(width, height));
            const w = Math.max(2, Math.round(width * ratio));
            const h = Math.max(2, Math.round(height * ratio));
            canvas.width = w;
            canvas.height = h;
            const data = context.createImageData(w, h);
            const raw = new Float32Array(w * h * 2);
            const edge = Math.min(22, Math.min(width, height) * .22);
            let extent = 0;
            for (let i = 0; i < w * h; i++) {
                const x = ((i % w) + .5) / w * width - width / 2;
                const y = (Math.floor(i / w) + .5) / h * height - height / 2;
                const distance = roundedRectSDF(x, y, width / 2, height / 2, radius);
                const field = smoothStep(-edge, 0, distance);
                // Keep upstream's flat centre and bent edge, using actual aspect ratio and pixel strength.
                const scale = 1 - smoothStep(0, 1, field) * 7 / Math.max(1, Math.hypot(x, y));
                const dx = x * scale - x;
                const dy = y * scale - y;
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
            image.setAttribute('href', canvas.toDataURL());
            displacement.setAttribute('scale', String(extent * 2));
            if (!svg.isConnected) document.body.append(svg);
            element.style.setProperty('--glass-refraction', `url("#${id}") blur(${control ? 1.5 : 12}px) saturate(1.35)`);
            element.dataset.glass = 'refractive';
            lastSize = key;
        } catch {
            // Canvas restrictions or unavailable resources must never prevent using the workspace.
            reset();
        }
    }

    function schedule() {
        if (!disposed && !frame) frame = requestAnimationFrame(update);
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
        reset();
        delete element.dataset.glass;
    };
}
