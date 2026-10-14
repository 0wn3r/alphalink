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
- **Program**: the program text exactly as it goes to the lathe. Ctrl+F searches it; N10 goes to block 10.
- **Sheet**: the setup sheet: tools, operations and every tool move as text, like ALPHA-T's print. Save as Text writes it to a file. It shows each block's restart number (#140) and any tool warnings.
- **Tools**: the tool library (tool memory slots of the control, nose radius, type, default feed).
- **Machine**: sending programs, Manual Ops backup and restore, and the cable test.

The operations list is on the left. Tap an operation to select it: its toolpath is highlighted and its editor opens.

## Files

- **Open** (Ctrl+O) reads:
  - AlphaLink parts (.alcam), with drawing and operations;
  - ALPHA-T drawings (.SDG). ALPHA-T keeps the operations only in the .SD1 file next to the drawing, so the app rebuilds them from there and tells you whether the result matches the .PRG next to it. If the .SD1 is not found, use Choose .SD1… in the message;
  - DXF drawings: lines, arcs and polylines, drawn with the spindle axis along the DXF X axis.
- **Save** (Ctrl+S) / **Save As** (Ctrl+Shift+S) write the .alcam part.
- **Export PRG** (Ctrl+E) writes the .PRG program and, next to it, the .SD1 listing with the same name, as ALPHA-T does. With Preferences ▸ Also save the ALPHA-T drawing, it writes the .SDG drawing (and .SDT) too. It remembers the folder and suggests the part's name cut to 8 characters.
- **Save as ALPHA-T Drawing…** (main menu) writes the drawing as an .SDG that DOS ALPHA-T can open (names of up to 8 characters). Operations are not part of an ALPHA-T drawing.

## Drawing

The tools are in the bar under the drawing.

- **Select**: tap an element to select it; **Delete** (or the bin button) removes it.
- **Edit Element** (the pencil next to the bin, while an element is selected) changes a line's or arc's end points, and an arc's radius, by typed values. Ends of other elements that met it move too, so the profile stays joined; switch that off in the dialog to move only this element.
- **Handles**: a selected element shows its ends; drag one to move that point (under a finger a loupe shows it; slow moves drag finely).
- **Long press or right click** on an element opens Edit Values…, Delete, Zoom Here and Fit Part. The Menu key or Shift+F10 opens it at the cursor.
- **Line**: tap points, or type coordinates in the entry and press Enter:
  - `X,Z` is an absolute point, for example `40,-25`;
  - `@dX,dZ` is relative to the last point, for example `@0,-25`.
  - Taps snap to end points, the axis and a 1 mm grid, and lock to Z, X or 45 degrees.
  - Tapping the first point again closes the profile. With a finger, the chip on the last line shows its end; tap it to correct the point on the keypad.
  - **Done** or **Esc** finishes the line; the next tap starts a new one.
  - In touch mode the keypad button opens a big number pad for X and Z.
- **Arc**: three points: start, a point on the arc, end.
- **Fillet**: tap a corner between two lines, then pick a radius or type one.
- **Chamfer**: tap a corner, set the angle (30°, 45°, 60° or any), then pick a size or type one. The size is measured along the line that runs along Z, the angle is between the chamfer and that line: 2×30° on a shoulder is 2 mm along the diameter and 1.155 mm down the face.
- **Corner**: tap two lines to extend or trim them until they meet.
- **More** (ALPHA-T's other drawing commands): a banner says what to tap; where there are several answers they are shown dashed and you tap the one you want. Esc or Cancel stops.
  - **Circle…**: centre X, Z and radius (two half arcs, split at the 3 o'clock point as ALPHA-T does).
  - **Tangent**: tap a point (or a line end) and an arc, or two arcs.
  - **Tangent at an Angle…**: tap an arc, give the angle (0° towards -Z, 90° towards +X).
  - **Parallel Line…**: tap a line, give the distance (1.5 × as long, as in ALPHA-T).
  - **Split Arc**: tap an arc where it should be split.
  - **Blend Arc…**: tap two points, lines or arcs, give the radius, tap the arc you want.
  - **Mirror Image**: as in ALPHA-T, copies the whole drawing mirrored about the spindle axis (the lower half), then asks whether it is correct; No takes it back.
  - **Standard Thread…**: on the selected diameter line, or tap the line when asked. Pick the standard (ISO metric, or UNC / UNF inch threads, converted on a mm part), the size (M6 to M64, or 1/4" to 2") and the pitch (coarse first); the line goes to the nominal diameter, the minor-diameter line is drawn under it, a relief groove with DIN 76 proportions is cut at its back end (it can be switched off), and a Threading operation with its tool change is added.
  - **Chamfer Outside Corners…** / **Round Outside Corners…**: one size on every outside corner of the profile at once; change single corners afterwards with Chamfer or Fillet.

## Operations

Each operation has a **tool** (the tool memory slot on the control), a **tool start** point (where the tool goes before and after the cut), and a **feed** per revolution. Most operations follow a **contour**: the drawing elements in cutting order. Pick Contour on the Drawing, then tap the elements in order and press Done.

New operations take the outer profile, not thread or construction lines; Grooving takes the groove and Threading the two thread lines. Adding Grooving, Threading or Drilling also adds a Tool Change when the tool in the turret does not suit them. Tool warnings show as a sign on the operation and on the setup sheet.

- **Area Clear**: roughing with passes along Z.
  - Depth of cut (per pass, on radius), Offset X / Z (finish allowance left on the part).
  - External or internal (boring), towards the chuck.
  - Front / face: with Front off the passes go across the face instead, like Face Clear.
  - Pre-finish pass: a finish pass along the contour after the roughing, in the same operation.
  - Follow contour: each pass also feeds up the step it ends at, back to where the previous pass stopped. Retract distance: how far each pass pulls off (default 1 mm).
- **Finish Pass**: one pass along the contour. Offset X / Z leaves an allowance; external, internal, or Face (finishing a face: the pull-off goes towards the axis).
- **Face Clear**: roughing with passes across the face, towards the axis. Depth of cut per pass along Z, offsets, and an optional pre-finish pass.
- **Grooving**: plunges along the drawn groove profile.
  - Tip width, stepover, peck depth (0 = plunge in one go), full retract after each peck, dwell at the bottom.
  - External, or internal for a groove in a bore. Finish offset X / Z leaves an allowance on every side of the groove.
  - Datum left or right: which corner of the tip is the tool reference.
  - Z shift and repeats: repeat the plunges shifted along Z.
  - Mode area (plunges) or finish (one pass along the profile).
  - Front off: face grooving, for a groove drawn into a face: plunges along -Z, stepping outwards from the smallest diameter.
- **Threading**: pick the outer and inner diameter lines of the thread.
  - Pitch, first cut depth (cut n goes to first × √n), or Number of cuts instead.
  - Lead in (default 5 mm in front of the thread), lead out (past the end), external or internal, spring passes (more cuts at the final diameter).
  - Infeed angle: 0 cuts straight in; with an angle (e.g. 30) each cut starts further towards the chuck so the tool cuts on one flank.
- **Drilling**: on the axis, from the start Z to the hole bottom Z, with peck depth (0 = no pecking) and dwell at the bottom. Retract distance: how far it backs off after each peck (default 2 mm). Full retract: out to the start Z after every peck.
- **Tool Change**: the next operations use this tool; the tool goes to the tool change point for the change (the turret is manual).
- **Dwell**: wait a number of spindle revolutions.
- **Move / Rapid**: a feed or rapid move to a point.

Keep **Towards the chuck** on. ALPHA-T's AWAY programs are unsafe (they cut past the shoulder, into the diameter or start inside the thread), so the app refuses them and says why. A contour picked from the chuck end is cut as if picked from the front: ALPHA-T's own programs for that only skim the face or make one cut.

Use the arrows in the editor header to move an operation up or down, and the bin to delete it (Undo brings it back). If an operation cannot be made into a program, a banner says which one and why, and Export is disabled until it is fixed.

Drag a row by its handle to reorder the operations; **Duplicate Operation** at the end of the editor copies one.

Before export the app checks every move. A cut into the part, a move into the chuck jaws or the tailstock, or a dive steeper than the tool clears shows a banner with **Show**, a red ring on the drawing and a sign on the operation. It only warns: export still works.

## Simulation

The buttons under the drawing step through the toolpath: first move, previous pass, play / pause, next pass, show all. The label shows the move, the operation, the pass and the program line. The 1× button changes the speed, the button next to it jumps to the start of the selected operation, and tapping a toolpath jumps there.

## Sending to the lathe

Connect the serial cable, then on the Machine page:

- **Send CAM program**: on the lathe press the CAM key, choose 4 "LOAD CAM FILE" and touch the ← arrow. Then hold **Send to the Lathe** in the app until it fills, check the summary and confirm.
- **Back up Manual Ops**: start the backup in the app first. Then on the lathe, in Manual Ops, touch the ops file icon, choose 1 "OP. SAVE/RESTORE" and touch the → arrow.
- **Restore Manual Ops**: on the lathe, same menu, touch the ← arrow, then hold **Restore to the Lathe…** in the app and choose the file. Only restore files saved from this lathe.
- **Cable Test**: unplug the cable from the lathe, bridge pins 2 and 3 of the adapter, and run the loopback test.

The app never starts the machine. Always check the program on the control before Cycle Start, and run new programs with the feed override turned down.

## The program file

The .PRG sets variables for the lathe's Harrison macros and calls them with M-codes. In it every Z, feed and thread pitch is twice the real value; the macros halve them. Items marked (inferred) come from comparing ALPHA-T programs.

- **#132** program number shown on the control. **O2** is always the same.
- **#140** restart point: 0 runs from the start, n jumps to the restart of pass n (inferred).
- **#1005, #1006, #1009** control inputs that restart or skip a block (inferred). **M71** metric, **M70** inch.
- **#120** number of passes, **#121** pass number, **#122** tool memory slot.
- **#123 / #124** tool start X and Z×2 (in a tool change block: the tool change point).
- **#125 / #126** limits: lowest X minus 4× nose radius, lowest Z×2 minus 2× nose radius.
- **#127** block kind: 1 tool change, 6/7 area clear (internal), 8 face clear, 9 grooving, 10/11/12 pre-finish, 14/15/16 finish (internal, face), 17 grooving finish mode, 18 drilling, 20 threading, 21 spring passes.
- **#128** always 1, **#129** block number, **#130** feed×2 (threading: pitch×2), **#131** operation counter (inferred).
- **M60 … M61** around each rapid **G0**, **M62** before feed moves **G1 / G2 / G3** (I as a diameter, K×2), **G32** thread cut with **U W** pull-out, **G4 P** dwell (revolutions×1000).
- **M63** start of a pass, **M64** end of an operation, **M65** end of the program, **M66** start of a block or pass (inferred).
- The moves are the imaginary tool tip path: the nose radius is already allowed for.
- The .SD1 next to it is ALPHA-T's record of the operations in real units (G70 finish, G71 area/face, G75 grooving, G76 threading, G74 drilling, G99 tool change); opening an .SDG reads the operations back from it.

## Touch and pen

- One finger drags the view, two fingers pinch to zoom, double tap fits the part (again returns).
- A faint grid follows the zoom (1, 2 or 5 × 10ⁿ mm); every fifth line is labelled at the edges, X as a diameter. Main menu ▸ Show Grid turns it off.
- Mouse: wheel pans up and down, Shift+wheel pans sideways, Ctrl+wheel zooms, Ctrl+0 fits.
- **Touch Lock** (padlock in the header) ignores touches, for example while coolant is running; hold it to unlock by touch. Mouse and keyboard keep working.
- Main menu ▸ Input Layout switches between Automatic, Pointer and Touch layouts.

## Keyboard shortcuts

- Ctrl+N new part, Ctrl+O open, Ctrl+S save, Ctrl+Shift+S save as, Ctrl+E export.
- Ctrl+Z undo, Ctrl+Shift+Z or Ctrl+Y redo.
- Ctrl+0 fit, Ctrl++ zoom in, Ctrl+- zoom out.
- Esc finishes the line being drawn, cancels contour picking or a command, or deselects, and takes the keyboard out of the X,Z entry.
- Delete removes the selected drawing element.
- Ctrl+F searches the program. Shift+F10 or the Menu key (or a right click) opens the drawing's menu.
- Ctrl+, preferences, Ctrl+? all keyboard shortcuts, F1 this help, Ctrl+W close the window, Ctrl+Q quit.
- Main menu ▸ Training Manual and Reference Manual open the full manuals (PDF) in the PDF viewer.

### ALPHA-T keys

On the Part page ALPHA-T's one-letter commands work as in ALPHA-T, also while the X,Z entry has the keyboard (a coordinate has no letters) and with the Ukrainian keyboard layout (the key marked A/Ф is A). The cursor is where the mouse last was on the drawing, or where these keys put it (a cross).

- **X** draw line X,Z: the Line tool, ready for a typed point. Typing a number does the same.
- **D** draw line D,A: type length,angle. 0° points towards -Z (9 o'clock), 90° towards +X.
- **+** the Line tool, for drawing with taps.
- **S** starts a line at the cursor.
- **G** moves the cursor to X,Z, **H** moves it by dX,dZ, **Y** by length,angle. With the Line tool the next line starts there.
- **P** jumps to the nearest point (a line or arc end, an arc centre), **I** to the nearest intersection.
- **L** or **A** finds the nearest line or arc; then **-** or Delete erases it.
- **F** radius at the corner nearest the cursor, **O** blend arc between two elements (ALPHA-T's 2 PKT RADIUS, More ▸ Blend Arc), **K** trim corner (the Corner tool).
- **M** measures X and Z between two points, **N** length and angle. Tap the points, or move the cursor and press Enter; the nearest point is used.
- **T** puts text at the cursor, **U** erases the nearest text.
- **Q** erases all of the drawing after asking (Enter erases, Esc does not). Undo brings it back.
- **Z** zooms in, **R** redraws the whole part, **V** goes back to the view before.
- **C** opens the operations menu (ALPHA-T's CAM menu).
- The arrow keys move the cursor, PgUp and PgDn make the step 10 times bigger or smaller, Home puts it in the middle of the view, End on X0 Z0, and Enter takes it as the next point.
- With Fillet or Chamfer, Enter picks the corner nearest the cursor: type the size and press Enter again. With Corner, Enter picks the line nearest the cursor.
- These keys act while the drawing has the keyboard: it takes it when a part opens, when you come back to the Part page, when you click the drawing or pick a drawing tool, and after Esc in the X,Z entry. The focus ring round the drawing shows it once you use the keys.
- Picking a contour, **3** or Enter finishes it.
- Added here (free in ALPHA-T): **E** edits the selected element, **B** is the Chamfer tool, **W** the Arc tool.
- Not mapped: Tab. F1 stays this help. Esc also clears the messages at the bottom (or main menu ▸ Clear Messages).

## Part settings

Main menu ▸ Part Settings changes the billet of the open part: diameter, length, bore (for a tube; 0 = solid bar), the chuck jaws (on the outside, as ALPHA-T draws them, or expanding in the bore), and whether the tailstock centre is shown at the front face. New Part asks for the same; these billet settings only change the picture, not the program. **Program number** is the number the control shows for the program (#132). **Program units** writes the program in mm (M71) or inch (M70) whatever the drawing's units (the .SD1 stays in the drawing's units, as ALPHA-T keeps it), like ALPHA-T's "Select Units To Store Output In"; as the drawing is the default.

## Preferences

Main menu ▸ Preferences: units, billet size and first tool for new parts, the language (after a restart), and Export ▸ Also save the ALPHA-T drawing (.SDG) next to the program.
