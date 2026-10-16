# Input-mode parity in the Rust app: touch, mouse/touchpad, keyboard, pen

Read-only audit, 2026-10-16. The repository on main (PR #6 merged). Paths: Rs = `rust/alphacam-gtk/src/…`,
UI = `app/alphacam_gtk/ui/*.blp` (shared by both apps). This builds on the touch parity check of 2026-10-16 and does
not repeat it: the Python and Rust touch code are line-for-line equal, so every cell here holds for Python too
unless it says otherwise.

Legend: **OK**; **AWKWARD** (reachable, but poor; the reason is given); **MISSING** (no way in this mode).
e2e: **W:** = `app/tests/e2e/test_workflows.py`, **I:** = `app/tests/e2e/test_interaction.py`. CI (`e2e.yml`) now runs
both apps (`matrix: python / rust`), and `test_interaction.py` runs only on the Rust leg. On the Python leg,
`press()` is an AT-SPI `do_action`, not real input. On the Rust leg, `press()` is a real libei click (or a tap
under `--touch`). libei has no tablet tool, so **there is no pen coverage at all**. `harness.hold_key()` exists but
nothing uses it.

## 0. How each mode is recognised

| | Touch | Mouse / touchpad | Keyboard | Pen |
|---|---|---|---|---|
| Layout | `.touch` after two touch presses in a row (Automatic), or when set in the menu. `Rs input_mode.rs:InputMode::pressed` | pointer layout | — (keys do not change the layout) | counted as **pointer**: `pressed` checks only for `Touchscreen` |
| Hit radius (`canvas/hittest.rs:Source::radius_px`, `canvas/input.rs:source`) | 24 px | 6 px | the Menu key uses the touch radius around the ALPHA-T cursor | 10 px. The eraser end falls through to Mouse (6 px) and does not erase |
| Touch-only widgets (`window.rs` l.612: `zoom_box.set_visible(Touch)`, `drawing_tools/mod.rs:set_touch`) | zoom buttons, Keypad, value chip, 12 px handles | hidden | — | hidden, unless Input Layout = Touch is forced |
| Long press (`canvas/input.rs` `GestureLongPress::touch_only(true)`, lock button `window.rs:setup_lock`) | yes | — (right click instead) | — (Menu / Shift+F10) | **no**. Only a barrel button that GDK reports as button 3 opens the menu |
| `keypad_on_touch` (`op_editor.rs:115`, `GestureDrag::touch_only(true)`) | yes | — | — | **no** |
| Touch lock (`input_mode.rs:swallow`) | blocked | works | works | works |

Mixing pen and finger in Automatic: two pen presses switch to pointer, and two finger presses switch back. Each
switch reflows the window (button sizes 48 ↔ default) and shows or hides the zoom buttons and the Keypad. On a
Surface with a pen this flips back and forth.

## 1. Drawing tools

| Feature | Touch | Mouse | Keyboard | Pen | e2e |
|---|---|---|---|---|---|
| Choose a tool (Select/Line/Arc/Fillet/Chamfer/Corner) | OK: tap the AdwToggleGroup | OK | OK: X/+ Line, W Arc, F Fillet, B Chamfer, K Corner (`hit_keys.rs`), or Tab to the palette. `palette_changed` then gives the canvas the focus | OK | touch: I:DrawingByTouch (Arc/Corner/Fillet radio taps). keys: W:Keyboard.test_exercise1 (b, f) |
| Line: points | OK: taps with snapping (`mod.rs:tapped`/`add_point`), Keypad, value chip | OK: click; hover marker and rubber band (`mod.rs:motion`) | OK: type `x,z` / `@dx,dz` in the command entry, or arrows + Enter (`accept_cursor`) | OK: tap with hover marker. **AWKWARD**: no Keypad in the pointer layout and no on-screen keyboard for pen, so a pen alone cannot type a coordinate | touch: W:Touch.test_line_tool_by_taps, Scale200; keys: W:Keyboard.*, KeyboardMore.test_undo_and_redo; mouse: none; keypad: W:TouchMore.test_keypad_enters_points, I:test_keypad_keys |
| Line by length,angle (D), start at cursor (S), freehand (+) | **AWKWARD**: only by typing the letter into the command entry with the GNOME on-screen keyboard (`entry_key` turns letters into commands). No button | **AWKWARD**: needs the keyboard | OK | **MISSING** (no on-screen keyboard for pen) | keys only (x, @) |
| Close the profile / Done / Esc | OK: tap the first point; Done | OK | OK: Esc (`window_esc`), Alt+D | OK | W:TouchPlacement.test_tapping_the_first_point… |
| Arc (3 points) | OK | OK | OK: W, then arrows/G + Enter ×3 | OK | I:test_arc_by_three_taps. keys: none |
| Fillet / Chamfer | OK: tap the corner → popover chips | OK | OK: F/B → Enter at the corner nearest the cursor, type the size, Enter (`dialogs.rs:size_popover`) | OK | I:test_fillet_size_chip_by_tap, W:Keyboard.test_exercise1 |
| Corner (trim two lines) | OK | OK | OK: K, then Enter takes the line nearest the cursor (`accept_cursor`) | OK | I:test_corner_joins_two_lines. keys: none |
| Construct: Circle… | OK | OK | OK: More (Tab, Enter) → number dialog | OK | none |
| Construct: Tangent, Tangent at Angle, Parallel, Split Arc, Blend (O), Mirror; choose a dashed solution | OK: taps (`construct.rs:construct_tap`, `ref_at`, `choose`) | OK | **MISSING**: `accept_cursor` has no construct branch, so Enter does nothing; you cannot pick an element or a solution with keys. **O is an ALPHA-T key that starts Blend, which then cannot be finished by keys.** ALPHA-T cycled solutions with Space; there is no equivalent | OK | mouse: W:StandardFeatures (thread, corners). Tangent/parallel/split/blend/mirror: **none in any mode** |
| Standard Thread / Chamfer-Round Outside Corners | OK | OK | AWKWARD: thread on a selected line needs L first, then More… | OK | mouse: W:StandardFeatures.* |
| Measure X,Z / L,A (M, N) | **AWKWARD**: on-screen keyboard letter in the entry only | AWKWARD (keyboard) | OK: M/N, Enter at points | **MISSING** | none |
| Find point / intersection / move cursor (P, I, G, H, Y) | n/a (snapping does it) | n/a | OK | n/a | G: W:Keyboard.test_exercise1 |
| Add text (T), erase text (U) | **MISSING** without the on-screen keyboard (no button or menu item; only in the entry) | **AWKWARD**: needs the keyboard | OK | **MISSING** | **none** |
| Erase the whole drawing | OK: More → Erase Drawing… | OK | OK: Q | OK | I:MenusByTouch.test_erase_drawing… |

## 2. Selecting and editing elements

| Feature | Touch | Mouse | Keyboard | Pen | e2e |
|---|---|---|---|---|---|
| Select an element | OK: tap; ambiguity popover at corners (`dialogs.rs:choose_element`) | OK: click (6 px) | **AWKWARD**: only "nearest line/arc to the cursor" (L/A); you must first steer the cursor with arrows or G | OK (10 px) | touch: W:TouchMovePoint, TouchMore2.test_corner_tap…; keys: none |
| Select an operation's toolpath on the drawing | OK | OK | AWKWARD: through the op list instead | OK | W:TouchMore2.test_tap_a_toolpath… |
| Move a point by dragging a handle | OK: loupe, fine drag (`handles.rs`) | OK (6 px grab, 6 px handles) | n/a: use Edit Values | OK (no loupe) | touch: W:TouchMovePoint.*; mouse and pen: none |
| Move a point exactly (Edit Values…) | **AWKWARD**: long press → Edit Values… opens plain SpinRows (`dialogs.rs:edit_selected_element`), so you get the system keyboard, not the keypad. The value chip covers only the last drawn point | OK | OK: E (or the Edit button) | **AWKWARD/MISSING**: right click, or the Edit button; the numbers then cannot be typed without a keyboard (no keypad, no on-screen keyboard) | **none in any mode** |
| Delete an element | OK: long press → Delete, or the trash button | OK: right click → Delete, trash button | OK: `-` anywhere on the Part page. **AWKWARD**: Delete/Backspace work only while the canvas has the focus (`mod.rs:key`), although the Shortcuts window lists Delete without that limit | OK: trash button | W:TouchPlacement.test_long_press_menu_deletes…, W:StandardFeatures.test_right_click…; keys: none |
| Undo the delete from the toast | OK | OK | OK: Ctrl+Z | OK | I:test_delete_toast_undo_by_tap |
| Undo / Redo | OK: header buttons, or the main menu when narrow | OK | OK: Ctrl+Z, Ctrl+Shift+Z, Ctrl+Y | OK | W:KeyboardMore.test_undo_and_redo; tap Undo: I:test_erase_drawing… |
| Context menu | OK: long press (`input.rs` GestureLongPress) | OK: right click (`input.rs` GestureClick button 3) | OK: Menu or Shift+F10 at the ALPHA-T cursor, **only while the canvas has the focus** (`mod.rs:key`) | **AWKWARD**: long press is `touch_only`. This works only if the barrel button arrives as button 3 (GDK maps BTN_STYLUS to secondary, but GNOME's tablet settings can remap it). A pen without a button has no context menu | touch: W:TouchPlacement, I:CanvasNavigation.test_long_press_zoom_here…; right click: W:StandardFeatures; **Menu/Shift+F10: none** |

## 3. Canvas navigation

| Feature | Touch | Mouse / touchpad | Keyboard | Pen | e2e |
|---|---|---|---|---|---|
| Pan | OK: one-finger drag, fling (`input.rs`) | OK: drag on empty space; wheel; Shift+wheel; touchpad two-finger scroll | **MISSING**: the arrows move the ALPHA-T cursor, and the view does not follow it off screen (`mod.rs:move_cursor`). The only workaround is Menu → Zoom Here then R/V | OK: drag | W:TouchMore.test_one_finger_drag_pans, I:test_fling…; wheel: **none** |
| Zoom in | OK: pinch, + button | OK: Ctrl+wheel at the pointer, Ctrl+=; touchpad pinch (GestureZoom) | OK: Ctrl+= (about the centre); Z; Menu → Zoom Here (at the cursor) | **AWKWARD**: no wheel, no pinch, zoom buttons hidden in the pointer layout. Only barrel button → Zoom Here works | W:Touch.test_pinch…, I:test_zoom_buttons; mouse/keys: **none** |
| Zoom out | OK: pinch, − button | OK: Ctrl+wheel, Ctrl+− | OK: Ctrl+− | **MISSING**: only Fit (double tap) or V (keyboard) | I:test_zoom_buttons only |
| Fit / back | OK: double tap, Fit button, menu Fit Part | OK: double click, main menu Fit | OK: Ctrl+0, R; V recalls the view | OK: double tap | I:test_double_tap_fits_and_taps_again_go_back; keys: none |
| Zoom Here | OK: long press | OK: right click | OK: Menu key at the cursor | AWKWARD (barrel button only) | I:test_long_press_zoom_here… |
| Shortcuts-window text vs behaviour | — | — | **Mismatch**: "z  Zoom In at the Cursor", but `hit_key` Hit::ZoomIn calls `c.zoom(1.25)` about the view centre. "r  Redraw" actually fits | — | none |

## 4. Operations

| Feature | Touch | Mouse | Keyboard | Pen | e2e |
|---|---|---|---|---|---|
| Add (+ menu) | OK | OK | OK: C, or Tab to + | OK | I:MenusByTouch.test_every_add_operation_item_by_tap; W:Mouse.test_exercise2 |
| Select | OK: tap, swipe guard (`window.rs:guard_list_taps`) | OK | OK: Tab to the list, arrows select (ListBox cursor). Escape deselects (`win.deselect` accel) | OK | W:TouchScroll, TouchMore.*; keys: none |
| Reorder | OK: drag handle (`window.rs:drag_handle`), Move Up/Down | OK | AWKWARD: Tab into the editor header → Move Up/Down. No Alt+Up/Down accelerator | OK | mouse drag: W:TouchMore2.test_drag_handle_reorders…; tap Move Up: I:EditorByTouch.test_header_buttons…; **touch drag of the handle: none**; keys: none |
| Duplicate | OK: button row at the bottom of the editor | OK | AWKWARD: many Tabs to reach the last row; no accelerator | OK | W:QuickWinsUI.test_grooving… (press) |
| Delete | OK: editor trash | OK | AWKWARD: Tab to the editor header; Delete on a list row does nothing | OK | I:test_header_buttons_move_delete_and_close |
| Edit numbers | OK: `keypad_on_touch` (tap → keypad, −/+ step, swipe scrolls) | OK: type, −/+, wheel | OK | **AWKWARD/MISSING**: the GestureDrag is `touch_only`, so a pen tap focuses the text with no keypad and no on-screen keyboard. Only −/+ work | W:TouchPlacement.test_editor_number…, test_swipes…; I:test_plus_and_minus…; mouse/keys: none |
| Switches / combos | OK | OK | OK | OK | W:TouchMore.test_editor_switch_by_touch |
| Pick contour | OK: taps, Done in the banner | OK | **AWKWARD**: element picking has no keys (`hit_action` returns only PickDone while picking). The fallback is to type the IDs (`g0 g1`) into the "Contour, in cutting order" EntryRow (`op_editor.rs` l.443); you find each ID with L's toast | OK | W:TouchMore.test_pick_contour_by_taps; mouse/keys: none |

## 5. Simulation, program, sheet, tools

| Feature | Touch | Mouse | Keyboard | Pen | e2e |
|---|---|---|---|---|---|
| Transport (first/prev/play/next/all/start of op/speed) | OK | OK | AWKWARD: Tab to each button; no accelerators (e.g. Space play, `[`/`]` pass) | OK | I:SimulationByTouch.test_play_pause…; W:KeyboardMore.test_simulation_steps…, QuickWinsUI.test_simulation_speed… (press) |
| Scrub | OK: GtkScale drag | OK: drag, wheel | AWKWARD: Tab to the scale, then arrows one move each. The adjustment has no page increment (`simulation.blp`), so PgUp/PgDn do nothing; Home/End work | OK | I:test_play_pause_and_scrub (touch); keys: none |
| Jump to a toolpath move | OK: tap the toolpath | OK | AWKWARD: per operation only ("Start of the Selected Operation") | OK | W:TouchMore2.test_tap_a_toolpath… |
| Program: select the op of a line | OK: `GestureClick released` → `window.rs:program_tapped` | OK | **MISSING**: the TextView has no visible cursor, no key handler, and search does not select the op (`show_found`) | OK | **none** |
| Program: font size | OK: pinch (`pinch_begin/pinch_scaled`) | touchpad pinch OK; **mouse MISSING** (no Ctrl+wheel) | **MISSING**: Ctrl+=/− zoom the hidden canvas (`win.zoom-in`); no preference | **MISSING** | **none** |
| Program: search, go to N block | AWKWARD: the search button works, but typing needs the on-screen keyboard | OK | OK: Ctrl+F, Enter / Shift+Enter, Esc | MISSING typing (no on-screen keyboard) | W:TouchMore2.test_search_and_go_to_block (keys), I:test_program_search_button_by_tap |
| Sheet: switch, Save as Text/PDF, notes | OK (notes: on-screen keyboard) | OK | OK (mnemonics `_Save as Text…`, `Save as _PDF…`) | OK, except notes | W:WorkflowSteps456.test_sheet_pdf… (press) |
| Tools page: add/delete/import/reset, name/number rows | AWKWARD: EntryRow/SpinRow give the system keyboard, not the keypad | OK | OK (mnemonics on the four buttons) | AWKWARD (no keyboard) | **none** |

## 6. Machine page

| Feature | Touch | Mouse | Keyboard | Pen | e2e |
|---|---|---|---|---|---|
| Send (hold 1.5 s) | OK: `hold_button.rs` GestureClick(button 0) | OK | OK in code: Space/Enter held (`hold_button.rs` key controller, bubble phase). **Unverified**: it relies on GtkButton's own Space/Enter "activate" binding not swallowing the press first. Any key release stops it | OK (button 0) | finger: I:SendByTouch.test_finger_hold…; mouse: W:SendOverVirtualPort.*; tap does nothing: W:Touch.test_send_needs_a_hold; **keyboard: none** (`hold_key` unused); pen: none |
| Restore Manual Ops (hold) → file → confirm | OK | OK | as Send | OK | **none** |
| Back up Manual Ops | OK (ButtonRow) | OK | OK | OK | W:BackupOverVirtualPort.* (press) |
| Cable (loopback) test | OK | OK | OK | OK | none |
| Port select / rescan, delay, wait, parity | OK (delay SpinRow: system keyboard) | OK | OK | OK | port shown only |
| Auto touch lock while sending | locks touch | n/a | n/a | n/a: the pen still works while locked (by design) | I:test_finger_hold_sends… asserts lock on and off |

## 7. Dialogs, menus, window

| Feature | Touch | Mouse | Keyboard | Pen | e2e |
|---|---|---|---|---|---|
| New Part | AWKWARD: billet SpinRows give the system keyboard | OK | OK: Ctrl+N, Enter | AWKWARD (no keyboard) | W:Keyboard.test_new_part… |
| Preferences | AWKWARD (SpinRows, system keyboard) | OK | OK: Ctrl+, | AWKWARD | none |
| Keypad (`keypad.rs`) | OK | OK (if shown: touch layout or chip) | OK: digits, `.`, `-`→±, Backspace, Tab/`,`→next, Enter→✓ (`hw_key`). **AWKWARD**: Tab is taken for "next field", so you cannot Tab to ABS/INC; it has no key (arrows may move focus there). If a key button holds the focus, Enter may activate that button instead of ✓ (unverified) | OK if shown | I:test_keypad_keys (taps); W:TouchMore.test_keypad_enters_points; hardware keys: none |
| File dialogs (portal) | OK | OK | OK: Ctrl+O, Ctrl+Shift+S, Ctrl+E | OK | W:FileDialogs.* (keys) |
| Main menu | OK | OK | OK: F10 (`primary: true` MenuButton) | OK | W:Mouse.test_main_menu_has_the_gnome_items (press + Ctrl+?) |
| Keyboard Shortcuts / Help / About | OK | OK | OK: Ctrl+?, F1 | OK | W:Mouse.test_main_menu… |
| Switch pages (Part/Program/Sheet/Tools/Machine) | OK | OK | AWKWARD: Tab to the ViewSwitcher; no Ctrl+PgUp/PgDn or Alt+1…5 | OK | press/tap throughout |
| Touch lock | OK: tap to lock; hold 2× long press to unlock | OK: click to lock or unlock | OK: Tab + Space | OK: a pen click unlocks (the tap gesture is `touch_only`) | W:TouchMore.test_touch_lock_ignores_taps_until_held; mouse unlock: none |
| Input Layout (Automatic/Pointer/Touch) | OK | OK | OK: F10 → menu | OK. But **pen counts as pointer** in Automatic | I:InputLayout.* (tap and click) |

## 8. Shortcuts window (`ui/shortcuts.blp`) vs what is bound

Everything listed is bound. The mismatches:

- **"z: Zoom In at the Cursor"** zooms about the view centre (`mod.rs:hit_key` Hit::ZoomIn → `c.zoom(1.25)`).
  `ctx.zoom` (Zoom Here) is the one that zooms at the cursor.
- **"r: Redraw"** actually fits the part (Hit::Fit). The name is ALPHA-T's, the behaviour is Fit.
- **Delete (Delete Element)** works only while the canvas has the focus. `-` works everywhere.
- **Shift+F10 / Menu** work only while the canvas has the focus. The menu says "Drawing Has the Focus" only for the
  Cursor group, and Shift+F10 is listed there, so this one is documented.

Bound but not listed:

- Ctrl+Y (redo) and Ctrl+= (zoom in).
- `3` and Enter finish picking.
- `-` erases the selected element (only the toast says so).
- Escape → `win.deselect`.

Not bound at all:

- simulation transport;
- program font size;
- page switching;
- reorder / duplicate / delete an operation.

`win.deselect` is an application accelerator on plain **Escape**. It is worth one test that Escape still closes an
AdwDialog (New Part, Preferences) and the keypad, rather than only deselecting the op. GTK4 runs application
accelerators early for widgets inside the window.

## 9. Features only one mode can reach

- **Keyboard only:**
  - Text add/erase (T/U).
  - Measure (M/N).
  - Line by length+angle (D).
  - Move the cursor (G/H/Y).
  - Find point/intersection (P/I).

  Touch reaches these only by typing the letter into the command entry with GNOME's on-screen keyboard. Pen
  cannot reach them at all.
- **Pointer/touch only (no keys):**
  - Construction picks (tangent, parallel, split, blend O, mirror) and choosing a dashed solution.
  - Picking contour elements (IDs typed into the EntryRow as a workaround).
  - Panning.
  - Selecting an op from a Program line.
  - Program font size (touch or touchpad pinch only).
- **Touch only:**
  - The keypad for op-editor numbers (`keypad_on_touch` is `touch_only`).
  - Long press.
  - The loupe.
  - Fling (also on mouse release with speed).
  - Zoom buttons, Keypad button and value chip (hidden in the pointer layout).

  A pen (pointer layout) therefore has no zoom-out, and no way to type a number without a keyboard.

## 10. e2e coverage summary

**Covered** (mode):

- touch:
  - pinch;
  - pan;
  - fling;
  - double-tap fit/back;
  - zoom buttons;
  - long-press Zoom Here/Fit;
  - long-press Delete;
  - toast Undo;
  - Line/Arc/Corner/Fillet by taps;
  - keypad keys;
  - value chip (value, follows zoom, hides);
  - handle drag + loupe;
  - pick contour;
  - ambiguity popover;
  - op list swipe guard;
  - editor number → keypad, −/+, swipes, header buttons, switch;
  - every + menu item;
  - Erase Drawing;
  - play/pause, scrub;
  - search button;
  - tap a toolpath;
  - finger hold to send + auto-lock;
  - touch lock;
  - layout Automatic + saved Touch;
  - target sizes.
- mouse:
  - right-click menu;
  - drag-handle reorder;
  - hold to send;
  - + menu ops;
  - standard features (click select);
  - Turn Around / template / sheet (press);
  - two clicks → pointer layout.
- keyboard:
  - Ctrl+N + typed line;
  - exercise 1 (x, @, g, b, f, Enter);
  - arrows/End cursor;
  - Ctrl+Z/Ctrl+Shift+Z;
  - Ctrl+F search + go to N;
  - Ctrl+? shortcuts;
  - file dialogs (Ctrl+O/Ctrl+Shift+S/Ctrl+E);
  - Ctrl+S everywhere.
- pen: **nothing** (libei cannot make tablet events).

**Untested cells worth a test** (in priority order):

1. Keyboard hold-to-send (Space held 2 s on "Send to the Lathe"). `harness.hold_key` is already written and unused.
2. Edit Values… (long press / E) changes a point exactly. Untested in every mode.
3. Menu key / Shift+F10 opens the drawing menu at the cursor; Delete key on a selected element.
4. Construction commands (Tangent, Parallel, Split Arc, Blend, Mirror). Untested in any mode.
5. Program: tap/click a line → selects its op; pinch → font size.
6. Escape closes a dialog (New Part, Preferences, Keypad) without other effects (the `win.deselect` accelerator).
7. Mouse wheel pan, Shift+wheel, Ctrl+wheel zoom at the pointer; Ctrl+=/−/0.
8. Op list selection by arrow keys; Move Up/Down via keyboard focus.
9. Restore Manual Ops hold → file → confirm.
10. Keypad hardware keys (typed digits, Tab, Enter).
11. Unlock the touch lock with a mouse click.
12. Touch drag of the reorder handle.
