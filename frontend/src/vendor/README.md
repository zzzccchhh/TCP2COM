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
and refraction on supported touch Chromium. Small lenses use the rounded SDF's
edge normal with a narrow, low-blur rim; the flat centre stays transparent.
Segment labels paint below the shared lens so its backdrop includes actual
text detail, while panel text and form inputs stay outside their optical layers.
CSS provides press/rebound and light effects. Shared typography and control metrics
use 14px body / 42px primary controls on desktop and 16px / 48px on touch.
Responsive layouts prioritize connection and device actions without page scaling.

`LiquidSegments` uses one shared lens per group, pointer capture after a primary
press, separate click candidates (5px mouse / 8px touch threshold), local drag
previews and a single change on release. A click targets the option rectangle
directly; a drag retains its grab offset. An outer anchor owns monotonic position
snapping and an inner lens owns lift, velocity-driven asymmetric droplet radii,
area-preserving stretch, shear and damped shape recovery. The resting-size SDF
bitmap is reused throughout dragging; shape updates only touch local CSS variables.
The anchor takes its final size directly, avoiding per-frame map regeneration
during width transitions. Initial layout appears in place before travel is enabled.
Hover does not move
the lens or the picker highlight. `GlassPicker` renders a portal listbox
with the native form value and focus, and editable suggestions for COM/baud.
`usePresence` retains overlays through reversible entrance/exit transitions,
accepts a readiness gate for first entrance, and immediately resumes a retained
overlay when reopening during exit.
These controls use the existing engine and CSS; no animation dependency is added.

`LiquidToggle` retains a native labelled checkbox (switch role for boolean
settings), with pointer capture and a single commit on release. Confirmation
checkboxes keep checkbox semantics. `LiquidDisclosure` animates grid height while
keeping collapsed fields inert and preserving their values. Menus settle their
constrained width and position before attaching optics. Their readiness callback
waits for the displacement bitmap to decode; the initial highlight is laid out
without transitions. Opening and closing then animate the same optical sheet
with the same duration and easing, while text is revealed/clipped at full size.
The sampling surface stays fixed; its clip rectangle reveals the material.
The wrapper never scales or covers its trigger, and interrupted transitions
resume without a repeated text delay. Overlay ancestors stay opaque so
opacity does not create a temporary backdrop root during the morph; the backdrop
color, geometry and individual content layers provide the transition instead.

Touch layouts include tablets above the phone breakpoint. Large panes use a
lighter material, the modal shade has a fixed blur and fades as a sibling of
the dialog, and touch drawer reveals do not move a filtered surface. Touch menu
highlights avoid transient SVG allocations; draggable segmented lenses retain
refraction. `useExitBounds` freezes a closing rectangle, while the LAN popover
also retains its displayed content. Transition completion releases retained
nodes with a timeout fallback; reopening cancels cleanup. Focus returns without
scrolling and without stealing it from another dialog.

No CDN, remote image or runtime download is required. Update this commit and
recheck the browser/WebView2 rendering when changing the upstream implementation.
