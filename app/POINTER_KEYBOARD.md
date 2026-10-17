# AlphaLink CAM: mouse, keyboard and pen design

Status: **design draft 1** (2026-10-17). Companion to `app/TOUCH.md` (touch) and `app/DESIGN.md` (section 6, user
interface). It covers the other three ways in: a mouse or touchpad, the keyboard (a workshop PC, or the Surface's
Type Cover), and the Surface Pen. It closes the gaps found by the input-mode parity check of 2026-10-16: every
feature should be reachable from every input, and nothing should need a second one. The Rust app (`rust/alphacam-gtk`)
is the implementation; the Python app is frozen.

## Implementation status

✓ works today · ✗ proposed here, not built yet. 🧪 = an end-to-end test with real input (libei, `app/tests/e2e`).

| § | Item | Status |
|---|---|---|
| 3 | Wheel pans 🧪, Shift+wheel sideways, Ctrl+wheel zooms at the pointer 🧪; Ctrl+= / Ctrl+− / Ctrl+0 🧪; double click fits | ✓ |
| 3 | Middle-button drag pans | ✓ |
| 3 | Keyboard pan: Ctrl+arrows; the view follows the ALPHA-T cursor off the edge | ✓ |
| 3 | `z` zooms at the cursor (as the Shortcuts window says) | ✓ |
| 4 | Hover marker and rubber band; click points; right click menu 🧪; drag handles | ✓ |
| 4 | Keyboard drawing: ALPHA-T letters, the command entry, arrows + Enter 🧪 | ✓ |
| 4 | Construction commands finished by keys (Enter picks, Tab cycles the dashed solutions) | ✓ |
| 4 | Text, Measure, Line by length and angle in the More menu (reachable without letters) | ✓ |
| 5 | Shift+F10 / Menu 🧪 on the drawing; Delete 🧪 anywhere on the Part page outside text | ✓ |
| 5 | `[` / `]` select the previous / next element along the profile | ✓ |
| 5 | Pick contour by keys: Space adds or removes the element at the cursor | ✓ |
| 6 | Operations list: arrows select; Alt+↑ / Alt+↓ move; Ctrl+D duplicates; Delete deletes (Undo toast) | ✓ |
| 7 | Simulation: F5 play / pause, Ctrl+[ / Ctrl+] previous / next pass, Alt+Home / Alt+End first / all; PgUp/PgDn on the slider | ✓ |
| 8 | Program: a visible caret; Enter or a search hit selects the line's operation | ✓ |
| 8 | Program font: Ctrl+= / Ctrl+− / Ctrl+0 and Ctrl+wheel while the Program page is shown; remembered | ✓ |
| 9 | Pages: Alt+1 … Alt+5, Ctrl+PgUp / Ctrl+PgDn | ✓ |
| 9 | Hold to send by Space / Enter 🧪; confirmations default to Cancel, Alt+S sends 🧪 | ✓ |
| 9 | Escape closes dialogs and the keypad before it cancels or deselects 🧪 | ✓ |
| 10 | Pen: does not flip the Automatic layout; keypad and zoom buttons for the pen; long press = menu | ✓ (not yet tried with a real pen) |
| 11 | The keypad for every number field (dialogs, Preferences, Tools page) for finger and pen | ✓ |
| 11 | Keypad keys: digits, `.`, `-`, Backspace, Tab / `,` next, Enter ✓; `i` toggles ABS / INC | ✓ |
| 12 | The Shortcuts window matches what the keys do | ✓ |

## 1. Who uses which input

- **Workshop PC** (mouse and keyboard). Operators who used ALPHA-T on DOS know its single-letter keys and type
  coordinates. They expect the keys to keep working, and they draw with the mouse when it is quicker.
- **Surface Pro 7 with the Type Cover.** The same, plus a touchpad: two-finger scroll and pinch.
- **Surface Pen.** Precise like a mouse, but with no wheel, no keyboard when the cover is off, and a barrel button
  that GNOME may or may not report as a right click. Today the app treats it as a mouse, which leaves it without
  zoom out and without a way to type a number.

## 2. Principles

1. **Every action from every input.** A feature reachable only by touch, only by mouse or only by keys is a bug.
   The gaps today are in the parity list (`docs/INPUT_PARITY.md`, 2026-10-16); this document assigns each one a way in.
