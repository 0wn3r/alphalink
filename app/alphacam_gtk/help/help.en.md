# AlphaLink CAM help

AlphaLink CAM makes programs for the Harrison Alpha 400T lathe (FANUC Power Mate 0 control). It replaces the old DOS ALPHA-T CAM: you draw the part, add machining operations, check them in the simulation, and send the program to the lathe over the serial cable. The programs it writes are the same, byte for byte, as the ones ALPHA-T writes.

## The basics

- **X is the diameter**, **Z is the length**. Z0 is normally the front face of the part; the part extends towards the chuck in -Z.
- On screen, +Z points right and +X points up, as you see the lathe from the front (arrows in the bottom-left corner). The cross marks X0 Z0.
- Units are millimetres (inches are possible per part, but the lathe runs metric).
- The control doubles Z, feed and pitch values. The app does this for you; always enter real values.
- Every change can be undone with **Ctrl+Z** and redone with **Ctrl+Shift+Z**.

## A typical job

- **New part** (Ctrl+N): enter the billet diameter and length.
- **Draw the profile** on the Part page with the Line tool, from the front face towards the chuck. Add fillets and chamfers.
- **Add operations** with the **+** button above the operations list: usually Area Clear for roughing, then Finish Pass.
- **Pick the contour** for each operation (button in the operation editor), set tool, feed and depths.
- **Check** the toolpaths on the Part page with the simulation buttons, and read the program on the Program page.
- **Save** the part (Ctrl+S, .alcam file) and **export** the program (Ctrl+E).
- **Send** it to the lathe on the Machine page.

## Pages

- **Part**: the drawing, the toolpaths and the drawing tools.
- **Program**: the program text exactly as it goes to the lathe.
- **Sheet**: the setup sheet: tools, operations and every tool move as text, like ALPHA-T's print. Save as Text writes it to a file.
- **Tools**: the tool library (tool memory slots of the control, nose radius, type, default feed).
- **Machine**: sending programs, Manual Ops backup and restore, and the cable test.

The operations list is on the left. Tap an operation to select it: its toolpath is highlighted and its editor opens.

## Files

- **Open** (Ctrl+O) reads:
  - AlphaLink parts (.alcam), with drawing and operations;
  - ALPHA-T drawings (.SDG). ALPHA-T keeps the operations only in the .SD1 file next to the drawing, so the app rebuilds them from there and tells you whether the result matches the .PRG next to it. If the .SD1 is not found, use Choose .SD1… in the message;
  - DXF drawings: lines, arcs and polylines, drawn with the spindle axis along the DXF X axis.
- **Save** (Ctrl+S) / **Save As** (Ctrl+Shift+S) write the .alcam part.
- **Export PRG** (Ctrl+E) writes the .PRG program and, next to it, the .SD1 listing with the same name, as ALPHA-T does.

## Drawing

The tools are in the bar under the drawing.

- **Select**: tap an element to select it; **Delete** (or the bin button) removes it.
- **Line**: tap points, or type coordinates in the entry and press Enter:
  - `X,Z` is an absolute point, for example `40,-25`;
  - `@dX,dZ` is relative to the last point, for example `@0,-25`.
  - Taps snap to end points, the axis and a 1 mm grid, and lock to Z, X or 45 degrees.
  - **Done** or **Esc** finishes the line; the next tap starts a new one.
  - In touch mode the keypad button opens a big number pad for X and Z.
- **Arc**: three points: start, a point on the arc, end.
- **Fillet**: tap a corner between two lines, then pick a radius or type one.
- **Chamfer**: tap a corner, set the angle (30°, 45°, 60° or any), then pick a size or type one. The size is measured along the line that runs along Z, the angle is between the chamfer and that line: 2×30° on a shoulder is 2 mm along the diameter and 1.155 mm down the face.
- **Corner**: tap two lines to extend or trim them until they meet.

## Operations

Each operation has a **tool** (the tool memory slot on the control), a **tool start** point (where the tool goes before and after the cut), and a **feed** per revolution. Most operations follow a **contour**: the drawing elements in cutting order. Pick Contour on the Drawing, then tap the elements in order and press Done.

- **Area Clear**: roughing with passes along Z.
  - Depth of cut (per pass, on radius), Offset X / Z (finish allowance left on the part).
  - External or internal (boring), towards the chuck.
  - Front / face: with Front off the passes go across the face instead, like Face Clear.
  - Pre-finish pass: a finish pass along the contour after the roughing, in the same operation.
