# AlphaLink CAM: touch and multi-touch design

Status: **design draft 1** (2026-10-13). No code yet. Companion to `app/DESIGN.md` (section 6, user interface).

## 1. Why touch, and where it matters

The app runs on two kinds of screen:

| Where | Typical hardware | What the user does there |
|---|---|---|
| Desk / office | laptop, mouse, touchpad, maybe a 2-in-1 | draws parts, sets up operations, posts PRGs |
| Next to the lathe | **Microsoft Surface Pro on Ubuntu 26.04 / 26.10** (section 1.1), on an arm or stand, with fingers plus Type Cover and mouse, coolant nearby | checks the part and toolpaths, scrubs the simulation, reads the program, makes small edits, sends to the control |

So the goal is: **touch-first for review, simulation and sending; fully touch-capable (but not touch-optimised at the
cost of precision) for drawing.** Keyboard command entry (`40,-25` Enter, as in ALPHA-T) stays the fastest path and is
never replaced; touch adds to it.

Design rules that follow from the shop floor:

- **T1. Everything works with one finger.** Resistive panels, many industrial PCAP panels in "mouse mode", and gloves
  give single-touch only, often delivered to the app as plain pointer events. Every multi-touch gesture has an on-screen
  equivalent (zoom buttons, fit button, a keypad).
- **T2. No precision from the finger.** A fingertip covers 7-10 mm of screen; the CAM needs 0.001 mm. Exact values come
  from snapping and from typed numbers, never from where the finger landed.
- **T3. No destructive action on a single accidental touch.** Coolant drops and sleeves make phantom touches on
  capacitive screens. Delete is undoable, sending needs a hold plus a confirmation (section 7).
- **T4. Leave the system gestures alone.** GNOME Shell owns screen-edge swipes and three-or-more-finger gestures
  (overview, workspaces, on-screen keyboard). The app only uses one- and two-finger gestures, away from the edges.
- **T5. Rotation means nothing on a lathe drawing.** X (diameter) is always up, Z always horizontal. Two-finger rotate is
  ignored on purpose.

### 1.1 Target device: Surface Pro on Ubuntu