2. **Keyboard-complete, GNOME first.** Standard GNOME keys keep their meaning (HIG, "Keyboard"): Ctrl+N/O/S/Q/W,
   Ctrl+Z / Ctrl+Shift+Z, Ctrl+F, F10 for the main menu, Shift+F10 / Menu for a context menu, F1 for help, Ctrl+?
   for the shortcuts, Escape to close or cancel, Tab and F6 to move between regions, Alt+number for pages.
3. **ALPHA-T's letters only on the drawing.** The single-letter keys (L, A, F, B, K, G…) act only while the
   drawing has the keyboard focus, so they never steal a letter from a text field. A click, tap or Escape gives
   the drawing the focus back; the new region keys (F6) make that reachable without a pointer.
4. **Discoverable.** Every accelerator is in the Shortcuts window and in the tooltip of its button
   ("Undo (Ctrl+Z)"), and the Shortcuts window says only what the key really does.
5. **Escape peels one layer.** In order: close a popover or dialog (keypad included); end a command, pick or
   construction; finish the line being drawn; deselect. Never two at once.
6. **Safe by default.** Anything that reaches the lathe needs a hold (finger, mouse button, Space or Enter), and
   its confirmation defaults to Cancel; Alt+the mnemonic confirms.

## 3. Canvas navigation

| Action | Mouse / touchpad | Keyboard | Pen |
|---|---|---|---|
| Pan | drag on empty space; wheel; Shift+wheel sideways; touchpad two-finger scroll; **middle-button drag** (✗) | **Ctrl+arrows: an eighth of the view** (✗). **The view follows the ALPHA-T cursor** when an arrow moves it past the edge, with a 10 % margin (✗) | drag on empty space |
| Zoom in / out | Ctrl+wheel at the pointer; touchpad pinch | Ctrl+= / Ctrl+− about the centre; **`z` at the cursor** (✗: centre today) | **zoom buttons** (§10) |
| Fit / back | double click; main menu Fit Part | Ctrl+0; `r` (ALPHA-T "Redraw" = fit); `v` recalls the view | double tap |
| Zoom here | right click → Zoom Here | Menu → Zoom Here (at the cursor) | long press or barrel button (§10) |

The cursor-follow rule is what ALPHA-T users expect from a cursor-driven program: they steer the cursor, not the
view. Ctrl+arrows covers the rest without moving the cursor.

## 4. Drawing

**Mouse.** Hover shows the snap marker and the rubber band from the last point; a click places it; the right click
opens the drawing menu; a drag on a selected element's end handle moves it with snapping. Precision comes from the
keyboard: type `x,z` or `@dx,dz` and Enter at any time, or E to edit the values.

**Keyboard.** The ALPHA-T letters, the command entry and arrows + Enter work today for lines, arcs, fillets,
chamfers, corners, measuring, text and the cursor commands. Two things are missing:

- **Construction commands** (Tangent, Tangent at Angle, Parallel, Split Arc, Blend `o`, Mirror): Enter takes the
  element or point nearest the cursor, as Corner does; when the command offers several solutions (drawn dashed),
  **Tab and Shift+Tab move the highlight between them and Enter takes it** (✓). Escape cancels. ALPHA-T's own `o`
  then finishes as it did on DOS.
- Nothing else needs new keys; the shortcut list in §12 must be corrected.

**Touch and pen without letters.** Text (`t`), Erase Text (`u`), Measure (`m`, `n`), Line by Length and Angle (`d`)
and Move Cursor (`g`, `h`, `y`) get items in the **More** menu, each opening the same dialog the letter does (✓).
Today a finger reaches them only by typing the letter into the command entry, and a pen not at all.

## 5. Selecting and editing elements

| Action | Mouse | Keyboard | Pen |
|---|---|---|---|
| Select | click (6 px radius) | `l` / `a` nearest line / arc to the cursor; **`[` / `]` previous / next element along the profile** (✓) | tap (10 px) |
| Context menu | right click | Shift+F10 or Menu at the cursor | long press (§10) or barrel button |
| Edit exact values | right click → Edit Values…; E | E | menu → Edit Values… with the keypad (§11) |
| Delete | right click → Delete; trash button | Delete, Backspace or `-` **whenever the Part page has the focus outside a text field** (✗: drawing focus only for Delete) | menu → Delete; trash button |
| Pick a contour | click elements, Done | **Space adds or removes the element nearest the cursor**, `l`/`a` and `[`/`]` move between them, Enter or `3` finishes (✓) | tap elements |