- **Finish Pass**: one pass along the contour. Offset X / Z leaves an allowance; external or internal.
- **Face Clear**: roughing with passes across the face, towards the axis. Depth of cut per pass along Z, offsets, and an optional pre-finish pass.
- **Grooving**: plunges along the drawn groove profile.
  - Tip width, stepover, peck depth (0 = plunge in one go), full retract after each peck, dwell at the bottom.
  - External, or internal for a groove in a bore. Finish offset X / Z leaves an allowance on every side of the groove.
  - Datum left or right: which corner of the tip is the tool reference.
  - Z shift and repeats: repeat the plunges shifted along Z.
  - Mode area (plunges) or finish (one pass along the profile).
- **Threading**: pick the outer and inner diameter lines of the thread.
  - Pitch, first cut depth (cut n goes to first × √n), or Number of cuts instead.
  - Lead in (default 5 mm in front of the thread), lead out (past the end), external or internal, spring pass (one more cut at the final diameter).
- **Drilling**: on the axis, from the start Z to the hole bottom Z, with peck depth (0 = no pecking) and dwell at the bottom.
- **Tool Change**: the next operations use this tool; the tool goes to the tool change point for the change (the turret is manual).
- **Dwell**: wait a number of spindle revolutions.
- **Move / Rapid**: a feed or rapid move to a point.

Keep **Towards the chuck** on, and grooving **Front** on. ALPHA-T's AWAY and back-grooving programs are unsafe (they cut past the shoulder, start inside the thread or plunge through the part), so the app refuses them and says why.

Use the arrows in the editor header to move an operation up or down, and the bin to delete it (Undo brings it back). If an operation cannot be made into a program, a banner says which one and why, and Export is disabled until it is fixed.

## Simulation

The buttons under the drawing step through the toolpath: first move, previous pass, play / pause, next pass, show all. The label shows the move, the operation, the pass and the program line.

## Sending to the lathe

Connect the serial cable, then on the Machine page:

- **Send CAM program**: on the lathe press the CAM key, choose 4 "LOAD CAM FILE" and touch the ← arrow. Then hold **Send to the Lathe** in the app until it fills, check the summary and confirm.
- **Back up Manual Ops**: start the backup in the app first. Then on the lathe, in Manual Ops, touch the ops file icon, choose 1 "OP. SAVE/RESTORE" and touch the → arrow.
- **Restore Manual Ops**: on the lathe, same menu, touch the ← arrow, then hold **Restore to the Lathe…** in the app and choose the file. Only restore files saved from this lathe.
- **Cable Test**: unplug the cable from the lathe, bridge pins 2 and 3 of the adapter, and run the loopback test.

The app never starts the machine. Always check the program on the control before Cycle Start, and run new programs with the feed override turned down.

## Touch and pen

- One finger drags the view, two fingers pinch to zoom, double tap fits the part (again returns).
- Mouse: wheel pans up and down, Shift+wheel pans sideways, Ctrl+wheel zooms, Ctrl+0 fits.
- **Touch Lock** (padlock in the header) ignores touches, for example while coolant is running; hold it to unlock by touch. Mouse and keyboard keep working.
- Main menu ▸ Input Layout switches between Automatic, Pointer and Touch layouts.

## Keyboard shortcuts

- Ctrl+N new part, Ctrl+O open, Ctrl+S save, Ctrl+Shift+S save as, Ctrl+E export.
- Ctrl+Z undo, Ctrl+Shift+Z or Ctrl+Y redo.
- Ctrl+0 fit, Ctrl++ zoom in, Ctrl+- zoom out.
- Esc finishes the line being drawn, cancels contour picking, or deselects.
- Delete removes the selected drawing element.
- Ctrl+, preferences, F1 this help, Ctrl+Q quit.

### ALPHA-T keys

On the Part page, while no text field has the focus, ALPHA-T's one-letter commands work as before:

- **S** start point, **X** or **Y** draw line: the Line tool, ready for a typed point.
- **G** absolute point (`X,Z`), **H** incremental point (`@dX,dZ`).
- Typing a number starts a point in the entry; Enter draws to it.
- **F** or **O** fillet, **C** chamfer, **B** trim two lines to a corner.
- **L** or **A** erases the selected line or arc.
- **Z** zooms in, **R** redraws the whole part.
- Picking a contour, **3** or Enter finishes it.

## Preferences

Main menu ▸ Preferences: units, billet size and first tool for new parts, and the language (after a restart).
