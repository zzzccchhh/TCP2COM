# Liquid Glass Web

`liquid-glass-web.ts` adapts the refraction pipeline from
[xiaofangstudio/liquid-glass-web](https://github.com/xiaofangstudio/liquid-glass-web),
fixed at commit `3094add905e60ee5aeb45e806751beb94425dd73`.
Sources: `liquid-glass.js` and `showcase.html`. The upstream README declares MIT;
its original refraction engine is by [Shu Ding](https://github.com/shuding/liquid-glass).
License notices ship with the app in `public/third-party-licenses.txt`.

The integration uses the upstream smoothstep, rounded rectangle SDF and
canvas/SVG displacement pipeline. It replaces demo auto-start and floating drag
behaviour with a React lifecycle. Changes include aspect-aware geometry, bounded
raster resolution, signed displacement normalization, a named SVG map result,
size caching, explicit cleanup and a frosted fallback for large touch surfaces
or unsupported engines. Small moving control lenses keep their bounded raster
and refraction on supported touch Chromium. Optical filters only affect
decorative backgrounds. CSS provides press/rebound and light effects.

`LiquidSegments` uses one shared lens per group, pointer capture, local drag
previews and a single change on release. `GlassPicker` renders a portal listbox
with the native form value and focus, and editable suggestions for COM/baud.
`usePresence` retains overlays through reversible entrance/exit transitions.
These controls use the existing engine and CSS; no animation dependency is added.

No CDN, remote image or runtime download is required. Update this commit and
recheck the browser/WebView2 rendering when changing the upstream implementation.