`[` and `]` follow the order of the profile (the contour order the operations use), so a keyboard user can walk
the part one element at a time and see each one's description in the read-out.

## 6. Operations list and editor

| Action | Mouse | Keyboard (list focused) |
|---|---|---|
| Select | click a row | ↑ / ↓ (✓), Home / End |
| Open the editor | click | Enter (✓) |
| Reorder | drag the handle; Move Up / Down in the editor | **Alt+↑ / Alt+↓** (✗) |
| Duplicate | the editor's last row | **Ctrl+D** (✗) |
| Delete | the editor's trash | **Delete** (✗), with the Undo toast |
| Add | + menu | `c` on the drawing (✓), or Tab to + and Enter |
| Numbers | type, −/+, wheel | type, ↑ / ↓ steps, PgUp / PgDn ten steps |

Alt+↑ / Alt+↓ is GNOME's usual reorder key (Settings, Files' sidebar). Each of these keys works with the list or
the editor focused, so the editor's header buttons are no longer the only way.

## 7. Simulation

| Action | Mouse | Keyboard (anywhere on the Part page) |
|---|---|---|
| Play / pause | ▶ | **F5** (✗) |
| Previous / next pass | ◀◀ / ▶▶ | **Ctrl+[ / Ctrl+]** (✗) |
| First move / show all | ⏮ / ⏭ | **Alt+Home / Alt+End** (✗) |
| Scrub | drag the slider, wheel | the slider focused: ← / → one move, **PgUp / PgDn a tenth** (✗: no page step), Home / End |
| Jump to a toolpath | click it | the operation's "Start of the Selected Operation" |

F5 stays free in GNOME apps and ALPHA-T, and Space must stay free for buttons and the hold-to-send.

## 8. Program page

- **A visible caret** in the program text (read-only, as in a viewer): arrows, PgUp/PgDn, Ctrl+Home/End move it.
  **Enter selects the operation that owns the caret's block**, as a tap or click on the line does, and a search
  hit (Ctrl+F, Enter) selects it too (✗).
- **Font size:** while the Program page is shown, Ctrl+= / Ctrl+− / Ctrl+0 and Ctrl+wheel change the program's
  font (7 to 32 pt, as the pinch does) instead of zooming the hidden drawing (✗). The size is remembered in the
  settings (TOUCH.md 7.3 asks for the same).

## 9. Pages, Machine page, dialogs

- **Pages:** Alt+1 Part, Alt+2 Program, Alt+3 Sheet, Alt+4 Tools, Alt+5 Machine, and Ctrl+PgUp / Ctrl+PgDn for
  previous / next (✗). F6 / Shift+F6 cycle the regions (operations, page, bottom bar).
- **Hold to send / restore:** a press held 1.5 s by finger, mouse button, Space or Enter (✓). With keys the
  confirmation opens on the release (key repeat must not press its buttons). The confirmation defaults to
  Cancel; Alt+S sends (✓).
- **Dialogs:** every number dialog opens with its first field focused and selected; Enter applies; Escape
  cancels; the keypad is one tap away for finger and pen (§11).

## 10. Pen

The pen is precise, so it keeps the pointer layout's density, but it cannot type and has no wheel (✓; libei has no tablet devices, so only a real pen can test it):

1. **A pen press does not count in Automatic.** Only the mouse, the touchpad and the touchscreen switch the
   layout, so a user alternating pen and finger does not see the window reflow back and forth.
2. **Pen helpers in the pointer layout:** after a pen press, show the on-canvas zoom buttons (+, −, Fit) and the
   Keypad button, and make the value chip and the editor's number keypad answer a pen tap, as they answer a finger.
   They hide again after a mouse press.
3. **Long press with the pen opens the drawing menu** (a `GestureLongPress` for the stylus), so a pen without a
   barrel button still reaches Edit Values…, Delete, Zoom Here and Fit Part. The barrel button stays a right click.
4. The eraser end is a pointer with the pen's radius (it erases nothing; erasing stays an explicit command).

## 11. Numbers without a keyboard

