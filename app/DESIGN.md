# AlphaLink CAM (GNOME): design

Status: **design draft 1** (2026-10-12).
- M0 done: `alphacam/model` (project, `.alcam` JSON), `alphacam/io/sdg.py` (SDG import), `alphacam/io/prg_parse.py`,
  `python -m alphacam dump FILE.PRG`.
- M1 done: `alphacam/post/alphat_prg.py` posts the tool block (plus move/rapid and dwell), finish pass and area clear;
  MET2 (mm and inch), AREA1 and MOVE_RAPID are byte-identical to ALPHA-T. `python -m alphacam post PART.alcam -o P.PRG`.
- M1+ (2026-10-13): arcs, sloped lines, offsets, internal finish/area (#127 15/7), several operations and mid-program
  tool changes; 13 golden samples byte-identical. The PRG contour is the imaginary tool-tip path (RE_NOTES Progress 27).
- M4 post part (2026-10-13): face clear, grooving, drilling, threading and area pre-finish; all 18 ALPHA-T samples
  are byte-identical (RE_NOTES Progress 28). Drawing tools and the remaining options are still to do.
- M2 shell (2026-10-13): `python3 -m alphacam_gtk [PART.alcam|DRAWING.SDG]` opens a part, previews every operation
  on the lathe canvas (pinch/drag/fling/double-tap fit, wheel, Ctrl+wheel, tap or click a toolpath to select its
  operation), shows the program with the selected operation highlighted (pinch changes the font size), and exports
  the PRG. Touch density switches automatically (TOUCH.md 3). Examples in `app/examples/`. Not yet: editing operations,
  op reorder and bottom-sheet editors (M4), simulation, machine page (M3).
- M3 (2026-10-13): `alphacam/link/serial_link.py` (9600 7E2, wait for DC1, XON/XOFF within a 4-byte chunk, per-char
  delay, receive to the 2nd %, Manual Ops load parity, loopback; pty tests) and the **Machine** page: port, live
  modem lines, hold-to-send with confirmation, transfer dialog with cancel, Manual Ops backup / restore (hold +
  warning), loopback test, transfer logs in ~/.local/state/alphalink-cam/logs, and a touch lock (also automatic
  during transfers). Not yet tried against the lathe from the app.
- M4 part 1 (2026-10-13): editing. `alphacam/commands.py` undo stack (JSON snapshots); operation editor pane with
  rows generated per operation type; add (menu, sensible defaults), delete (Undo toast), move up/down; undo/redo
  (Ctrl+Z / Ctrl+Shift+Z, header buttons); Save / Save As (.alcam) and an unsaved-changes prompt. Next: drawing tools.
- M4 part 2 (2026-10-13): drawing. `alphacam/drawing.py` (snap to ends/axis/grid, Z/X/45° direction lock, lines,
  fillet, chamfer, delete, contour pick ordering); Part page tools Select / Line / Fillet / Chamfer (Adw.ToggleGroup
  bar), command entry `X,Z` / `@dX,dZ`, fillet/chamfer size chips in a popover at the corner, element select +
  delete (Undo toast), "Pick Contour on the Drawing" with numbered badges, New Part dialog (units, billet).
  A part drawn in the app (MET2 + R5/R3 fillets) posts byte-identical to the ALPHA-T ARC sample.
- Simulation and keypad (2026-10-13): transport bar under the canvas (first / previous pass / play / next pass /
  all, scrub), not-yet-cut moves faded, tool marker, label with operation, pass and PRG line, Program page follows;
  on-screen keypad for X,Z (ABS/INC, big keys) shown in touch mode.
- M5 packaging (2026-10-13): `app/pyproject.toml` (entry points `alphalink-cam`, `alphacam`), desktop file,
  metainfo (validated), icon, Flatpak manifest `app/build-aux/flatpak/` (GNOME 51 runtime, pyserial pinned by
  sha256, `--device=all` for the serial port). Built and installed on Ubuntu 26.04 with flatpak-builder 1.4.8:
  `flatpak-builder --user --install-deps-from=flathub --jobs=8 --install build-dir
  app/build-aux/flatpak/io.github._0wn3r.AlphaLinkCAM.json`; examples in /app/share/alphalink-cam/examples.
- Translations (2026-10-13): gettext domain `alphalink-cam`; all UI strings marked; Ukrainian complete
  (`app/po/uk.po`, sources of truth `app/po/uk_translations.py`). The language follows the desktop; `ALPHALINK_LANG=uk`
  forces it. After changing UI strings run `python3 tools/update_translations.py` (needs gettext); a test fails if
  a string has no Ukrainian translation. Messages from the core (post errors) are still English.
- Tests: `cd app && python3 -m unittest discover -s tests -t .` (or `python3 -m pytest`).

Working name: *AlphaLink CAM*. App ID: `io.github._0wn3r.AlphaLinkCAM` (a GApplication id element may not start with a
digit, so `0wn3r` becomes `_0wn3r`, as Flatpak does).
Stack: Python 3.12+, PyGObject, GTK 4.16+, libadwaita 1.7+ (Adw.ToggleGroup), pyserial. Target: a Microsoft Surface Pro tablet running
**Ubuntu 26.04 LTS** (decided 2026-10-13; native: `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-serial`) (GNOME, Wayland), used with touch and pen as well as keyboard and mouse (touch design:
`app/TOUCH.md`); packaged as a Flatpak.

## 1. Purpose and scope

The app is a modern replacement for the DOS **ALPHA-T / AlphaLink V1.2** lathe CAD/CAM. It targets a Harrison Alpha 400T
(FANUC Power Mate 0, Harrison macro package B703-0703). It covers the same job end to end:

1. Draw a 2D turned part (X diameter, Z), or import it.
2. Define the billet, tools and an ordered list of operations.
3. Generate and preview the toolpaths.
4. Post an Alpha-T `.PRG` that the control's macros accept.
5. Send and receive over RS232 (CAM program, Manual Ops backup/restore).

**Non-goals for v1:** milling, 3D, general-purpose CAM, and a G-code editor for hand-written programs. The control's macro package
must only ever receive Alpha-T-shaped programs (see section 2).

Later, as optional post-processors: DOS ALPHACAM V1.81 `.600` and AlphaWin-S `#500` format, for older Alpha machines.

## 2. Hard constraints (from the reverse engineering)

| # | Constraint | Source |
|---|---|---|
| C1 | **Byte compatibility.** Output must match what ALPHA-T writes for the same input. Programs not made by Alphalink T corrupt the T macros (manual warning). Conformance is tested against golden samples (section 9). | RE_NOTES Progress 16 |
| C2 | Line format of the CAM transfer: **9600 baud, 7E2, XON 0x11 / XOFF 0x13**, file sent unchanged (CRLF kept). | `data/line_settings.json` |
| C3 | Manual Ops: Save = receive 7E2 until the 2nd `%`. Load = 7E2 with even parity in bit 7. | Progress 22 |
| C4 | **Scaling.** In the PRG, Z, feed (`F`, `#130`) and thread pitch are written ×2; X (diameter) is 1:1. The SD1/ISO preview is unscaled. Default `z_scale = f_scale = 2`, kept in the machine profile, **unverified on the control**. | Progress 15, 16, 25 |
| C5 | The PRG writes the **programmed contour**, not a tool-nose-compensated path (finish pass: PRG `X40.Z0.`; SD1 `X40.8 ... Z0.4`). Nose compensation is presumably done by the macros. Keep both paths in the model. | MET2 samples |
| C6 | Numbers always carry a decimal point (`100.`, `0.3`), with trailing zeros stripped. mm: max 3 decimals; inch: max 4. Line endings are CRLF. | samples |
| C8 | **Manual turret with tool memory.** A tool block is an operator tool change at the tool change point; `#122` selects the control's stored offsets, so the app's tool numbers must match the control's tool memory slots. The tool library page should say which slot each tool uses. | user, 2026-10-13 |
| C7 | Handshake: the control needs CTS/DSR (and probably CD) active. The app should show the live modem lines and refuse to start a send while DSR is off, with an override. | Progress 25 |

## 3. Architecture

```
app/
  alphacam/                 pure Python, no GTK imports (unit-testable, CLI-usable)
    model/                  dataclasses + JSON (de)serialisation
      project.py            Project, Units, Billet, Chuck, MachineProfile
      geometry.py           Point(x_dia, z), Line, Arc, Contour, snapping helpers
      tools.py              Tool, ToolLibrary (import ATOOLIB.TL1/TL2 later)
      operations.py         one dataclass per op (section 4.3)
    toolpath/               generators: Operation + geometry + tool -> Toolpath
      contour.py            offsets, nose radius, profile clipping
      area.py face.py finish.py groove.py thread.py drill.py moves.py
      toolpath.py           Toolpath = list[Move(kind=RAPID|FEED|THREAD|DWELL, x, z, f, ...)] grouped in Passes
    post/
      base.py               PostProcessor interface: post(project, toolpaths) -> bytes
      alphat_prg.py         Alpha-T .PRG (C1, C4, C5, C6), spec in section 5
      alphat_sd1.py         ISO/SD1 listing (human-readable, unscaled, G7x cycles)
      v181_600.py           later
    io/
      sdg.py                import ALPHA-T/ALPHACAM .SDG drawings (from tools/sdg_dump.py)
      dxf.py                DXF import (from tools/dxf2sdl.py)
      prg_parse.py          PRG -> moves, for preview and round-trip tests
    link/
      serial_link.py        port open, 7E2, XON/XOFF, char/line delay, modem lines (from tools/alphalink.py)
      transfers.py          SendProgram, ReceiveManOps, LoadManOps, Loopback, Listen
  alphacam_gtk/             GTK4 / libadwaita front end
    application.py          Adw.Application, actions, shortcuts
    window.py               main window (section 6)
    canvas/                 lathe view widget (Gtk.Widget + snapshot / cairo)
    pages/                  drawing.py cam.py program.py machine.py tools.py
    dialogs/                op editors, billet, preferences, transfer progress
    resources/              .blp UI files (Blueprint), icons, gresource
  tests/                    golden + unit tests
  data/                     desktop file, metainfo, gschema, icons
  build-aux/flatpak/        manifest
```

Rules:

- `alphacam` never imports `gi`. The UI only reaches the model through **commands** (undo/redo stack) and listens for
  change signals. A small observable wrapper emits `changed(path)` and is bridged to GObject signals in the UI layer.
- All generation is pure and deterministic: project in, bytes out. This is what allows golden testing against ALPHA-T.
- Long jobs (toolpath regeneration for many passes, serial transfers) run in worker threads. Results come back on
  the main loop via `GLib.idle_add` or `Gio.Task`.

## 4. Data model

### 4.1 Project (native file `*.alcam`, JSON, versioned)

```json
{
  "format": "alcam/1",
  "units": "mm",
  "machine": "harrison-alpha-400t",
  "billet": {"diameter": 100, "length": 100, "face_allowance": 0.8},
  "reference": {"g92_x": 163.3803, "g92_z": 120},
  "tool_change": {"x": 100, "z": 50},
  "program_number": 10,
  "tools": [{"number": 3, "name": "55 Deg Finisher 0.4R", "type": "turn", "nose_radius": 0.4,
             "angle": 55, "hand": "R", "default_feed": 0.15}],
  "geometry": [{"type": "line", "a": [0, 0], "b": [40, 0]}],
  "operations": [{"type": "finish", "tool": 3, "start": [120, 5], "contour": ["g0", "g1"], "feed": 0.15}]
}
```

- Coordinates are X as **diameter** and Z with 0 at the finished face, negative into the part (ALPHA-T convention).
- Billet, chuck jaws and the G92 reference follow the SDG settings block (`tools/sdg_dump.py`), so SDG import is lossless.

### 4.2 Machine profile (`machines/*.json`, read-only presets + user copies)

Contents: post id, `z_scale`, `f_scale`, decimals, line settings for each transfer type (from `line_settings.json`),
max spindle speed, tool stations, header template, and the `#127` op-kind table. This is where C4 becomes a setting
rather than a hard-coded constant.

### 4.3 Operations (parameters mirror the ALPHA-T menus, so users of the DOS program feel at home)

| Op | `#127` | Parameters (ALPHA-T menu) |
|---|---|---|
| Tool change | 1 | tool, tool change X/Z, feed |
| Area clear | 6 (7 internal; 8 with FACE direction) | start, contour, depth of cut, offset X/Z, feed, towards/away, front/face, pre-finish yes/no |
| Face clear | 8 | start, contour, depth of cut, offset X/Z, feed, towards, pre-finish |
| Grooving | 9 (17 finish mode) | start, groove, datum left/right, finish offset X/Z, peck depth, stepover, tip width, feed, full retract, external/internal, front/back, Z shift/repeat, area/finish, dwell revs |
| Finish pass | 14 | start, contour, feed, offset |
| Drilling | 18 | start X/Z, depth, peck, feed, dwell |
| Threading | 20 | start, outer/inner diameter lines, number of cuts, cut depth/data, lead in (default 5) / lead out (default 0), pitch (> 0, V1.21), angle, external/internal, towards, spring pass (own block, 21) |
| Dwell | - | revs (PRG `G4P<revs*1000>`) |
| Move / rapid to point | - | X, Z (appended to the current tool block, or the empty block after an operation) |

## 5. Alpha-T PRG post (spec, from samples)

Export also writes ALPHA-T's `.SD1` listing next to the `.PRG` (`alphacam/post/alphat_sd1.py`, rules in
docs/RE_NOTES.md Progress 34), so the files can go back into the DOS/Windows ALPHA-T tools.

