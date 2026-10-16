# AlphaLink CAM: touch and multi-touch design

Status: **design draft 1** (2026-10-13), mostly implemented by 2026-10-14; see the status table below. Companion to
`app/DESIGN.md` (section 6, user interface). Target device: Surface Pro 7 (12.3", 2736×1824 at 200 %, capacitive
multi-touch, pen).

## Implementation status (2026-10-14)

✓ done · ◐ partly or done differently · ✗ not yet. Every ✓ that has a 🧪 is covered by an end-to-end test with real
touch input (libei, `app/tests/e2e`).

| Section | Item | Status |
|---|---|---|
| 3 | InputMode: Automatic / Pointer / Touch, two presses agree; `.touch` density; per-source hit radius 6 / 10 / 24 px | ✓ |
| 4 | Pinch zoom about the fingers, two-finger pan 🧪; one-finger pan 🧪 with fling; double tap fit and back; touchpad, wheel | ✓ |
| 4 | On-canvas +, −, Fit in touch mode | ✓ (box-select toggle ✗) |
| 4 | Long press: context menu at the finger (Edit Values…, Delete, Zoom Here, Fit Part) 🧪 | ✓ (Select chain ✗) |
| 4 | Two-finger tap undo | ✗ |
| 5.1 | Tap points, snapping to ends and grid, Z / X / 45° direction lock 🧪 | ✓ |
| 5.1 | Value chip on the new segment, tap → keypad corrects the point 🧪 | ✓ (last segment only) |
| 5.1 | Loupe (x4, 80 px above the finger) while dragging a point 🧪; fine drag 1:4 when the finger moves slowly | ✓ |
| 5.2 | Line: tap = point 🧪, tap the first point = close 🧪, Done | ✓ (double tap stays Fit) |
| 5.2 | Fillet / chamfer popover with size chips and Other, Enter applies 🧪 | ✓ |
| 5.2 | Select: equally close elements (a corner) offered in a popover, touch only 🧪 | ✓ |
| 5.2 | Move a point: drag the selected element's end handle, with snapping; neighbours follow 🧪 | ✓ |
| 5.3 | Pinch wins over drag; tap vs drag threshold; quick taps not taken as a double tap 🧪 | ✓ (palm rejection ✗) |
| 6 | Keypad for points 🧪 with ABS/INC, → next, ✓ | ✓ |
| 6 | Keypad for the editor's number fields (a finger on the value) 🧪 | ✓ (billet-limit check ✗) |
| 7.1 | Tap a row: select, highlight, editor (side pane; the whole page below 700 px 🧪, not a bottom sheet) | ◐ |
| 7.1 | Reorder by the row's drag handle 🧪; move up / down; delete with Undo toast; pick on drawing with Done 🧪 | ✓ (dimming ✗) |
| 7.2 | Transport bar 🧪, scrub slider, tap a toolpath to jump the simulation there 🧪 | ✓ |
| 7.3 | Pinch zooms the font; tap a line ↔ drawing; search and Go to N-block (Ctrl+F, or the search button) 🧪 | ✓ (GtkSourceView, long-press copy ✗) |
| 7.4 | Hold to send 🧪 / restore; touch lock 🧪, automatic during a transfer | ✓ |
| 8 | Architecture: InputRouter, ViewTransform, hit testing | ◐ (tools live in `drawing_tools.py`, no intent layer, no `snap.py` / `loupe.py`) |
| 9 | End to end with a virtual touchscreen | ✓ libei, not uinput (see 9) |

## 1. Why touch, and where it matters

The app runs on two kinds of screen:

| Where | Typical hardware | What the user does there |
|---|---|---|
| Desk / office | laptop, mouse, touchpad, maybe a 2-in-1 | draws parts, sets up operations, posts PRGs |
| Next to the lathe | panel PC or tablet-style laptop on an arm, dirty or gloved hands, coolant nearby | checks the part and toolpaths, scrubs the simulation, reads the program, makes small edits, sends to the control |

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
- **End to end (done):** `app/tests/e2e`. It runs the installed Flatpak in a private headless GNOME Shell, with a
  virtual multi-touch touchscreen, keyboard and pointer from Mutter's EIS socket through libei. That needs no
  `uinput` and no root. The tests include pinch, pan, taps, long press, hold-to-send and touch lock, at the
  Surface's 200 % scale and in portrait. `libinput record` traces from the real screen are still an option.
- **Hardware:** a checklist run on the actual panel: pinch, pan, fling, long press, gloves, a wet screen, and touch lock.

## 10. Milestone placement

| M | Touch work |
|---|---|
| M2 (GTK shell) | InputRouter + ViewTransform: pinch, two-finger pan, one-finger pan, fling, double-tap fit, on-canvas zoom buttons; source-aware tap selection; InputMode + `.touch` density; op list (tap, handle reorder, bottom sheet); program page (kinetic scroll, pinch font size, line ↔ canvas link) |
| M3 (machine) | hold to send, touch lock, large transfer dialog |
| M4 (drawing tools, ops) | line/arc/chamfer touch tools, snapping with direction locks, loupe, fine drag, value chips, keypad, pick on drawing, simulation scrub strip |
| M5 | hardware checklist on the shop screen, preference polish |

## 11. Open questions

1. ~~Which screen will be next to the lathe?~~ A Surface Pro 7: a capacitive multi-touch tablet with a pen
   (2026-10-14). The whole multi-touch layer is used, with touch targets of at least 48 px at 200 %.
2. Gloves at the screen: yes or no? (Capacitive needs a glove-mode panel; resistive works with gloves.)
3. Should the two-finger tap undo be on by default? (Not built yet.)
4. ~~Is libadwaita 1.7 acceptable?~~ Yes: the GNOME 51 runtime has libadwaita 1.10, and the palette is an
   `AdwToggleGroup`.