`keypad_on_touch` (the op editor's numbers) becomes the rule for **every** number field a finger or pen can reach:
Edit Values…, New Part (billet), Preferences, the Tools page, the construction dialogs, the fillet "Other" size and
the Machine page delay (✓). A still tap opens the keypad with that one field; a swipe scrolls; −/+ step.

In the keypad, `i` toggles ABS / INC (✓), since Tab is "next field".

## 12. Shortcuts window corrections

| Listed | Does today | Change |
|---|---|---|
| `z` Zoom In at the Cursor | zooms about the centre | zoom at the cursor (as listed) |
| `r` Redraw | fits the part | list it as "Fit Part (ALPHA-T Redraw)" |
| Delete: Delete Element | only with the drawing focused | anywhere on the Part page outside text (§5) |
| not listed | Ctrl+Y redo, Ctrl+= zoom in, `-` delete, `3` / Enter finish picking | list them |
| not bound | simulation, pages, reorder / duplicate / delete an operation, program font | §6–§9 |

## 13. Testing

Every row above gets an end-to-end test with real input through libei, reading the app through its test bus
(`ALPHALINK_TEST=1`), in `app/tests/e2e/test_interaction.py`; they run on GitHub for every push (the Rust job). The
pen cannot be faked there: libei (1.5) has no tablet device, and headless GNOME Shell reads no `uinput` devices. Its
§10 logic is tested through a test-only switch on the bus (`ForceSource("pen")`: every press counts as a pen), and
the pen itself on the Surface with this checklist (Automatic layout, the Type Cover off):

1. Draw a few lines with the pen tip: the snap marker follows the hovering pen; the layout stays as it was (the
   buttons do not grow or shrink) after switching between pen and finger several times.
2. The zoom buttons and the Keypad button are shown after the first pen press; − zooms out, + in, Fit fits.
3. Tap the value chip and an operation's number with the pen: the keypad opens; type a value; ✓ sets it.
4. Hold the pen tip on a line for about a second: the drawing's menu opens (Edit Values…, Delete, Zoom Here,
   Fit Part). Press the barrel button on a line: the same menu.
5. Pick an element at a corner with the pen: it takes the nearer line (10 px reach), not both.
6. Hold the pen on Send to the Lathe for 1.5 s: the confirmation opens; Cancel.
7. Turn the pen over (eraser end) and tap a line: it selects it, nothing is erased.

Already covered: Ctrl+wheel at the pointer, Ctrl+0 / Ctrl+−, wheel pan, Shift+F10 and Delete, Escape closing
Preferences and the keypad, Space held to send, a mouse click unlocking the touch lock, the Automatic layout
following finger and mouse, right click → Delete, the keyboard exercise (points, fillet, chamfer by keys), undo and
redo, search and Go to N.

To add with each change (names as they will appear):

- `KeyboardCanvas.test_ctrl_arrows_pan` · `test_view_follows_the_cursor` · `test_z_zooms_at_the_cursor`
- `KeyboardDrawing.test_tangent_by_enter_and_tab` · `test_blend_o_finishes_by_keys` · `test_bracket_keys_walk_the_profile`
  · `test_pick_contour_with_space`
- `KeyboardOperations.test_alt_arrows_reorder` · `test_ctrl_d_duplicates` · `test_delete_key_deletes_with_undo`
- `KeyboardSimulation.test_f5_plays_and_pauses` · `test_ctrl_brackets_step_passes` · `test_page_keys_on_the_slider`
- `KeyboardProgram.test_enter_selects_the_operation` · `test_search_selects_the_operation` · `test_ctrl_plus_sizes_the_font_and_is_remembered`
- `KeyboardPages.test_alt_numbers_switch_pages`
- `MouseCanvas.test_middle_drag_pans` · `MouseDrawing.test_more_menu_reaches_text_and_measure`
- `NumbersEverywhere.test_edit_values_keypad_by_finger` · `test_new_part_keypad_by_finger`
- `ShortcutsWindow.test_listed_keys_do_what_they_say` (each listed key, its effect read on the bus)

## 14. Order of work

1. §12 corrections and the Delete scope (small, removes wrong documentation).
2. §6 operation keys and §9 page keys (accelerators only).
3. §7 simulation keys and the slider's page step.
4. §8 program caret, selection and font.
5. §3 keyboard pan and cursor-follow; `z` at the cursor; middle drag.
6. §4 / §5 construction by keys, `[` `]`, pick with Space; More menu items.
7. §11 keypad for every number field.
8. §10 pen (needs the most care on the Surface itself).