```
%                                   CRLF everywhere
O2
#132=<program number>
#140=0
IF[#1006EQ1]GOTO[[#129+1]*10]       restart logic, constant
IF[#1005EQ1]GOTO[#129*10]
#131=#131-1
IF[#1009EQ1]GOTO[#129*10]
#131=0
M71
N10                                 block k = 1, 2, 3 ... label N<k*10>
#121=1  #122=<tool>  #123=<tc X>  #124=<tc Z * zs>  #127=1  #129=1  #130=<feed * fs>
M66
IF[#140EQ1] GOTO 502
M63
[move/rapid-to-point moves: M62 G1..F / M60 G0.. M61]
N502
N20                                 operation block
#120=<passes> #121=1 #122=<tool> #123=<start X> #124=<start Z*zs> #125=<limit X> #126=<limit Z*zs>
#127=<op kind> #128=1 #129=<block no> #130=<feed*fs | pitch*fs for thread> #131=#131+1
M66
IF[#140EQ<b>] GOTO 50<b+1>
M63
per pass: [N5nn] #121=n M66 [IF[#140EQn] GOTO 5nn] M63 / M60 G0 ... M61 / M62 G1|G32 ... F / M64 after the last
N50<b+1>
...
N<last> M65
%
```

`#125/#126` are the billet limits (SD1 `G115 X Z`). The exact placement of the per-pass labels differs per op. Each op
gets a **fixture test** that pins it down against its sample before the generator for it is called done.