The chosen device is a Microsoft Surface Pro running Ubuntu 26.04 LTS or 26.10. What that means for the app and the setup
(source: the linux-surface project's
[supported devices list](https://github.com/linux-surface/linux-surface/wiki/Supported-Devices-and-Features)):

| Topic | Consequence |
|---|---|
| **Model** | Intel models only: Surface Pro 7, 7+, 8, 9 (Intel), 10 and the Intel Pro 11. The ARM models (Pro X, Pro 9 5G/SQ3, Pro 11 Snapdragon) are listed as unsupported. Pro 10 "with 5G" has no Type Cover or sensor support. |
| **Kernel** | Touch and pen need the **linux-surface kernel** plus the `iptsd` touch daemon (Pro 8 and later say "requires linux-surface kernel"). Stock Ubuntu gives no touch. Install from the linux-surface apt repository and enrol its Secure Boot key (MOK). Prefer **26.04 LTS**: the linux-surface packages may lag a fresh 26.10 release. |
| **Session** | Ubuntu has no GNOME X11 session any more, so it is GNOME on Wayland. That is the best case for GTK4 touch and pen. |
| **Screen** | 13" 2880x1920 (Pro 8 and later), about 267 ppi, run at 200% scale, so about 1440x960 logical px. At 200%, 48 logical px is about 9 mm, a good finger target; the 24 px hit radius in section 3 is about a 9 mm wide circle. |
| **Touch type** | Capacitive multi-touch, no glove mode: bare fingers. Gloves are out, so the rule "everything works with one finger" (T1) stays for robustness but no longer for gloves. |
| **Inputs in use** | Fingers, the Type Cover keyboard and a mouse, often in the same session: touch for navigating and reviewing, mouse and keyboard for precise drawing and typed values. A pen is unlikely; it works if present (section 5.4) but nothing depends on it. |
| **Orientation** | Auto-rotation (iio-sensor-proxy). Landscape with the Type Cover is the main layout; portrait must still work (an `AdwBreakpoint` on aspect ratio: canvas on top, ops and program in a bottom sheet). |
| **Keyboard and mouse** | With the Type Cover attached, ALPHA-T style typed entry works and all shortcuts apply. Detached, GNOME's on-screen keyboard appears for text fields; numeric fields use our keypad (section 6). Because users switch between mouse and finger constantly, the Automatic input layout (section 3) must switch quickly and without reflowing the canvas: only control sizes change, never the view. |
| **Serial** | USB-C only on Pro 8 and later (two ports): the PL2303 adapter and a wired mouse together need a USB-C hub or dock, or use a Bluetooth mouse. The `dialout` group rule from DESIGN.md section 7 is unchanged. |
| **Power** | It is a tablet that suspends when idle. A transfer must inhibit suspend and idle (`Gtk.Application.inhibit` with `SUSPEND` and `IDLE`) for its duration, or the link drops mid-program. |
| **Shop floor** | Not rugged: a rugged case, a VESA or clamp mount on an arm away from the coolant, and a screen protector. Coolant drops on a capacitive screen make phantom touches, which is why touch lock (7.4) matters. |

## 2. What GTK4 and libadwaita give us

### 2.1 GTK4 event controllers (all usable from PyGObject)

| Controller | Touchscreen | Touchpad | Mouse / pen | Use in AlphaLink |
|---|---|---|---|---|
| `GtkGestureClick` | tap, double tap (`n_press`) | tap-to-click | click | select, double tap = zoom to fit |
| `GtkGestureDrag` | one-finger drag | click-drag | drag | pan, move a handle, draw |
| `GtkGestureLongPress` | press and hold (`delay-factor`) | - | hold | context menu (the "right click" of touch) |
| `GtkGestureZoom` | two-finger pinch (`scale-changed`, bounding-box centre) | pinch | - | zoom about the fingers, and two-finger pan from the centre's movement |
| `GtkGestureRotate` | two-finger rotate | rotate | - | not used (T5) |
| `GtkGestureSwipe` | release velocity | - | release velocity | kinetic pan after a fling |
| `GtkGesturePan` | orientation-locked drag | - | drag | simulation scrub strip |
| `GtkGestureStylus` | - | - | pen pressure, tilt, hover (`proximity`) | pen hover shows the snap preview like a mouse |
| `GtkEventControllerScroll` (`BOTH_AXES`, `KINETIC`) | - | two-finger scroll | wheel | pan; Ctrl+wheel zoom |
| `GtkEventControllerLegacy` | raw touch begin/update/end/cancel | raw | raw | fallback if the stock gestures fight each other (section 5.3) |

Useful details:

- `controller.get_current_event_device().get_source()` returns `Gdk.InputSource.TOUCHSCREEN`, `PEN`, `MOUSE` or
  `TOUCHPAD`. This drives the hit radius and the touch layout (section 3).
- `Gdk.Event.get_pointer_emulated()` tells a real mouse event from one GDK made up from a touch.
- Gestures compete through **event sequences**. A gesture that recognises its input sets `Gtk.EventSequenceState.CLAIMED`
  and the others are cancelled; `Gtk.Gesture.group()` lets two gestures share sequences.
- Touchscreen and touchpad gestures are first-class on **Wayland**. On X11 they need XInput 2.2+ (touch) and 2.4
  (touchpad pinch). Target the GNOME Wayland session; X11 is best-effort.
- Flatpak needs no extra permission for touch or pen.

### 2.2 libadwaita widgets that already behave well on touch

| Widget | Touch behaviour we get for free |
|---|---|
| `AdwNavigationSplitView` / `AdwNavigationView` | swipe back on narrow layouts |
| `AdwBottomSheet` (1.6) | drag handle, swipe down to close, peek height |
| `AdwBreakpoint` | layout switches by size, independent of input type |
| `AdwSpinRow` | large +/- buttons, a usable numeric field |
| `AdwToast` with an Undo button | the safety net for touch deletes (T3) |
| `AdwSpringAnimation` (with initial velocity) | kinetic pan deceleration and zoom snap-back |
| `AdwTabView` / `AdwTabOverview` | touch-friendly tabs for PRG / SD1 |
| `AdwToggleGroup` (1.7) | large segmented buttons for the drawing tool palette (needs libadwaita 1.7 instead of 1.6) |

`GtkScrolledWindow` already does kinetic touch scrolling with overshoot for lists and the program text.

**What is missing:** GTK has no "touch mode" and no automatic touch-target sizing. libadwaita controls are about 34 px
tall, which is fine for a finger on a laptop but tight with gloves. We add our own touch density (section 3).

## 3. Input source awareness and touch density

A small `InputMode` service in the UI layer watches the source of every press on the window (one capture-phase
`GtkGestureClick` with button 0 on the window):

- Last press from `TOUCHSCREEN` → mode **touch**; from `MOUSE` or `TOUCHPAD` → **pointer**; `PEN` → **pen**.
- Preference *Input layout*: Automatic (default) / Pointer / Touch. Automatic switches only when the last two presses
  agree, so one stray touch does not reflow the screen.
- In touch mode the window gets a `.touch` CSS class:
  - buttons and rows at least 48 px, op list rows 64 px, canvas handles drawn at 12 px radius;
  - the simulation transport bar and zoom buttons become visible on the canvas (T1);
  - numeric fields open the AlphaLink keypad (section 6) instead of the system on-screen keyboard.
- The canvas hit-test radius comes from the source of **that** event, not from the mode: mouse 6 px, pen 10 px,
  touch 24 px (about 6 mm at 100 dpi). The radius is in screen pixels, so it is the same at any zoom.

## 4. Canvas navigation (lathe view)

| Input | Action |
|---|---|
| Two-finger pinch | zoom about the fingers' centre, clamped (whole billet ... 0.01 mm per px) |
| Two fingers moving together | pan (centre movement of the zoom gesture), simultaneous with pinch |
| One-finger drag on empty space | pan (map-like); a fling continues with spring deceleration |
| One-finger drag on a handle or selected entity | move it (section 5), with snapping |
| Double tap | zoom to fit the part and billet; double tap again returns to the previous view |
| Two-finger tap | undo (optional, off by default, built on `GtkEventControllerLegacy`) |
| Long press | context menu at the finger (`GtkPopoverMenu`): Select chain, Edit values, Delete, Zoom here |
| Pen hover | crosshair and snap preview, like a mouse |
| Touchpad pinch / two-finger scroll | zoom / pan (same code as touch) |
| Mouse wheel / Ctrl+wheel / middle drag | pan / zoom / pan |
| On-canvas buttons (touch mode) | +, -, Fit, and a box-select toggle (box select has no free one-finger gesture, because one finger pans) |

All of these only change the **view transform**. They never touch the model and never enter the undo stack.

## 5. Drawing and editing contours

### 5.1 Principle: rough placement, then exact values

Lathe contours are mostly lines along Z or X, chamfers, radii and tapers, with known dimensions from the drawing. On touch:

1. **Place roughly.** With the Line tool, each tap adds a point. Snapping, in priority order: existing endpoints,
   intersections, billet edges, axis (X0), grid. Direction locks to Z, X and 45° when within 8° of them, so a quick
   tap-tap-tap draws a clean stepped profile.
2. **Correct exactly.** Every new segment shows a value chip (`Z -25.000` / `Ø 40.000`). Tapping the chip opens the
   keypad with that value. This is ALPHA-T's typed entry, triggered from the drawing.
3. **Loupe while dragging.** While a finger moves a point, a magnifier bubble (x4) shows the area under the finger
   80 px above it, with the crosshair, the active snap and the live coordinates. The finger never hides the point.
4. **Fine drag.** Dragging a handle starts at 1:1 and drops to 1:4 gain while the finger moves slowly (under 2 px per
   frame), for sub-grid adjustments without a keyboard.

### 5.2 Gestures per drawing tool

| Tool | Touch interaction |
|---|---|
| Select | tap = pick nearest entity within the hit radius; if 2+ entities are equally close, a small popover lists them ("Line Z -25", "Arc R2") |
| Line | tap = next point; tap the first point = close; Done button or double tap = finish |
| Arc / fillet | tap a corner → popover with radius chips (0.5, 1, 2, 5, Other...) |
| Chamfer | tap a corner → chips (0.5x45°, 1x45°, 2x45°, Other...) |
| Trim / delete | tap the segment; the result shows an Undo toast |
| Move point | drag the handle (loupe + snap), or tap the handle and type X/Z |

The tool palette is an `AdwToggleGroup` in a bottom bar in touch mode (thumb reach), and a sidebar strip in pointer mode.

### 5.3 Gesture conflicts and how to resolve them

| Conflict | Resolution |
|---|---|
| One-finger drag (pan) vs second finger landing (pinch) | when `GtkGestureZoom` begins, reset the drag and stop any half-made edit; pinch always wins |
| Tap vs start of a drag | tap only if movement stays under the GTK drag threshold; else it is a drag |
| Long press vs drag | long press fires only if the finger stays inside the threshold for the press time |
| Drag on entity (edit) vs drag on space (pan) | decided at press time by the hit test, with the event's source radius |
| Palm resting on screen | ignore touches whose contact area is very large when the driver reports it; otherwise one finger already on a handle locks out new touch sequences until it lifts |

If the stock gestures prove hard to coordinate in Python, the canvas switches to one `GtkEventControllerLegacy` and our
own small recogniser that tracks touch sequences directly. The tool code does not change (section 8).

### 5.4 Mouse, keyboard and fingers on one device

On the Surface the same person uses all three, often within a minute:

- **Mouse:** precise drawing (6 px hit radius, hover snap preview, no loupe), box select by dragging on empty space,
  middle-drag or wheel to pan, Ctrl+wheel to zoom.
- **Keyboard:** the ALPHA-T style command entry (`40,-25` Enter) and shortcuts.
- **Fingers:** navigation, selecting, reviewing, the ops list, the simulation, and rough edits with snapping and the
  keypad.
- Each event uses its own source's rules (hit radius, drag on empty space = pan for touch but box select for mouse), so
  mixing needs no mode switch. Only control sizes follow the Automatic input layout (section 3).
- A pen, if one is ever used, behaves like a precise mouse: tip = click, hover = snap preview, side button = context
  menu, and touches on the canvas are ignored while the pen is near the screen (palm rejection). This is not planned work.

## 6. Numeric keypad

The system on-screen keyboard is a full QWERTY layout and covers half the screen. AlphaLink has its own keypad, shown
in an `AdwDialog` (bottom sheet on narrow screens) when a numeric field is tapped in touch mode:

```
┌───────────────────────────────┐
│  X Ø [ 40.000 ]  Z [-25.000 ] │  fields to fill, the active one highlighted
│  [ 7 ][ 8 ][ 9 ]  [  ⌫  ]     │
│  [ 4 ][ 5 ][ 6 ]  [ +/- ]     │
│  [ 1 ][ 2 ][ 3 ]  [ ABS|INC ] │
│  [ 0 ][ . ][ → next ] [ ✓ ]   │
└───────────────────────────────┘
```

- Keys at least 64 px, usable with gloves.
- `ABS|INC` toggles absolute versus incremental from the previous point, as on a control.
- The value is checked against the unit's decimals (C6 in DESIGN.md) and the billet limits before it is accepted.
- Fields that use the keypad are buttons styled as entries, so the system keyboard does not pop up as well. A physical
  keyboard still types into them directly.

## 7. Operations, simulation, program and machine pages

### 7.1 Operations list

- Tap a row: select the op, highlight its toolpath on the canvas, open its editor in an `AdwBottomSheet`.
- Reorder: drag the row's **handle** icon only (`GtkDragSource` on the handle). A drag starting anywhere else in the row
  scrolls the list, so scrolling and reordering do not fight. Move up / Move down are also in the row menu (T1).
- No swipe-to-delete. Delete is in the row menu and shows an Undo toast (T3).
- Pick on drawing (contour, groove or thread lines): the sheet drops to its peek height, the canvas dims everything not
  pickable, the user taps the start and end segments, and a Done button in the sheet confirms.
- Editors use `AdwSpinRow` (big +/-) and the keypad for exact values. Toolpaths regenerate live, so the effect of each
  change is visible above the sheet.

### 7.2 Simulation

- Transport bar on the canvas in touch mode: ⏮ ◀ ▶/⏸ ▶ ⏭ (pass steps), large enough for gloves.
- Scrub strip: a horizontal `GtkGesturePan` strip under the canvas; dragging moves through the passes, faster the
  further the finger travels.
- Tap a toolpath segment: jump the simulation to that pass and select the matching line on the Program page.

### 7.3 Program page

- `GtkSourceView` read-only with kinetic scrolling.
- Pinch zooms the **font size** (not the view), remembered per device, for reading at arm's length from the lathe.
- Tap a line: the canvas shows that move, linked both ways with 7.2.
- Long press a line: Copy line, Copy N-block, Show on drawing.
- Search and Go to N-block are header bar buttons; there is no Ctrl+F on a touch screen.

### 7.4 Machine page and sending

- **Hold to send.** The Send CAM program button fills a progress ring over 1.5 s while held (`GtkGestureLongPress` with
  a raised `delay-factor` and a drawn progress). Letting go early cancels it. Then the confirmation from DESIGN.md
  (program number, tools, run time) appears, with Send as the non-default button.
- The same for Restore Manual Ops.
- **Touch lock.** A toggle in the header bar (and automatic during a transfer) makes the window ignore touch, except
  an Unlock control that needs a hold. This protects against coolant and sleeves while the machine runs. Mouse and
  keyboard keep working.
- Modem-line indicators and the transfer dialog use large text, readable from a step away.

## 8. Architecture

This fits DESIGN.md's rule that the UI changes the model only through commands:

```
alphacam_gtk/canvas/
  input.py        InputRouter: owns the GTK controllers, turns events into intents
                  Tap(pt, radius, source, n) Drag{Begin,Update,End}(pt, radius, source)
                  LongPress(pt) ViewZoom(centre, factor) ViewPan(delta) Fling(velocity)
  view.py         ViewTransform: model <-> screen, pan/zoom, kinetic animation, fit
  hittest.py      nearest entity / handle within a screen-pixel radius, ambiguity list
  snap.py         snap candidates (UI side of geometry.py snapping helpers)
  loupe.py        magnifier overlay
  tools/          one state machine per tool: select, line, arc, chamfer, trim, pick_contour
                  input: intents only; output: preview state + model commands
alphacam_gtk/input_mode.py   InputMode service + .touch CSS class (section 3)
alphacam_gtk/widgets/keypad.py
```

- Tools never see GTK events or device types, only intents with a pixel radius. Mouse, touchpad, pen and touch all run
  the same tool code, and the tools are unit-testable by feeding intents.
- View changes (`ViewZoom`, `ViewPan`, `Fling`) go straight to `ViewTransform` and are never undoable.
- Only the router knows about GTK gestures. Changing to the legacy-controller recogniser (5.3) touches only `input.py`.

## 9. Testing touch

- **Unit:** tool state machines and hit testing, driven by intent sequences (no display).
- **Recogniser:** feed recorded event sequences into the router.
- **End to end:** a virtual multi-touch device through `uinput` (python-evdev) under a headless GNOME/mutter or weston
  session, or `libinput record` / `libinput replay` traces captured on the real shop screen.
- **Hardware:** a checklist run on the Surface: pinch, pan, fling, long press, switching between mouse and finger,
  portrait rotation, a wet screen, touch lock, and a transfer with the screen idle (suspend inhibit).

## 10. Milestone placement

| M | Touch work |
|---|---|
| M2 (GTK shell) | InputRouter + ViewTransform: pinch, two-finger pan, one-finger pan, fling, double-tap fit, on-canvas zoom buttons; source-aware tap selection; InputMode + `.touch` density; op list (tap, handle reorder, bottom sheet); program page (kinetic scroll, pinch font size, line ↔ canvas link) |
| M3 (machine) | hold to send, touch lock, large transfer dialog, suspend inhibit during transfers |
| M4 (drawing tools, ops) | line/arc/chamfer touch tools, snapping with direction locks, loupe, fine drag, value chips, keypad, pick on drawing, simulation scrub strip |
| M5 | hardware checklist on the shop screen, preference polish |

## 11. Open questions

1. Which Surface Pro model? It must be an Intel one (section 1.1), and the Pro 8 or later has the 13" 2880x1920 screen
   the sizes above assume.
2. Should the two-finger tap undo be on by default?
3. Is libadwaita 1.7 acceptable (for `AdwToggleGroup`), or do we stay on 1.6 and build the palette from toggle buttons?
   Ubuntu 26.04 ships a newer libadwaita than 1.7, so the system side is fine; this is only about the minimum we declare.

Settled: the device is a Surface Pro on Ubuntu 26.04/26.10, used with fingers, the Type Cover keyboard and a mouse.
No gloves; a pen is unlikely.
