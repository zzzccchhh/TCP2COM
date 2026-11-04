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
edge normal with a low-blur rim; elastic controls add a small central
magnification while preserving a transparent interior.
Segment labels paint below the shared lens so its backdrop includes actual
text detail, while panel text and form inputs stay outside their optical layers.
`useLiquidLens` supplies the shared analytic damped springs; CSS paints the shape
and light without moving the selection anchor. Shared typography and control metrics
use 14px body / 42px primary controls on desktop and 16px / 48px on touch.
Responsive layouts prioritize connection and device actions without page scaling.

`LiquidSegments` uses one shared lens per group, pointer capture after a primary
press, separate click candidates (5px mouse / 8px touch threshold), local drag
previews and a single change on release. A click targets the option rectangle
directly; a drag retains its grab offset. An outer anchor owns monotonic position
snapping and an inner lens owns lift, small acceleration-driven edge flex and
damped shape recovery, keeping the body full and rounded without skew or pointed tails. The resting-size SDF
bitmap is reused throughout dragging; shape updates only touch local CSS variables.
Small elastic lenses prepare normal and raised filters sharing that bitmap at
initialization/resize, with bounded raster resolution sized for enlargement.
Resting elastic glass uses no displacement. A single raised refractive layer fades
with lift; its separate sheen has no backdrop filter, avoiding repeated lensing. No SVG attributes or maps change per gesture
frame. A short press retains its raised target for 125ms even after commit, then
settles through the same spring as a long press. Press uses lower damping, while
release uses near-critical resistance for lift and stronger resistance for bend,
preventing visible undershoot without resetting material position or velocity.
Regrabs preserve spring velocity;
shape recovery is independent of one-time value commits and position snapping.
Segmented lenses use .24/.38 width/height lift gains, compact log controls use
.18/.32, and switch thumbs use .42/.38, all with bounded overrun. Disabled state,
reduced motion, unmount and cancellation terminate or settle the local material.
`useSegmentGeometry` allocates the destination size once and uses a FLIP transform
to interpolate the entire visible box over 280ms, including unequal option widths.
Interrupted travel reads the current visible rectangle before replacing its
animation; direct dragging preserves the grab and release smoothly changes size.
Optics therefore see discrete destination allocations rather than per-frame
width changes. Live label/counter resizing does not cancel a held selection.
Initial layout appears in place before travel is enabled.
Hover does not move
the lens or the picker highlight. `GlassPicker` renders a portal listbox
with the native form value and focus, and editable suggestions for COM/baud.
`usePresence` retains overlays through reversible entrance/exit transitions,
accepts a readiness gate for first entrance, and immediately resumes a retained
overlay when reopening during exit.
These controls use the existing engine and CSS; no animation dependency is added.

`LiquidButton` keeps a native button and its original children, callbacks, form
behaviour and refs. Only an aria-hidden decorative surface uses `useLiquidLens`;
labels/icons paint above that surface and never scale or refract. Primary,
secondary, circular icon and lightweight text variants share contact lighting,
rounded lift and damped recovery. Pointer/keyboard cancellation and vertical touch
scrolling release the material without invoking the action. Button optical maps
are cached at rest, and hidden optical layers do not sample the page until a
press. Text-only entries omit SVG optics. The material inherits its button's
radius to avoid mixing capsule corner proportions with a segment's elastic rim.

`LiquidToggle` retains a native labelled checkbox (switch role for boolean
settings), with pointer capture and a single commit on release. A separate
position anchor leaves the thumb free to use the same lift/droplet springs as
segmented controls, including keyboard pulses and touch cancellation. Confirmation
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

The material palette now uses shared CSS variables with original warm light and
near-black purple dark appearances. The root theme covers body-level portals too.
Theme changes retain optical instances and geometry, recolouring the tint,
edge highlights, shadows, labels and semantic statuses without a page filter.
Dark glass uses pale lavender rim light and restrained local glow, with black
drop shadows kept separate. Light mode keeps its original warm palette.
A blocking local bootstrap applies the preference before React/CSS first paint.
Browser preferences stay origin-local; native startup supplies a validated
fragment and matching window background, while a desktop-only API saves the
appearance separately from device state. Native writes are serialized, storage
errors leave the current appearance usable, and system events apply only when
selected. This does not enable persistence for the whole WebView profile.

`SlidingContent` animates only committed segment changes, never pointer previews.
It snapshots the outgoing DOM before React mutates it, copying form values and
scroll positions into a closed, inert shadow tree with stylesheet context. The
copy has no React handlers or effects and cannot duplicate live form queries.
Ancestor shells preserve contextual selectors without painted layout boxes;
outgoing optics use static material rather than duplicate global SVG resources.
Incoming and outgoing pages translate for 270ms with height interpolation, and
rapid reversals reuse the current visual position. Per-page scroll offsets are
restored; reduced motion and unmount immediately clean up animations and copies.
Large optical surfaces defer size-map updates during these transitions and
refresh on the bubbling completion event. Selected button focus uses the lens
rim instead of a second rectangular outline that touch Chromium could refract.

No CDN, remote image or runtime download is required. Update this commit and
recheck the browser/WebView2 rendering when changing the upstream implementation.

Phone config, diagnostics and remote reset use a full-width child page instead of the touch
drawer reveal. `useMobilePage` records a small namespaced history state entry
without changing the URL, restores the level on reload and handles back/forward.
Back first dismisses a picker or save confirmation; fast returns/reopens serialize
history traversal without adding duplicate entries. The retained home and child
page translate in opposite directions for 320ms, without scaling, clipping or a
blur shade. Large background optics stay static; small controls inside the child
page retain their existing optics. Layout mode is captured on entry so rotation
does not turn an open page into a drawer. A fixed body preserves home scroll;
visualViewport events size the child to keyboard/rotation changes, safe-area
padding protects its header/footer, and exit restores scroll and entry focus.
Reduced motion disables travel and releases an exiting page immediately. Wider
layouts and reset/help overlays keep their original presentation.

Elastic controls keep idle labels free of displacement. Held controls use one
refractive layer and a non-refractive sheen. Normal/raised states share a cached
SDF bitmap, with no per-gesture map edits. The rounded silhouette has small edge
flex from acceleration/material lag, no skew, no sustained tail and no vertical
compression. Raised switches use a wider capsule and 64/72px desktop/touch tracks
while retaining 22/26px travel. The motion is visually referenced to the switch
interaction and convex lenses in
[Apple's Liquid Glass introduction](https://developer.apple.com/videos/play/wwdc2025/219/).
`useToggleProgress` still supplies one bounded progress for thumb and green tint.

`themeTransition` registers the shared colour palette and animates it on the root
for 300ms; existing controls and body portals inherit the same timeline. A colour
transition duration override avoids cascaded colour lag without disabling geometry
or presence transitions. Reversal samples the currently displayed palette before
cancelling its predecessor. A small root-colour fallback supports older engines;
initial paint, unchanged visual themes and reduced motion apply directly. Native
appearance fragment updates preserve history state and keep back destinations in
sync with the current preference. Reset-page navigation retains the current
device endpoint and task state without adding reset requests.