## 6. User interface (GNOME HIG, libadwaita)

```
┌ AdwApplicationWindow ───────────────────────────────────────────────────────────────┐
│ HeaderBar: [≡ ops sidebar]  Drawing | CAM | Program | Machine (AdwViewSwitcher)  [⋮]│
├─────────────────────┬───────────────────────────────────────────────────────────────┤
│ Operations          │                                                               │
│  1 ⚙ Tool 3 55°  ⋮  │        lathe canvas: billet, chuck jaws, contour,             │
│  2 ▤ Area clear  ⋮  │        toolpaths per op (colour = op), tool outline,          │
│  3 ✎ Finish      ⋮  │        cursor readout  X 40.000  Z -25.000                    │
│  4 ⟂ Groove      ⋮  │                                                               │
│ [+ Add operation ▾] │  ─── simulation: ◀ ▶ ▮▮ ──●────────── pass 3/26 ───          │
├─────────────────────┴───────────────────────────────────────────────────────────────┤
│ AdwBanner: "Z and feed scaling ×2 not yet verified on the control"   [Details]      │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

- **Window:** `AdwApplicationWindow` > `AdwToolbarView` > `AdwNavigationSplitView` (sidebar = operations, content =
  `AdwViewStack` pages). An `AdwBreakpoint` collapses the sidebar below 720 px, so the layout works on a small shop laptop.
- **Drawing page:** canvas plus a tool palette (line XZ, arc, chamfer, fillet, trim, delete). Keyboard-first entry like
  ALPHA-T: type `40,-25` then Enter in a command entry under the canvas. Snaps to end, mid, centre and grid; zoom and pan
  with mouse, touchpad and touch. Billet and chuck are set from an `AdwDialog`.
- **CAM page:** the operations list is the sidebar (`GtkListView`, drag to reorder, `AdwActionRow` with a type icon and a
  summary such as "26 passes, 2 mm"). Selecting an op opens its editor in an `AdwBottomSheet` (narrow) or a side pane
  (wide). Editors use `AdwPreferencesGroup` with `AdwSpinRow`, `AdwComboRow` and `AdwSwitchRow`. Contour, groove and
  thread lines are picked on the canvas (pick mode with a hint toast). Toolpaths regenerate live, debounced.
- **Program page:** `AdwTabView` with *PRG* (what is sent) and *SD1/ISO* (readable). `GtkSourceView 5` read-only, plus
  a custom FANUC language file. A diff against the last sent version, and Export.
- **Machine page:**
  - Port `AdwComboRow` (udev-enumerated, `/dev/ttyUSB*`, `/dev/ttyS*`).
  - Live modem-line indicators (CTS, DSR, CD, RI) polled at 5 Hz.
  - Buttons: **Send CAM program**, **Backup Manual Ops (receive)**, **Restore Manual Ops**, **Loopback test**.
  - A transfer runs as an `AdwDialog` with a progress bar, byte count, XON/XOFF state and Cancel. The outcome is an `AdwToast`.
  - Step-by-step instructions for the control ("CAM menu → 4 → arrow") are shown next to each action.
- **Tools page:** tool library list with an editor (number, type, nose radius, angle, width, hand). Imports ATOOLIB.TL1/TL2
  (once decoded) and AlphaWin tool files.
- **Preferences** (`AdwPreferencesDialog`): units, default machine profile, post scaling (advanced, behind a
  confirmation), serial delays, theme.
- **Safety UX:**
  - Never auto-send.
  - A send needs an explicit confirmation showing the program number, tool list and run time estimate.
  - The scaling banner stays until the user marks the profile "verified on machine".

## 7. Serial link

- pyserial in a worker thread, port opened with DTR and RTS asserted (as `alphalink.py` does).
- Honours XOFF/XON in software (0x13/0x11, with the 0x93 variant accepted), plus optional per-character and per-line delays
  (the V1.21 notes report fast PCs overrunning the control).
- Receive stops at the 2nd `%`, with an inactivity timeout. Raw bytes are saved before any decoding.
- Flatpak needs `--device=all` (serial ports). Outside Flatpak the user must be in the `dialout` group; the app
  detects `EACCES` and shows how to fix it.
- Every transfer writes a timestamped log (bytes, timing, modem lines) for diagnosis.

## 8. Toolpath engine

- 2D only, in the XZ half-plane, working in radius internally and converting X to diameter at the edges.
- Contour offsetting with our own line/arc offset code; `pyclipper` as a fallback for polygonal area clearing.
- Each generator outputs passes the same way ALPHA-T does (pass count, order, retract moves). Exact ALPHA-T behaviour
  is learned by **differential testing**: draw the same part in ALPHA-T under DOSBox-X (`tools/atsession.sh`,
  `tools/mk_at_script.py`), post it from our model, and diff the results. Every new behaviour gets a new sample.

## 9. Testing

| Level | What |
|---|---|
| Golden | For each `samples/alphat_v12/*.SDG`: import SDG, rebuild the ops from metadata, post, and compare the PRG byte for byte (and the SD1). |
| Oracle | Optional CI job (local only, needs the vendor files): random small parts → ALPHA-T in DOSBox-X → diff against our output. |
| Unit | Geometry offsets, number formatting (C6), scaling (C4), serial framing (7E2 parity table) using a pty pair. |
| Hardware | Checklist in `docs/ALPHA-T_STATUS.md` (read-only Save first, CAM load without cycle start). |

## 10. Milestones

| M | Content | Done when |
|---|---|---|
| M0 | Repo skeleton, model and JSON, SDG import, PRG parser | samples load and render in a CLI dump |
| M1 | Alpha-T post for finish + area clear + tool change | MET2 and AREA1 golden tests are byte-identical |
| M2 | GTK shell: window, canvas, ops list, program page | a part opens, previews and exports a PRG |
| M3 | Machine page + serial transfers | loopback and pty tests pass; first read-only Save on the lathe |
| M4 | Remaining ops (face, groove, thread, drill, dwell, moves) and drawing tools | all golden tests pass |
| M5 | Flatpak, metainfo, translations, docs | installable build |

## 11. Open questions

1. ~~Is Z/feed ×2 real for the control (C4)?~~ Yes: the control shows tool start Z50 for `#124=100.` and feed 0.15 for
   `#130=0.3` (2026-10-13, RE_NOTES Progress 29). Thread pitch not yet seen on the control.
2. Who applies tool-nose compensation: the macros (C5 suggests so) or the CAM?
3. Meaning of `#120..#140`, `M60..M71` and `#1005/#1006/#1009` (restart switches?). This needs the B703-0703 macro listing or
   a Manual Ops / macro dump from the control.
4. Is `O2` constant, or does it depend on the program store slot?
5. Inch mode: values are converted with 4 decimals, but `F0.3` was written unchanged in the inch sample. Check feed units.
6. ATOOLIB.IM1/IM2 tool image format (for tool graphics).
7. Whether ALPHA-T's pass-distribution maths (depth of cut → number of passes) must be bit-exact, or whether the macros
   tolerate any pass set. Assume bit-exact until proven otherwise (C1).
