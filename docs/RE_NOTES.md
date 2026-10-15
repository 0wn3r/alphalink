# ALPHACAM V1.81 (DOS) — RS232 / DNC protocol (reverse engineered)

> Context: ALPHACAM V1.81 (Superdraft Systems, 1995) is **lathe (turning) CAD/CAM** software for the "ALPHA" CNC lathe.
> Coordinates are X = diameter, Z = spindle axis (Z0 = front face, -Z towards the chuck). Operations are turning:
> area clear (roughing), finish pass, grooving and face clear. NC output goes through the FANUC600.600 post to *.600 programs, which ALINK sends.

Source: static analysis of ALINK.EXE (sha256 7893385576b7…) and AMANOPS.EXE (sha256 5dda8f8bbb91…)
with REA (Ghidra 12.1.4 provider) + ndisasm. Evidence bundles: ALINK.rea-evidence, AMANOPS.rea-evidence.
Addresses are REA/Ghidra linear (load seg 0x1000). ALINK behaviour verified by running the original
ALINK.EXE in DOSBox 0.74 (see "Dynamic verification"); not yet tested against a real CNC.

## Programs that touch the serial port
| Program | Role | Library |
|---|---|---|
| ALINK.EXE (menu opt 2) | Send NC program (*.600 from \ALPHACAM\PROGRAMS) | Blaise Computing Asynch (1988) |
| AMANOPS.EXE (menu opt 5) | SAVE = receive from CNC, LOAD = send to CNC (*.MOP in \ALPHACAM\MANOPS) | same |

UART is driven directly (own IRQ handler, 8250/16450). Line format is set with BIOS INT 14h AH=00.
Port base from BIOS data area 40:0000 (COM1/COM2), IRQ auto (COM1→IRQ4, COM2→IRQ3).

## Configuration
* Only the port is user-configurable: `PORT.CFG` in the working dir, first byte `'1'`=COM1, `'2'`=COM2
  (written by the `<!>` "Select Port" screen). Everything else is hard-coded.
* Comm record layout (0x3C bytes, far data seg 0x920): +0 name[12], +0x0C port(0/1), +0x0E baud code,
  +0x10 parity, +0x12 data code, +0x14 stop code, +0x16 unused(0), +0x18 CTS flag, +0x1A XON/XOFF flag, +0x1C path.

| Record | Baud | Data | Parity | Stop | Flow |
|---|---|---|---|---|---|
| ALINK "SDRAFT" | 9600 | 8 | even | 2 | XON/XOFF (both ways), CTS off |
| AMANOPS "SAVE" (receive) | 9600 | 8 | even | 2 | XON/XOFF, CTS off |
| AMANOPS "LOAD" (send) | 9600 | 7 | even | 2 | XON/XOFF, CTS off |

Encoding (asy_bios_init_line @0x190c7): AL = (baud&7)<<5 | P<<3 | (stop&1)<<2 | (data&3),
P = 1(odd) if parity==1, 3(even) if parity==2, else 0. Baud codes 0..8 = 110,150,300,600,1200,2400,4800,9600,19200.

Modem lines while open: MCR=0x0B (DTR, RTS, OUT2 on).
Buffers: ALINK rx 20 / tx 64 bytes; AMANOPS receive rx 128 / tx 32.
Library opts (INT14-style func 4): 5=honour XON/XOFF on TX, 6=send XOFF/XON on RX fill, 7=strip bit7 (TX and RX),
8=force bit7=1 on TX (0x400), 9=require CTS. App clears 5..9 then sets 5+6 when XON/XOFF flag=1, 9 when CTS flag=1.

## Flow-control quirk (important for emulators)
Blaise ISR (0x12bab) compares the raw RBR byte: **0x11 = XON, 0x93 = XOFF**. A plain 0x13 is ignored.
When the PC throttles the CNC it sends **0x93** (XOFF) and **0x11** (XON).
→ On 8-bit framing a 7-bit-even-parity CNC's DC3 arrives as 0x93 and works; on AMANOPS LOAD (7-bit framing)
  the UART delivers 0x13, so the CNC's XOFF is probably never honoured.
RX high-water marks: XOFF at size/2 … size-size/10; XON when drained.

## ALINK send (alink_send_file @0x10744)
1. Optional argv[1] = max file size; larger file → warning (Enter continue / Esc quit).
2. fopen(file,"rb"); open port; set options as above; display "Sending data…".
3. For every byte: **0x0A (LF) is skipped**, all other bytes sent unchanged (so CRLF → CR). No leader,
   trailer, DC2/DC4 or extra '%' added — whatever is in the file is sent.
4. Retries while TX queue full; Esc aborts. At EOF waits for TX queue to drain → "End Of Transmission", Enter, close.

## AMANOPS LOAD = send (amanops_send_LOAD @0x10c1e)
Each byte b → table[b] (xlat_even_parity_bit7 @0x111c0): 7-bit char with **even parity in bit 7**;
bytes > 0x7F become 0x00. Line endings not altered. (With 7-bit UART framing bit 7 is discarded anyway.)

## AMANOPS SAVE = receive (amanops_receive_SAVE @0x111f2)
* fopen(name,"wb"); waits ("Waiting for data…") until RX queue non-empty (Esc aborts).
* Each byte written **raw** to file (no stripping, no CR/LF translation).
* Stops after the **second '%' (0x25)** received, or Esc. Empty capture → file deleted.
* Line status shown: "Parity/Overrun/Framing error detected", "Input characters lost".

## Not covered / open questions
* Live wire capture to confirm 8E2 vs CNC expectations (DOSBox serial loopback or real CNC).
* AMANOPS LOAD XOFF behaviour on 7-bit framing (likely unrecognised; needs test).
* Other EXEs (ALPHACAM, APRINT, ATOOLS, ADXF, ACOPY) contain no "COM1"/"COM2" strings or the Blaise serial library (not disassembled).

## Tools (re-notes/tools)
* `alphalink.py` — modern replacement: `send-alink`, `send-load`, `recv-save` modes reproducing the above
  (incl. 0x11/0x93 flow control; `--accept-dc3` to also honour 0x13; line settings overridable).
* `test_loopback.sh`, `test_flow.py` — self-tests over socat pty pairs (all PASS: LF drop, 2nd-'%' stop,
  even-parity table, 0x93 pauses / 0x13 ignored / 0x13 honoured with --accept-dc3; ~800 B/s at 9600 8E2).
* `run_capture.sh` + `dosbox-alink.conf` + `cnc_capture.py` — run original ALINK.EXE in DOSBox-X and capture
  the wire. `MODE=tcp` (nullmodem, byte stream + XOFF injection) or `MODE=pty` (directserial via socat,
  prints the line settings ALINK programmed using stty). Needs dosbox-x, socat, python3-serial on the host.

## Dynamic verification (original ALINK.EXE in DOSBox 0.74, 2026-10-12)
Run headless (Xvfb) with `tools/run_alink_dosbox.sh`; key input injected with `tools/KEYS.COM`
(INT 16h AH=05 keystroke stuffer; `^` = Enter). DOSBox serial1 = nullmodem client -> cnc_capture.py.
| Test | Result |
|---|---|
| LOOPTEST.600 (55 B after LF removal) | wire bytes == file with every 0x0A removed; no leader/trailer. **Confirmed** |
| BIGTEST.600 (34 KB), XOFF=0x93 after 300 B, 3 s hold | 2 bytes during hold (in-flight), resumes on 0x11, full file intact. **Confirmed** |
| same, XOFF=0x13 | 2629 bytes during hold - plain DC3 ignored. **Confirmed** |
| Line settings via directserial -> pty, `stty` while ALINK holds COM1 | 9600 baud, 2 stop bits (cstopb). **Confirmed** |
| Parity / data bits | see DOSBox-X section below: **8E2 confirmed** |
Throughput ~810 B/s, i.e. 9600 baud with 12-bit frames (consistent with 8E2).

AMANOPS (same harness; menu: `~` = Down arrow in KEYS.COM, two Enters after the file name):
| Test | Result |
|---|---|
| SAVE (receive): CNC sends `%..M30\r\n%\r\nTRAILING-JUNK` | file = bytes up to and incl. 2nd '%', CR/LF kept raw, rest discarded. **Confirmed** |
| LOAD (send) BIGTEST.MOP 34 KB | wire == even-parity-in-bit7 table applied to every byte, LF **kept**. **Confirmed** |
Note: DOSBox nullmodem passes all 8 bits, so bit 7 is visible here; a real UART in 7-bit mode sends only bits 0-6
plus the hardware even-parity bit, which equals the table's bit 7 - so on a real line LOAD is plain 7E2 ISO.
Screenshots: re-notes/_shots. ALINK UI needs two Enters (field accept, then "Press <Enter> to Send File").

## DOSBox-X (2026.10.01, heavy-debug build) - UART line settings logged by the emulator
Built from source in the VM (tools-bin/dosbox-x). serial1 option `dbgtr dbgreg` logs every LCR/divisor change
("New COM parameters") to capture/*.serlog.txt. Configs: tools/dosbox-x-*.conf.
| Program / mode | UART programmed to | Wire result |
|---|---|---|
| ALINK send | **9600, even, 8 data, 2 stop** | matches model (LF dropped) |
| AMANOPS LOAD (send) | **9600, even, 7 data, 2 stop** | matches model (34 KB) |
| AMANOPS SAVE (receive) | **9600, even, 8 data, 2 stop** | file ends at 2nd '%', trailing junk dropped |
Static analysis is now fully confirmed for all three paths.

LOAD flow control (7E2): XOFF 0x93 -> paused (1 byte in 3 s); XOFF 0x13 -> ignored (2629 bytes in 3 s).
Emulators deliver all 8 received bits, but a real 8250/16550 in 7-bit mode zeroes bit 7, so a CNC's XOFF
always arrives as 0x13 -> **on real hardware AMANOPS LOAD cannot be paused by XON/XOFF** (inference; needs a
real-port test). In the 8-bit modes (ALINK, SAVE) only a received 0x93 pauses; a plain 0x13 does not.
Note: with DOSBox-X use KEYS.COM (as in dosbox-x-amanops-save.conf); autotype was unreliable for the SAVE path.

## Other programs in the suite (serial-port survey)
ALPHACAM, APRINT, ATOOLS and ASETUP are Microsoft EXEPACK-compressed. They were unpacked with
`tools/unexepack.py` (outputs in `unpacked/`); the unpacked ALPHACAM.EXE starts and runs normally in DOSBox.
Each image was scanned for INT 14h, UART port constants and direct port I/O, and hits were checked in context.

| Program | Compiler | Serial port use |
|---|---|---|
| AMENU.EXE | MS C 5.x | none (menu front-end, returns choice as ERRORLEVEL) |
| ACOPY.EXE | MS C | none |
| ADXF.EXE | MS C | none (single "INT 14h" byte hit is runtime data) |
| ASETUP.EXE | MS C, EXEPACK | none (installer) |
| APRINT.EXE | MS FORTRAN, EXEPACK | none - prints by opening the DOS device `PRN` |
| ALPHACAM.EXE | MS FORTRAN, EXEPACK | **dead code only** (below) |
| ATOOLS.EXE | MS FORTRAN, EXEPACK | **dead code only** (same module) |

ALPHACAM and ATOOLS link the same assembly helper module (ALPHACAM seg 0x2A22, ATOOLS seg 0x1477). Next to its
file-I/O helpers it has four BIOS INT 14h routines (offsets 0x251, 0x2E5, 0x35E, 0x3A8). **None of them is
called**: there are no direct far or near calls and no far pointers to them, and Ghidra/REA finds no callers.
What they do (from the code):
* 0x251 - sends 0x80, waits for data (retries, shows a message), reads 8 chars ignoring '+', sends 0x80 again.
* 0x2E5 - flushes RX, sends 'A' (0x41), waits for '/' twice, reads 9 chars (7-bit).
* 0x35E - polled variant: sends 'A', skips 5 chars.
* 0x3A8 - reads 5-byte packets synced on a first byte of 10000bbb (Mouse Systems serial mouse format):
  4 delta bytes + 3 button bits.
These look like leftover digitiser/serial-mouse support. The live programs use the INT 33h mouse driver
("Mouse NOT Connected or Mouse Driver NOT Installed"). **The only live RS232 code in the suite is in ALINK
and AMANOPS** (Blaise library, documented above).

## Non-serial modules (behaviour verified by running them in DOSBox-X)
Harness: `tools/dbxrun.sh PROG "KEYS" SECONDS SHOT` (DOSBox-X headless + KEYS.COM). Screenshots: `_shots/ui_*.png`.

### AMENU.EXE - main menu (MS C)
* `AMENU n` draws "ALPHACAM Menu Selection" with the bar on item n and exits with **ERRORLEVEL = chosen item (1-8)**.
  Verified: `AMENU 1` + Enter -> 1, `AMENU 1` + Down Down Enter -> 3, `AMENU 5` + Enter -> 5, item 8 -> 8.
* ALPHARUN.BAT loops: 1 ALPHACAM, 2 ALINK ("Download CAM To Machine"), 3 APRINT, 4 ATOOLS (tool library editor),
  5 AMANOPS, 6 ADXF, 7 ACOPY, 8 exit to DOS. It calls `AMENU k` after each module, so the bar comes back on k.
* Uses ALPHACAM.MES for its texts; no files are written.

### ACOPY.EXE - floppy transfer (MS C)
* `FLOPPY.CFG` (in \ALPHACAM) holds one ASCII byte, '1' = A: or '2' = B:, written by `<!>` "Select Floppy Drive".
  Same convention as PORT.CFG. Default is A: if the file is missing.
* Menu: 1/2 drawings HD<->floppy, 3/4 manual ops HD<->floppy, 5 DXF floppy->HD, 6/7 tool library HD<->floppy.
* Lists sources with `DIR <dir>*.<ext> /A-D-H /ONE /B >DIRLIST.TMP` (TMPFILE.BUF holds the list), then shows a
  file browser with "ARE YOU SURE?". Copying shells out to `COPY <srcdir><NAME>.* <drive>:\*.* >NUL`, so a
  drawing copies **all files of its set** (NAME.SDG/.SDL/.SDT/...).
  Verified: ZZTEST.SDG/.SDL/.SDT copied to A:.
* Folder / extension used for listing: drawings \ALPHACAM\DRAWINGS\*.SDG, manual ops \ALPHACAM\MANOPS\*.MOP,
  DXF \ALPHACAM\DXF\*.DXF, tool library \ALPHACAM\*.TL1/.TL2. A string for PROGRAMS\*.600 exists, but no
  menu item uses it.

### ADXF.EXE - DXF -> .SDL converter (MS C + 8087 emulator)
UI: DXF name (from \ALPHACAM\DXF\), then units `<0>` Metric / `<1>` Imperial, then Enter to convert.
Output: `\ALPHACAM\DXF\<name>.SDL`, read by ALPHACAM's "DXF INPUT" command (ALPHACAM lists *.SDL there).
Supported DXF (ENTITIES section; HEADER/TABLES/BLOCKS are skipped):
| Entity | Converted to |
|---|---|
| LINE | line segment (Z ignored) |
| ARC | CCW arc start->end around centre |
| CIRCLE | full arc start=end=(cx+r,cy), stored **CW** |
| POLYLINE/VERTEX/SEQEND | lines; bulge (42) -> arc (sign gives CW/CCW); flag 70 bit0 closes the outline |
| SOLID, TRACE | 4 lines through corners 1-2-3-4-1 in **DXF order**, so a rectangle comes out as a bow-tie |
| POINT, TEXT, SHAPE, INSERT/BLOCK, ATTDEF/ATTRIB | ignored (blocks are not exploded) |
Layers are ignored (all layers are converted). Imperial multiplies every coordinate by 25.4 (stored in mm).

**.SDL file format** (fixed 9216 bytes, little-endian):
| Offset | Content |
|---|---|
| 0x0000 int16 | low byte = segment count, high byte = 0x30 (constant) |
| 0x0002 int16 | number of points N |
| 0x0004... | segment table: int16 triples (start, end, centre). Point refs are 1-based. start/end are stored negative. centre = 0 for a line, +k for a CCW arc, -k for a CW arc |
| 0x01FC, then every 0x100 up to 0xBFC | 4-byte marker FF FF FF FF; the table continues after each marker (1640 words of capacity) |
| 0x0C00 | float32 X[768] (DXF X) |
| 0x1800 | float32 Y[768] (DXF Y) |
The point pool is de-duplicated. New points are added in the order end, centre, start, so shared vertices are reused.
`tools/sdl_dump.py` decodes a file. `tools/dxf2sdl.py` re-implements ADXF and is **byte-identical** to ADXF output
for 10 of 11 test DXFs. The remaining one (a CW bulge) differs only in the sign of a zero (-0.0 vs 0.0) in a centre
coordinate.
Static note: ADXF's float code uses the MS C 8087-emulator interrupts (INT 34h-3Dh), so Ghidra decompiles it poorly.
The behaviour above was established by black-box runs.

## ALPHACAM.EXE main lathe CAD/CAM - work in progress (paused 2026-10-12)
Harness (all in tools/):
* `acsession.sh PROG < script` - runs the module in DOSBox-X under Xvfb on a **scratch copy** of Alpha181
  (~/acwork in the VM, so the real folder is never touched). RUN=name keeps a snapshot in ~/acruns/name;
  KEEP=1 reuses the previous scratch state.
* `xkeys` (xkeys.c, built against the VM's libXtst) reads script lines from stdin:
  `key <keysym>`, `skey <keysym>`, `type <text>`, `move X Y`, `click X Y`, `sleep S`, `shot NAME`.
  Mouse clicks reach the program via the INT 33h driver (absolute positioning works).
* Saved scripts: `ac_scripts/draw.ks` (new drawing + 5-line stepped profile), `fp*.ks` (finish-pass attempts).
* KEYS.COM was extended. Special characters: ^ Enter, ` Esc, _ Space, ~ Down, { Up, [ Left, ] Right, @ F1,
  # F2, $ F3, & F10, ' Backspace, + Home, = End, * PgDn, ( PgUp.

Findings so far:
* Start-up: Enter = retrieve a drawing, Space = new drawing. Then a billet form (Units, billet dia X, length Z,
  first tool, tailstock): F1 = accept, Space = toggle.
  Note: even with Units = Metric selected, coordinates typed later are taken as **inches** (the billet of
  100 mm shows as 3.937). Not yet explained.
* Leftover work files (SDWORK.SDG/.SDT, NCWORK.BUF) from a killed session crash the next start with
  "run-time error F6415: OPEN(\ALPHACAM\SDWORK.SDG) - file already exists" (MS FORTRAN STATUS='NEW').
  The ones that got into Alpha181 were moved to _to_delete/alphacam_workfiles.
* Help files (HELP/ENG/SD*.HLP) give single-key **hit codes** in the CAD screen: S start point, X draw line
  X-Z, Y draw line D-A, T text, P find point, L/A erase line/arc, U erase text, Q erase drawing, H incremental
  move, D R-A move, G absolute move, M/N measure, F radius/chamfer, Z zoom, R redraw, O fillet, B trim.
  X values are diameters. Z0 is the front face and negative Z goes towards the chuck.
* Screen (800x600 Xvfb, DOSBox-X): side-menu items are at x=580, y = 100 + 19*n.
  CAD menu: FIND POINT, START POINT, DRAW LINE XZ, ... CAM (y=461).
  CAM menu: CHANGE TOOL 100, AREA CLEAR 119, FINISH PASS 138, GROOVING 157, FACE CLEAR 176, DEL LAST MOV 195,
  HIDE MOVES 214, SINGLE STEP 233, DEL ALL MOVS 252, MIRROR 271, PIXEL SHADE 290, PRINT SCREEN 309,
  DXF INPUT 404, HELP PAGES 423, EXIT DRAWING 442, CAD 461.
  Drawing view at default zoom: screen x = 448 + 82*Z, y = 254 - 41*Xdia (inch units).
* Finish pass got as far as the settings menu (OFFSET X,Z / EXTERNAL / TOWARDS / EXECUTE at y=271).
  Pressing EXECUTE did not run yet: the Enter key re-opened the offset prompt. Next: select EXECUTE with the
  menu bar, then EXIT DRAWING -> save -> inspect the drawing set (.SDG/.SDT/...) and the generated .600 program.
* Drawing sets use extensions .SDG .SDT .SDL .SDZ .SDX .SDP .SDC .SDS .SDA .SDB .SDD (+ .DAT .DXF .MAC).
  The post-processor definition is \ALPHACAM\FANUC600.600, and NC output goes to \ALPHACAM\PROGRAMS\*.600.

### Progress 2 (ALPHACAM main) - target machine: Harrison Alpha 400T lathe
**Units.** The working units come from the installed default in `ALPHACAM.MES` record 0x0D (80-byte records), first
field: `  0` = metric, `  1` = imperial. ASETUP sets it at install time, and this install is set to imperial.
The "Units" toggle on the new-drawing form changes only the form display: saved drawings are byte-identical either
way, and later prompts still say "in". With record 0x0D set to 0 the program runs in mm (`METRIC=1` in acsession.sh
does this on the scratch copy only).

**Full CAM run now automated** (`tools/ac_scripts/metric_finish_ok.ks`, run with `METRIC=1 RUN=met2`):
new drawing -> 5-line stepped profile (dia 40/60/100, steps at Z-25/-50) -> CAM -> FINISH PASS (tool start
X120 Z5, contour start snaps to (40,0), `0` = take the other branch, Enter x4) -> EXECUTE -> EXIT DRAWING ->
save as MET2. Outputs are copied to `re-notes/alphacam_samples/` (MET2.* is correct; TEST1.* is an earlier
inch run with a wrong contour).

Saving a drawing NAME produces:
| File | Content |
|---|---|
| PROGRAMS\NAME.600 | **program for the machine** (what ALINK sends): Fanuc Custom Macro B text |
| DRAWINGS\NAME.SD1 | the same operation in a second NC dialect (G5/G115/G70/G21 E../G20/G170), with tool-nose radius compensation |
| DRAWINGS\NAME.SDG | 28,416 bytes: geometry. Same segment-table + X[]/Y[] layout as .SDL at 0x0000/0x0C00/0x1800; further blocks hold tool outline, text, billet/tool/start settings (0x5700: ... 100.0, 100.0, 1.0, 120.0, 10.0 ...) |
| DRAWINGS\NAME.SDT | 2,304 bytes: text/annotation table (still to decode) |

**NAME.600 structure** (MET2, finish pass, from post definition FANUC600.600):
```
AI                                   <- fixed first line (origin not yet found)
(MET2    )                           <- program name, 8 chars padded
IF[#1000EQ1]GOTO[#509*10]            <- restart: jump to the operation block N(op*10) when
IF[#1001EQ1]GOTO[#509*10]               machine input #1000/#1001 is set
N10                                  <- operation 1 block
#500=1 #501=1 #502=1                 <- operation/tool parameters (one per line)
#503=4.7244 #504=0.1969              <- tool start position X,Z (120, 5 mm)
#505=1.4488 #506=-2.0315             <- = SD1 G115 X36.8 Z-51.6 (contour limit point)
#507=2 #508=1 #509=1                 <- #509 = operation number used by the restart GOTO
M10                                  <- operation start
M11 / G00 ... / M12                  <- rapid move, wrapped in M11 ... M12
M13 / G01 X.. Z.. F#510 / ... / M14  <- feed moves, wrapped in M13 ... M14, feed from macro variable #510
M11 / G00 X(start) Z(start) / M12    <- return to tool start
M15  M16  %                          <- operation end, program end
```
Each line in FANUC600.600 is: output text (columns 1-20), a type code, numeric format fields, and a final
event/sequence number that says when it is emitted (for example G00 = 50025000, G01 = 1001, G02 = 1002,
G03 = 1003, F#510 = 7001, M13 = 2013, M14 = 2014, `#50x=` = 600xxxxx, M16 = 66042030, `%` = 6604).
Address rows N/Z/X/K/I define number formats.

**Important for the Harrison Alpha 400T: the .600 coordinates are in INCHES even in metric mode**
(X40 mm -> X1.5748, Z-25 -> Z-0.9843). .SD1 follows the session units, but .600 does not. Changing post header
row 2 ("1000 1.000" -> "1000 25.400") or the first flag of row 1 made no difference. The .600 has no G20/G21,
so the control must be in inch mode, or the 25.4 conversion has to be found/patched. **Still open**: find the
.600 writer in ALPHACAM.EXE (many float32 25.4 constants in DGROUP).
.600 moves are the raw contour (no nose-radius offset, X1.5748 = 40 mm exactly); SD1 applies the 0.8 mm radius
(X41.6).

Also found: ATOOLIB.TL1 = tool records (256 bytes each: name[20], float32 nose radius, int16 angles, description;
first word = tool count, 23). ATOOLIB.TL2 = tool outline drawings in the same segment/point format as .SDL.

### Progress 3 - the inch conversion in .600 output (found; SUPERSEDED by Progress 5: it is a setting, not a bug)
* ALPHACAM.EXE = EXEPACK-packed main module (ends at file offset 0x39B53) + **16 MS-LINK overlays** stored
  unpacked as MZ images from 0x39C00. `unpacked/ALPHACAM_OVL.EXE` = unpacked main + original overlays; it gives
  byte-identical output to the original.
  Note: unpacked/ALPHACAM.EXE without overlays fails with "Overlay not found".
* The .600 writer is in **overlay 16** (file offset 0x52400; code at +0x200). At file offset **0x53A20**:
  ```
  cmp word [es:0], 1      ; seg [0x407E] = units flag, 1 = metric
  jnz skip                ; 75 4C
  for i in 1..n:  v[i] = v[i] / 25.4   ; fld dword [0x4082] (25.4) ; fdivr ; fstp
  skip:
  ```
  So the inch conversion is deliberate: a metric session's coordinates (#503-#506 and every X/Z) are divided
  by 25.4 before posting. SD1 is not affected.
* Found by patching each of the 11 float32 25.4 constants to 1.0 one at a time and re-posting MET2
  (`tools/patch254.sh`). Only DGROUP:0x4082 changed the .600.
* **Fix (1 byte, on the original packed EXE)**: 0x53A20 `75` -> `EB`. Result: the .600 is written in mm
  (`#503=120.`, `G01X40.Z-25.`, ...), identical to the SD1 coordinates.
  `tools/patch_metric600.py IN OUT [--undo]` applies it with a check; a pre-patched copy is
  `patched/ALPHACAM_METRIC600.EXE` (sha256 8844bbaa...). Not applied to Alpha181.
* `tools/set_units.py ALPHACAM.MES metric` sets the working units (record 0x0D) the same way ASETUP does.
* **Decide before use on the Harrison Alpha 400T:** if the control's macros expect inch values (the original
  design), keep the stock EXE. If the control runs metric, use metric units plus the patched EXE. Verify with a
  dry run or single-block first.
* Post-definition address rows (N/Z/X/K/I), 8th field (e.g. 21000) is the number format: first digit 2 =
  write a decimal point (1 = no point, implied decimals); the remaining digits set the number of decimals
  (21000 -> 4, 21010/21100 -> 3, 20100 -> 2). `tools/postflip.sh ROW FIELD [VALUE]` re-posts MET2 with one
  post field changed (row 1 flags 0-3 had no visible effect).

### Progress 4 - arcs and multi-operation programs
* **Arc** (corner radius 5 mm made with hit code F; sample MET3.600/SD1, finish pass):
  `.600: G03X2.3622Z-1.2126I0.K-0.2283F#510` / `SD1: N65 G3 X61.6 Z-30 I0 K-5.8`. I and K are incremental from the
  arc start (I = X/radius offset, K = Z offset). G02 = CW, G03 = CCW (post rows G02 1002 / G03 1003).
* **Several operations in one program** (finish pass + area clear; MET3_AREA_STRUCTURE_ONLY.*). The toolpath of
  this sample is wrong (contour picked badly), so use it only for the structure:
  ```
  N10  #500=1 ... #509=1  M10 <moves> M15      operation 1 (finish pass: 1 pass, #507=2)
  N20  #500=7 ... #509=2  M10 <pass 1> ...     operation 2 (area clear: 7 passes, #507=1)
       #501=2 M10 <pass 2> ... #501=7 M10 <pass 7>   #501 = pass number, counted up for each roughing pass
       M15 M16 %
  ```
  N(op*10) blocks are the restart targets of `IF[#1000EQ1]GOTO[#509*10]`.
  #500 = number of passes (corrected in Progress 6), #507 = operation kind, #509 = operation number.
  For area clear, #505/#506 = -0.126/-0.063 in = -3.2/-1.6 mm (SD1 `G115 X-3.2 Z-1.6`).
  Each pass is: rapid to (X, Z start) - feed along Z - 45-degree pull-off - rapid out in X - rapid back to Z start.
* In SD1 an area clear becomes a canned cycle: `G71 P115 Q145 U0 W0 D3 R1 A45 E100 H7` (D = depth of cut 3 mm,
  P/Q = contour block range); a finish pass is wrapped in `G21 E1 Q.. C0 ... G20`, and `G170`/`G171` close the
  operation.
* Area clear menu: DEPTH CUT, OFFSET X,Z, EXTERNAL/INTERNAL, TOWARDS/AWAY, FRONT, EXECUTE. After EXECUTE it asks
  "Automatic Pre-Finish pass?" (Enter = yes, Space = no). Script: tools/ac_scripts/area_clear_met3.ks.

### Progress 5 - .600 output units are a setting (no EXE patch needed)
* The flag tested by the .600 writer (COMMON var at seg 0x42FF:0; checked at overlay 16 +0x141A/+0x14A1/+0x15E6/
  +0x1796 and in the main module at 0x29EE3) is loaded at start-up from **ALPHACAM.MES record 0x0C** (index 12,
  first field) and clamped to 0..1.
* **Record 0x0C = machine / .600 output units**:
  | 0x0C | .600 header line | coordinates | decimals |
  |---|---|---|---|
  | `  1` (as installed) | `AI` | mm / 25.4 = inch | 4 (format 1000 -> 1001) |
  | `  0` | `AZ` | mm | 3 |
  Verified with MET2 and 0x0C=0: `AZ`, `#503=120.`, `G00X42.828Z1.414`, `G01X40.Z-25.`.
  The first .600 line `AI`/`AZ` is therefore the program-units marker for the control (chosen at main+0x29EE3,
  strings at DGROUP 0x1598 "AZ" / 0x159A "AI").
* **Record 0x0D** = default units for new drawings (0 metric / 1 imperial) plus the billet defaults. Each drawing
  stores its own units in the SDG settings block at 0x5700 (float index 5: 0.0 metric, 1.0 imperial). Index 6/7 =
  billet dia/length, 8 = first tool, 9/10 = tool start X/Z, all in mm. Internal geometry is always mm.
* So for a metric Harrison Alpha 400T: `tools/set_units.py ALPHACAM.MES --machine metric --drawing metric`.
  This is what ASETUP's "Alpha Lathe Units: METRIC" choice writes.
  The one-byte EXE patch from Progress 3 is **not recommended**: it gives mm values but keeps the `AI` (inch)
  header and 4-decimal inch formatting.

### Progress 6 - all four CAM operations automated (metric drawing + metric .600 output)
Run with `METRIC=1 MMETRIC=1 ./acsession.sh ALPHACAM < ac_scripts/<script>`. Profile: dia 40/60/100, steps at
Z-25/-50.
**Contour selection rule:** the contour start snaps to the drawing point nearest the cursor. After the tool-start
prompt the cursor sits at the tool start (X120 Z5), so it snaps to (40,0). To start at the axis (0,0), press Down x6
before Enter (each Down moves the cursor about 20 mm in X). Then Enter accepts each proposed next point, `0` takes
the other branch, `3` ends early, and accepting the last point ends automatically. Mouse moves do NOT move the
CAD cursor. The first character typed after the save prompt is sometimes lost (AREA1 -> RE1, FACE1 -> FCE1):
wait about 3 s before typing.

| Operation (menu y) | Settings menu | Script | Sample | Result |
|---|---|---|---|---|
| FINISH PASS (138) | OFFSET X,Z / EXTERNAL / TOWARDS / EXECUTE | metric_finish_ok.ks | MET2.*, MET3.* (radius) | OK |
| AREA CLEAR (119) | DEPTH CUT / OFFSET / EXTERNAL / TOWARDS / FRONT / EXECUTE; then "Automatic Pre-Finish pass?" | area_clear_ok.ks | AREA1_metric.* | OK, 26 passes of 2 mm |
| GROOVING (157) | DATUM LEFT / OFFSET / PECK DEPTH / STEPOVER / TIP WIDTH / EXTERNAL / FRONT / Z-SHIFT / REPEATS / EXECUTE; EXECUTE asks <1> Area Clear / <2> Finish Pass | grooving_try.ks | GROOVE1_metric.* | runs with tool 1; with tool 16 (2 mm groove) see grooving_tool16.ks / GROOVE2_tool16_metric.* (Progress 8) |
| FACE CLEAR (176) | DEPTH CUT / OFFSET / TOWARDS / EXECUTE | face_clear.ks | FACE1_metric_empty.* | 0 passes (the face is already at Z0) - structure only |

**Operation parameters in the .600 (corrected):**
| var | meaning (from samples) |
|---|---|
| #500 | number of passes in the operation (finish 1, AREA1 26, GROOVE1 24, empty face 0); = H in the SD1 cycle |
| #501 | current pass number (1..#500), re-set before each pass |
| #502 | the operation's "Tool No." as APRINT prints it - always 1 in every test (tool 16, first tool 2); the real library tool is only in SD1 (`T` in the header, `G99 Tnn`). See Progress 10 |
| #503/#504 | tool start X/Z |
| #505/#506 | SD1 G115 X/Z (finish: contour limit point; area/face: -3.2/-1.6 = -4*r, -2*r of the 0.8 nose radius) |
| #507 | operation kind: 1 area/face clear, 2 finish pass, 3 grooving |
| #508 | always 1 (fixed post row `#508=1`) |
| #509 | operation number, target of the restart GOTO (N = #509*10) |
| #510 | feed rate (set on the machine, not in the file) |

SD1 cycle codes: finish `G21 E1 Q.. C0 ... G20` + `G170`; area clear `G71 P.. Q.. U W D(depth) R1 A45 E100 H(passes)` +
`G171`; face clear `G71 ... E401 ...`; grooving `G75 P Q U W D I R E L H(passes)` + `G175`. Every operation starts
with `G5 X Z` (tool start) and `G115 X Z`; the program header is `T1 G95 G97 G90 G30(mm)/G31(inch) L0` and
`G92 X Z` (turret reference).

### Progress 7 - post definition FANUC600.600, line by line (from experiments + output correlation)
Lines 1-3 = global header (row 1: 40 flags; row 2 `1000 1.000 ...`; row 3 `0 0 0 0 0 1 0 0 15 0 0`). Flipping
row-1 flags 0-3, 11-13 and row-3 field 5 changed nothing in a finish-pass program. Units are NOT set here (see
Progress 5).
Lines 4-18 = **registers / address formats**: text (cols 1-20), then fields; the last field is the register id.
| line | register id | used as |
|---|---|---|
| N | 1 | sequence number |
| Z | 5 | Z word |
| X | 6 | X word (diameter) |
| K | 21 | arc K (incremental Z to centre) |
| I | 22 | arc I (incremental X to centre) |
| (blank) | 8..17 | value registers for #500..#509 (8 -> #500 ... 16 -> #509) |
| N (line 18) | 17 | the `N10`/`N20` operation label (operation*10) |
In the address lines the 8th field (`21000`) is the number format: 1st digit 2 = decimal point; the rest selects
the decimals (inch output adds +1 to `1000`, so 4 decimals in inch and 3 in mm).
Lines 19-52 = **output words**: text, type, words, event id. Types: 3 = code + listed address words, 5 = text +
value from register, 6 = literal text, 12 = IF, 13 = GOTO, 0 = no text (only the listed words), -1 = end (`%`).
The 2nd-4th numbers list how many words follow and which registers they come from, e.g.
`G00 3 2 6 5` -> X(6) Z(5); `G01 3 3 6 5 7001` -> X Z F; `G02/G03 3 5 6 5 22 21 7001` -> X Z I K F;
`#500= 5 1 8` -> value of register 8.
The trailing number is the event the word is attached to (the last two fields are printed run together in the
file):
| event | output |
|---|---|
| 5000/5001/5002 | rapid block: `M11`, `G00 X Z`, `M12` |
| 2013 / 1001 / 1002 / 1003 / 7001 / 2014 | feed block: `M13`, `G01`, `G02`, `G03`, `F#510`, `M14` |
| 6002 / 1198 / 7000 | `IF[#1000EQ1]`, `IF[#1001EQ1]`, `GOTO[#509*10]` restart lines |
| 6003-6014 | operation header: `N<op*10>`, `#500=`...`#509=`, `M10` |
| 6020/6201 | next roughing pass: `#501=<pass>`, `M10` |
| 1080 | operation end `M15` |
| 6604 + 2030 | program end `M16`, `%` |
The `AI`/`AZ` first line and the `(NAME    )` comment are written by the code, not taken from the post file.

### Drawing files (partly decoded)
* NAME.SDG (28,416 bytes): 0x0000 segment table (same layout as .SDL: word0 = 0x3000 | count, word1 = points,
  int16 triples with FF FF FF FF markers); 0x0C00 float32 array A, 0x1800 float32 array B. Values are mm in drawing
  coordinates (MET2: A = 120, 95, 70 = Z + 120; B = X/2 + 81.69; full decoding in Progress 8). 0x2700 a second table (tool
  outline); 0x3300/0x3F00 more point arrays; 0x5700 settings: float[5] = drawing units (0 metric / 1 imperial),
  [6]/[7] billet length/dia (corrected in Progress 11), [8] first tool, [9]/[10] tool start X/Z.
* NAME.SDT (2,304 bytes) = text table: 0x0100 onward = 64-character text lines ("HELLO" + spaces); count/flags at
  0x0000, 0x004C, 0x0080 (`70 00 28 00 4C 00`), 0x00CC. Sample TXT1 is in the scratch runs only.
* NAME.SD1 = SD1 NC program (see Progress 4/6).

### Progress 8 - tool selection, tool library, SDG geometry decoding
**Tool selection (grooving with a real groove tool).** In CAM, CHANGE TOOL (screen 580,100) opens the tool library;
Right x15 + Enter selects tool 16 "2mm Groove/Parting" (script `ac_scripts/grooving_tool16.ks`). Result for a
groove X60 Z-20 -> X50 -> Z-26 -> X60 (sample `alphacam_samples/GROOVE2_tool16_metric.*`):
* .600: `#500=5 ... #502=1 ... #505=50. #506=-26. #507=3`, five plunges at Z-22 .. -26
  (`G00X120.Z-22.` / `G01X50.Z-22.F#510` / `G01X52.Z-22.F#510` ...). Z starts at -22, because the grooving tool datum is its LEFT
  edge (chuck side) by default - confirmed by HELP\ENG\SD246.HLP ("Datum Left/Right"; menu DATUM LEFT/RIGHT).
* **The .600 carries no tool number and no tool change** (#502 stays 1). The operator must load / call the
  correct tool on the Harrison 400T himself (or the control's macro must do it).
* SD1 has the tool change `N25G99T16` and the cycle `N40G75P45Q60U0W0D0I1R1E2L11H5`.

**Tool library ATOOLIB.TL1**: 24 records x 256 bytes (record 0 empty, tools 1-23): name[20], float32 nose radius, int16 front angle, int16 rear
angle, description text. Tools 14-15 are internal grooving tools, 16-18 grooving/parting (16 = 2 mm groove/parting); full list in Progress 10. ATOOLIB.TL2 = tool
outlines in .SDL format (readable with `tools/sdl_dump.py`).

**SDG geometry (decoded, `tools/sdg_dump.py NAME.SDG`)**: segment table at 0x0000 refers to point indices; point
i has A = float32 at 0x0C00+4i and B = float32 at 0x1800+4i. Settings block 0x5700: float[3] = Zref (120),
float[4] = Xref/2 (81.6901, i.e. G92 X163.3803 / 2). Then
`Z = A - Zref`, `X(diameter) = 2 * (B - Xref/2)`. Arcs: CW/CCW and centre are decoded (MET3:
`ARC CCW X50 Z-25 -> X60 Z-30 centre X50 Z-30`, the .600 shows `G03 I0 K-5.8`; the difference is
  presumably tool-nose compensation - not confirmed).
Verified on MET2, MET3, GROOVE2. Still unknown: tables at 0x2700 (tool outline), 0x3300 / 0x3F00, text positions.

**Open items now:** SDG 0x2700/0x3300/0x3F00;
real-machine dry run on the Harrison Alpha 400T and the AMANOPS LOAD XOFF test on a real RS232 port.

### Progress 9 - face stock (part face behind Z0)
The billet front is fixed at Z0 (billet 100 x 100 from MES record 0x0D); it does not move with the geometry.
Test part: face at Z-3 (3 mm face stock). Results (samples in alphacam_samples/):
| method | contour | result |
|---|---|---|
| FACE CLEAR, open profile | (0,-3) (40,-3) (40,-25) ... | `H0`, no moves - FACE CLEAR does not see stock unless the contour closes to the billet front |
| FACE CLEAR, contour closed to Z0 at the OD | (0,-3) -> (100,-3) -> (100,0) (`face_stock2.ks`, FACE6_stock_metric.*) | `E401 H1`: one roughing cut `G01 X12.844 Z-3.` from X120 at Z-3, retract `X10.844 Z-2.`; the SD1 finish profile becomes a slope X14.4 -> X98.4 Z0 (tool-angle collision check of the 80 deg rougher) |
| same, contour started at the OD | (100,0) -> (100,-3) -> (0,-3) (`face_stock.ks`, FACE5_stock_metric.*) | `H1`, cut only to X43.374 - worse |
| AREA CLEAR with FRONT toggled to FACE | full profile, face at Z-3 (`area_face.ks`, AREAFACE_metric.*) | `G71 ... E101 H24`: Z-stepped plunges clearing the shoulders; the 3 mm in front of the face is NOT cut |
SD1 cycle code E: 100 = area clear FRONT, 101 = area clear FACE, 401 = face clear.
Observation: in .600 the face pass is at Z-3 (imaginary tool tip), while SD1 has Z-2.2 (= -3 + 0.8 nose radius),
so SD1 apparently programs the nose centre and .600 the imaginary tip (inference from these numbers only).
**Practical advice for the Harrison 400T:** face the stock with a simple FINISH PASS along the face line (or by
hand / MDI), and use AREA CLEAR for the turning; the V1.81 FACE CLEAR with the default rougher (tool 1) gives
unexpected profiles - check the screen graphics before sending any face-clear program.
Note: saved files go to \ALPHACAM\PROGRAMS\NAME.600 and \ALPHACAM\DRAWINGS\NAME.SDG/.SDT/.SD1 (SDWORK.SD1 in
\ALPHACAM is the working copy). In the xkeys automation the letter A is dropped from typed names (AFACE1 -> FCE1, FACE5 -> FCE5,
also with a 5 s wait) - an automation quirk, not an ALPHACAM one; avoid A in automated names.

### Progress 10 - APRINT.EXE (print utility) and the meaning of the .600 registers
APRINT (MS FORTRAN 1987 runtime, EXEPACK) has two menu items: **Print Tool Moves** and **Print Operation
Details**. It asks for a drawing name (`<?>` lists DRAWINGS\*.SDG), shows "Ensure Printer Is Ready", then writes
the listing to the screen and to the printer. It uses no serial port.
* Printer output: the FORTRAN runtime opens the file name `PRN` (string at file offset 0x1C976 in the original
  APRINT.EXE), which its device table maps to DOS handle 4 (stdprn); no INT 17h. In DOSBox-X nothing arrived in
  a `parallel1=file` capture this way (`ECHO x>PRN` does work), so for capture `tools/aprint_run.sh` patches the
  *scratch copy* only: `PRN  ` -> `PRT  `, so the listing goes to the disk file \ALPHACAM\PRT.
  On a real PC with a printer on LPT1 no change is needed.
* Data source: it reads the drawing (.SDG/.SDT/.SD1), the tool library ATOOLIB.TL1 (tool names) and the
  message file ALPHACAM.MES (all format strings). It also references NCWORK.BUF and DIRLIST.TMP. Units follow
  ALPHACAM.MES record 0x0C (inch output when it is 1, mm when 0), the same as the .600.
* Samples (re-notes/aprint_samples/NAME_1.txt = tool moves, NAME_2.txt = operation details; for example
  `sh tools/aprint_run.sh GROOVE2_tool16_metric GROOVE 2`):
  ```
  Operation  : GROOVING           Tool No  :   1    2mm Groove/Parting
  Tool Start X : 120.000      Maximum X :  50.000       Offset X :   0.000
  Tool Start Z :   5.000      Maximum Z : -26.000       Offset Z :   0.000
  Op. No. :  1     Total Passes :  5     Depth Of Cut :   0.000
  ...  RAPID TO   : X 120.000, Z -22.000 / FEED  TO   : X  50.000, Z -22.000 / Pass No. :  2 ...
       CONTOUR TO : X  60.000, Z -30.800, R   5.800      (arc, from MET3)
  ```
  For a finish pass the line "Total Passes / Depth Of Cut" is left out (only `Op. No.`).
* **Register meaning, confirmed by APRINT labels** (same numbers in .600 and in the print):
  | .600 | APRINT label |
  |---|---|
  | #500 | Total Passes |
  | #501 | Pass No. |
  | #502 | Tool No. (always 1; the tool name printed next to it is the real tool, e.g. tool 16 or first tool 2) |
  | #503 / #504 | Tool Start X / Z |
  | #505 / #506 | Maximum X / Maximum Z |
  | #509 | Op. No. |
  Not in the .600: Offset X/Z (finish allowance) and Depth Of Cut.
  Test with first tool = 2 (MES record 0x0D) and AREA CLEAR: SD1 `N10T2...`, APRINT "Tool No. : 1  60 Deg Rougher",
  .600 `#502=1` (sample AREA_firsttool2_metric.*). So #502 is a fixed 1 in V1.81. Only the SD1 says which tool to
  use; on the 400T the operator has to select the right tool for the .600 program.
* ATOOLIB.TL1 numbering: record 0 is empty, so tool n = record n (tool 1 = 80 Deg Rougher 0.8R, 2 = 60 Deg
  Rougher, 4 = 35 Deg Finisher, 5 = 80 L.H. Rougher, 11-13 boring bars, 14-15 internal groove, 16-18 groove/parting
  2/4/5 mm, 19-20 face neutral, 21 face grooving, 22-23 5 mm ball-end). A left-hand tool (5) as first tool gave
  an empty AREA CLEAR (no moves).
* GROOVING REPEATS = 2 (Z-shift 0) gives three identical operations (Op. No. 1-3, #509 = 1..3), each with its own
  G75 cycle in SD1 (sample GRV3_repeat2_metric.*).
* AREA CLEAR with TOWARDS switched to AWAY on an external profile produced no moves (empty program).

### Progress 11 - the rest of the SDG drawing file (billet, chuck, settings)
Test: billet 80 dia x 60 long (MES record 0x0D = `  0      80.000      60.000  1  0`), one line drawn (sample
BILLET80x60_metric.*). Comparing with the 100 x 100 drawings:
* **0x2700 / 0x3300 / 0x3F00 = second geometry layer** with the same layout as the part geometry (table, A array,
  B array; segment flag 0x7000 instead of 0). ALPHACAM generates it from the billet values: billet outline at
  +/-dia from Z0 to -(length + 20), and the stepped chuck jaws in the last 20 mm (6/7/7 mm long steps, dia
  +10 / +20 / +30). `tools/sdg_dump.py NAME.SDG -a` prints it.
* **Settings block 0x5700 (float32)**, corrected:
  | index | meaning | 100x100 | 80x60 |
  |---|---|---|---|
  | 0, 11 | screen scale (pixels per mm) | 3.2692 | 3.8636 |
  | 1, 2 | view origin (screen pan) | -16.67, 15.16 | -24.11, 25.40 |
  | 3 | Zref = billet length + 20 = `G92 Z` | 120 | 80 |
  | 4 | Xref/2 (G92 X163.3803 / 2), fixed | 81.69 | 81.69 |
  | 5 | drawing units (0 metric, 1 imperial) | 0 | 0 |
  | 6 | billet length | 100 | 60 |
  | 7 | billet diameter | 100 | 80 |
  | 8 | first tool | 1 | 1 |
  | 9, 10 | tool start X / Z (default X = dia + 20, Z = 10) | 120, 10 | 100, 10 |
  | 13, 14, 15 | view limits (14 = dia + 30 = chuck jaw) | 16.69, 130, 146.69 | 26.69, 90, 136.69 |
  (Earlier notes had [6]/[7] the wrong way round; that went unnoticed because both values were 100.)
* So **`G92 Z` in the SD1 is not fixed at 120**: it is billet length + 20 (the chuck face as seen from the part
  front). `G92 X163.3803` is fixed. The .600 has no G92 line at all.
* 0x4B00-0x4BFF: header word 0x0030, then 2-byte cells that hold only 0x20 (space) and 0xDB (full block). This is
  probably the small block-character picture that APRINT prints next to TITLE (inference: same characters, not
  traced in the code).

### Progress 12 - ATOOLS.EXE (tool library editor)
Menu: 1 List/Print Tools, 2 Add A Tool, 3 Delete A Tool, 4 Alter A Tool, 5 Copy A Tool, 6 Store & Exit;
F1 = toggle units (shows METRIC), "Total Tools : 23". It uses no serial port (the serial-looking code in it is
dead, see the survey above).
* Working copy: ATOOLS edits \ALPHACAM\WORKLIB.TL1/.TL2 (with WORKCPY.TL1/.TL2) and copies them back to
  ATOOLIB.TL1/.TL2 on Store & Exit. It does the file copies through COMMAND.COM (`COMSPEC` + `COPY ` / `DEL `
  strings). In DOSBox-X that start-up copy did not happen and ATOOLS stopped with
  `OPEN error : [\ALPHACAM\WORKLIB.TL1]`. The cause was not traced (possibly not enough free memory to start
  COMMAND.COM). Workaround: copy ATOOLIB.TL1/.TL2 to WORKLIB.TL1/.TL2 first (`PRE="cp ATOOLIB.TL1 WORKLIB.TL1;
  cp ATOOLIB.TL2 WORKLIB.TL2"`). On a real install, check that the same copy works before relying on ATOOLS.
* Tool record fields (Alter A Tool screen): Tool Name, Tool Tip Radius, Front Clearance Angle, Rear Clearance
  Angle, Tool Graphics View (outline from .TL2), User Description (e.g. tool 1: "GENRAL PURPOSE ROUGHER: 3 DEG
  FRONT CLEARANCE, 7 DEG REAR."). Angles are measured from the Z axis, so the tip angle = front - rear
  (tool 1: 87 - 7 = 80 deg). Negative angles are internal (boring) tools.
* Print (F1 in the list): writes TMPFLE.LST in \ALPHACAM, then sends it to PRN. The listing (fixed-width
  records with no line breaks) is saved as re-notes/atools_samples/tool_list.txt:
  | T | name | R | F | R ang |
  |---|---|---|---|---|
  | 1 | 80 Deg Rougher 0.8R | 0.8 | 87 | 7 |
  | 2 | 60 Deg Rougher 0.8R | 0.8 | 87 | 27 |
  | 3 | 55 Deg Finisher 0.4R | 0.4 | 87 | 32 |
  | 4 | 35 Deg Finisher 0.4R | 0.4 | 87 | 52 |
  | 5 | 80 L.H. Rougher 0.8R | 0.8 | 93 | 173 |
  | 6 | 55 L.H.Finisher 0.4R | 0.4 | 93 | 148 |
  | 7 | 35 L.H.Finisher 0.4R | 0.4 | 93 | 128 |
  | 8 | 60 Deg Neutral Tool | 0.8 | 60 | 120 |
  | 9 | 55 Deg Neutral tool | 0.4 | 62 | 152 |
  | 10 | 35 Deg Neutral Tool | 0.0 | 72 | 108 |
  | 11 | 80 Boring-Bar 0.8R | 0.8 | -87 | -7 |
  | 12 | 55 Boring-Bar 0.4R | 0.4 | -87 | -32 |
  | 13 | 35 Boring-Bar 0.4R | 0.4 | -72 | -37 |
  | 14 | 2mm Internal Groove | 0 | -90 | 0 |
  | 15 | 3mm Internal Groove | 0 | -90 | 0 |
  | 16 | 2mm Groove/Parting | 0 | 90 | 0 |
  | 17 | 4mm Groove/Parting | 0 | 90 | 0 |
  | 18 | 5mm Groove/Parting | 0 | 90 | 0 |
  | 19 | 55 Deg Face Neutral | 0.4 | 28 | -28 |
  | 20 | 35 Deg Face Neutral | 0.4 | 18 | -18 |
  | 21 | 3mm Face Grooving. | 0 | 90 | 1 |
  | 22 | 5mm Ball-End Facing | 2.5 | 2 | 1 |
  | 23 | 5mm Ball-End Profile | 2.5 | 90 | 89 |
  (Matches the numbering in Progress 10.)
* For the Harrison 400T: enter the real turret tools here (radius and clearance angles), so that ALPHACAM's
  collision check and nose-radius compensation match the inserts actually used.

### Progress 13 - Disk1 "98/NT version" = SerioAlpha (ON HOLD, first look only)
Disk1 (~/alpha_pass/Disk1) is an InstallShield 5 set for **SerioAlpha 1.1** (Superdraft 2000, "Serial
Communications Program for Windows 95/98/NT/2000"), VB6 native code, serial via MicroHelp MhComm32.ocx. It is the
Windows replacement for the DOS link programs (ALINK/AMANOPS), not a Windows ALPHACAM. Extracted with unshield;
the program files are in re-notes/serioalpha/.
* PORTCONX.CFG: 3 machine entries: 1 "Alpha-Lathe: CAM Progs", 2 "Alpha-Lathe: Man Ops.", 3 "Alpha-Lathe: ISO Progs".
* SendNNN.cfg / RecvNNN.cfg (NNN = entry). Header: byte 0 baud index (list `110 300 600 1200 2400 4800 9600 14400
  19200 ...`, 6 = 9600), byte 1 parity index (`NOE`: 0 N, 2 E), byte 2 data bits (2/3, probably 7/8), byte 3 stop
  bits (1, probably 2), byte 4 = 1 (probably XON/XOFF), then XON 0x11, XOFF 0x13, file extensions, start/end
  characters, three 256-entry int16 tables, NC path. The meanings marked "probably" are not yet confirmed from the code.
  | file | ext | header | likely line | tables / notes |
  |---|---|---|---|---|
  | Send001 (CAM progs) | FAN | 06 02 02 01 01 | 9600 7E2 | path c:\alpha-s\drawings |
  | Send002 (Man Ops) | MOP | 06 00 03 01 01 | 9600 8N2 | |
  | Send003 (ISO) | ISO | 06 02 02 01 01 | 9600 7E2 | |
  | Recv001/002 (Man Ops) | MOP | 06 02 03 01 01 | 9600 8E2 | end chars `%%` (stop at the 2nd %) |
  | Recv003 (ISO) | ISO | 06 02 02 01 01 | 9600 7E2 | |
  Tables in every file: T0 = identity (stored with an offset of +1), T1 = even-parity table (send) or parity
  strip (receive), T2 = ASCII -> EIA RS-244 code (send) or EIA -> ASCII (receive). Which table is used depends on
  header fields not yet decoded.
* First differences compared with DOS (to be confirmed):
  * XOFF is 0x13. In DOS it was 0x93, which is DC3 with the even-parity bit set: the machine sends 7E DC3.
  * CAM send appears to be 7E2 here, while DOS ALINK was measured at 8E2.
  * Man Ops send is 8N2 here, while DOS AMANOPS LOAD was 7E2 plus a parity table. On the wire this is the same
    11-bit frame if the parity table is applied (8N2 with the parity in bit 7 = 7E2).
  * Receive: 8E2 with end at the 2nd %, the same as DOS SAVE.
  * The Windows version adds EIA code conversion and an "ISO Progs" channel.
* Code anchors for later: string "NOE" at VA 0x40DE84 (referenced at 0x41F094), baud list 0x40CF38 (ref
  0x417AE2), "PORTCONX.CFG" refs 0x41E37B / 0x42B7AE, ".CFG" refs 0x41F9C1 / 0x4211A3.

### Progress 14 - first test on the real Harrison Alpha 400T (2026-10-12)
* Setup: laptop USB-serial (/dev/ttyUSB0) to the 400T. Command:
  `alphalink.py -v --accept-dc3 send-alink /dev/ttyUSB0 alphacam_samples/MET2.600` (9600 8E2, LF dropped).
* Result on the PC side: "Sending 373 bytes ... 373/373 End Of Transmission". No XON/XOFF was seen; the file is
  too small for the control to need to pause the sender.
* Result on the control: it received the file but refused it: **"can load only Alpha-T programs"** as a CAM
  program. So the link and the line settings were good enough for the control to read the data. The *format* is
  wrong: V1.81 only writes the Fanuc-post `.600` (`AI`/`AZ` header, macro variables). No other output format
  exists in ALPHACAM V1.81.
* The control has no function to output a program to the PC, so a reference "Alpha-T" program cannot be captured
  from the machine.
* Not tried yet: the drawing's `.SD1` program (native-looking: G5 / G115 / G71 / G75 / G170-175, G99 Tnn) sent
  as a CAM program; the ISO receive mode with a plain ISO program (Alpha181/PROGRAMS/LOOPTEST.600).
* Most likely a newer CAM package is needed (the Windows SerioAlpha "CAM Progs" channel sends `*.FAN` files from
  c:\alpha-s\drawings, which points to an "Alpha-S" package). The user is checking for that program.

### Progress 15 - ALPHA-T / "AlphaLink V1.2" (the CAM for Alpha "T" machines) - first program generated
* Source: ~/alpha_pass/Alpha-t (DOS install set, 2001-2002). Working copy: alpha-work/AlphaT (ALPHTZIP.EXE and
  ALPHTMES.EXE are PKZIP self-extractors; unpacked with unzip). The original folder is not modified.
  Modules: ALPHA-T (CAM, FORTRAN), ATSEND (send `\ALPHA-T\DRAWINGS\*.PRG`, Blaise serial, V1.2), ATMANOP
  (`\ALPHA-T\MANOPS\*.MOP`), ATMACROS (`\ALPHA-T\MACROS\MACROS.MOP`), ATPRINT, ATTOOLS, ATDXF, ATCOPY,
  ATPATHS, ATMENU. The post is ALPHTCNC.PRG (same file layout as FANUC600.600).
* User document alphat.doc (Superdraft, April 2002): "AlphaLink V1.21 for Alpha "T" machines"; it notes that fast
  PCs can send too quickly for the Alpha. Their fix was to lower the Windows UART FIFO setting.
  -> alphalink.py now has `--char-delay MS` and `--line-delay MS`.
* Automation: tools/atsession.sh (scratch copy ~/atwork/ALPHA-T; METRIC=1 sets ALPHA-T.MES record 0x0D to mm).
  Script tools/ac_scripts/at_met2f.ks draws the MET2 part (X0Z0 X40Z0 X40Z-25 X60Z-25 X60Z-50 X100Z-50) with DRAW
  LINE XZ (580,119; start and end point typed for every line), runs a FINISH PASS (tool 3 = 55 deg finisher 0.4R),
  and saves. Save prompts, in order: store drawing? Enter / name / "Key in Program Number" (10) / "Select Units To
  Store Output In: 0=Metric 1=Inch" / "re-number the Tools? 1=No 2=Yes".
* Output (sample alphat_samples/MET2_finish_metric.*): DRAWINGS\MET2.PRG = **Alpha-T program**:
  `%`, `O2`, `#132=10` (program number), restart logic `IF[#1006EQ1]GOTO[[#129+1]*10]`, `IF[#1005EQ1]...`,
  `#131` counters, `M71`; then blocks `N10`, `N20`... with `#120..#130` parameters, `M66`, `IF[#140EQn] GOTO 50n`,
  `M63`, `M60/M61` around rapids (`G0X..Z..`), `M62`/`M64` around feed moves (`G1X..Z..F0.3`), `M65`, `%`.
  CR LF line ends; no "AI"/"AZ" header. MET2.SD1 is written as well (same SD1 dialect as before).
* **Open question: every Z value in the .PRG is 2x the drawing value** (contour Z-25 -> `Z-50.`, tool start Z5 ->
  `#124=10.`, tool change Z50 -> `#124=100.`), while X and the SD1 are correct (SD1 Z-24.6 = -25 + 0.4 nose radius).
  Setting ALPHA-T.MES record 0x0C (0 or 1) has no effect on this. It is not yet known whether this is how the
  Alpha-T control expects Z, or an artefact of this setup. Check it on the control's program display / graphics
  before any machining.

### Progress 16 - Alphalink-T manual (pages 70-73, LNK/T-G1.2-JAN/2002) and the Z x2 check
* **Warning from the manual:** "Loading programs not created using Alphalink T will corrupt the Alpha-T control
  macros. This will prevent the CONTROL software from powering up correctly." This also applies to programs from
  the Alphalink for Fanuc 21i machines. The fix is to reload the macro software: part no. **B703-0703** from the 600
  Lathes service department (file allmacros.mop / allmacros.txt). Rename it to `MACROS.MOP`, copy it with menu 7
  -> option 8 "KOPIERE MACROS von Diskette zu Festplatte" (to \ALPHA-T\MACROS\), then on the control: CAM key ->
  option 4 "CAM-Datei laden" -> arrow key, and on the PC: main menu option 9 "Manuelle Macros an ALPHA senden"
  (ATMACROS.EXE). When the lathe returns to its 4-option menu, switch the machine off and on.
  -> **The DOS V1.81 `.600` sent on 2026-10-12 is exactly the kind of file the manual warns about.** The control
  refused it ("can load only Alpha-T programs"), so the check probably caught it before the macros were touched.
  This is not confirmed: check that the control still powers up normally. We do NOT have MACROS.MOP;
  \ALPHA-T\MACROS in the install set is empty.
* Load procedure on the control: CAM key (computer symbol) -> option 4 "CAM-Datei laden" -> arrow key starts the
  receive ("Laden muss nach Start abgeschl. werden" = the load must be completed once started).
* PC main menu (German): 1 ALPHALINK, 2 CAM to machine (ATSEND), 3 print, 4 tool library, 5 DXF, 6 manual ops,
  7 copy to/from floppy, 8 open drawing file (Zeichnungsdatei aufrufen), 9 send manual macros to ALPHA, 10 DOS.
* **Z x2 is systematic in Alphalink-T:** an inch drawing (sample MET2_finish_inch.*) gives the same pattern
  (contour Z-1 -> `Z-2.`, tool start Z0.2 -> `#124=0.4`, tool change Z1.9685 -> `#124=3.937`). The SD1 is unscaled.
  Because the vendor's own post does this in both unit systems, it is most likely what the Alpha-T control macros
  expect (for example Z handled at half scale or in increments of 0.5). This is NOT confirmed; check on the
  control's graphics before cutting.

### Progress 17 - manual pages 60-61 (download to machine, cable) and the ATPRINT check
* The 400T has one serial connector: a 25-pin D-type on the side of the electrical cabinet, directly behind the
  headstock.
* Cable PC DB9 -> Alpha DB25 (manual p.61). There is no hardware handshake; flow control is XON/XOFF only.
  | PC DB9 | Alpha DB25 |
  |---|---|
  | 2 RD | 2 |
  | 3 SD | 3 |
  | 5 SG | 7 SG |
  | 1 CD + 4 ER + 6 DR tied together (PC side) | 6 DR + 8 CD + 20 ER tied together (Alpha side) |
  | 7 RS - 8 CS looped (PC side) | 4 RS - 5 CS looped (Alpha side) |
  DB9 pin 2 = RxD and DB25 pin 2 = TxD, so this pin-to-pin wiring is a crossover (null modem). The shield must
  NOT be connected to the connector shell. With a USB adapter the same loopbacks are needed only if the adapter
  enforces handshake (pyserial with rtscts/dsrdtr off does not).
* Control side (German manual; the 400T's screens are English, Progress 35: 4 "LOAD CAM FILE", touch the arrow):
  CAM menu -> "4. CAM-DATEN LADEN" -> ENTER / arrow key, then start the PC side ("Daten senden ...
  Bytes=" on the PC, "in Ausfuehrung" on the lathe). ESC aborts. The standard control has about **16 KB** of CAM
  memory; ATSEND warns if the file is larger (Enter overrides on machines with extra memory). Split big jobs.
  The port is COM1/COM2, chosen after typing the CAM name.
* ATPRINT (AlphaLink T) reads the drawing (SD1/SDG), not the .PRG. Edits made only to the .PRG did not change the
  printout. It prints the true geometry (Z-25, Z-50; tool start Z5), so it cannot settle the Z x2 question in the
  .PRG. That has to be checked on the control's display.

### Progress 18 - the control is a FANUC Power Mate 0
* Label in the cabinet: **FANUC Power Mate 0, A02B-0166-B591, 2002-11**. The CAM programs are Fanuc Custom Macro
  B programs (`O2`, `#120..#140`, `IF[...]GOTO`). They run Harrison's macro package (B703-0703), which lives on the
  control as programs. Probably that is why "foreign" files corrupt it: a Fanuc load writes every `Onnnn` block
  in the file into program memory, so a file that carries the wrong O numbers or data can overwrite or upset the
  macros. This is an inference from how Fanuc controls load programs.
* A Fanuc reader/punch interface normally uses ISO code (7 data bits, even parity) with `%` start/end. On input the
  control sends DC1 (0x11) to start the sender and DC3 (0x13) to pause it. That matches ATSEND: 7E2, XON 0x11 /
  XOFF 0x13. Baud rate and stop bits come from the control's I/O parameters; the Alphalink setup expects 9600 and
  2 stop bits.
* The machine's 25-pin panel connector (photo 2026-10-12): wires on pins 1-8 of the top row, plus at least one
  more; pin 1 goes to the green/yellow earth. The cable runs inside the cabinet to the Power Mate's RS232 port.

### Progress 19 - Alpha-w1 = "AlphaLink for Windows" V1.00 (2003) - not for the T machine
* InstallShield 5 set, 5 volumes. unshield failed on files whose first part in a volume is empty
  ("bytes_to_read can't be zero"); fixed with a small local patch (continue in the next volume). Extracted
  AlphaLink.exe (4.4 MB, MFC, C:\altest\AlphaCam.cpp), AlphaUtils.exe (VB6 comm program, MhComm32), help, tool
  library and the same SendNNN/RecvNNN.cfg as SerioAlpha (CAM 7E2 9600, XON 0x11 / XOFF 0x13; only the paths differ:
  c:\AlphaWin\Programs).
* AlphaLink.exe writes the **old Fanuc-21i / ".600" format** (`#500=..#510=`, `IF[#1000EQ1]GOTO[#509*10]`,
  `#508=1`, plus `IF[#530EQ1]GOTO#529` / `IF[#531EQ%d] GOTO %d`). That is the program type the T manual says
  corrupts the Alpha-T macros. **Do not use AlphaLink for Windows output on the 400T.** Use ALPHA-T (DOS).
* AlphaUtils has an option **"Wait for Xon Signal"** (WaitForXonCheck, "Xon Char"): the PC waits for DC1 from
  the control before it starts sending (classic Fanuc reader handshake). ATSEND does not wait. alphalink.py gets
  `--wait-xon` for this.
* USB adapter used on the lathe: Prolific PL2303 (067b:2303). Loopback test OK after the connection was fixed.

### Progress 20 - cause of "no reaction": standard 9-to-25 adapter (no crossover)
* The laptop side used PL2303 USB -> DB9 -> standard 9-to-25 adapter (DB9 2 RxD -> DB25 3, DB9 3 TxD -> DB25 2;
  checked by the user). The Alpha's DB25 is also a DTE (pin 2 = TxD, pin 3 = RxD), so TxD met TxD and RxD met
  RxD: nothing could pass in either direction. All "sends" to the 400T so far (the V1.81 .600 and the Alpha-T
  .PRG) never reached the control. The earlier "can load only Alpha-T programs" message was therefore not a reaction
  to our data.
* Fix: a full-handshake DB25 null-modem adapter, or the cable from manual p.61 (DB9 2-2, 3-3, 5-7; DB25 4-5 and
  6-8-20 bridged; DB9 7-8 and 1-4-6 bridged; shield not on the shells).

### Progress 21 - SerioAlpha (98/NT) config format decoded from the code
Port setup (SerioAlpha.exe 0x41eb40, defaults at 0x420380; Ghidra/objdump). The config record is loaded into a
global UDT at 0x433da0:
| file offset | field (default) | meaning (from code) |
|---|---|---|
| 0 | baud index (6) | index into "110 300 600 1200 2400 4800 9600 14400 19200 38400 56000 57600 115200 128000 256000" -> 6 = 9600 |
| 1 | parity (0) | 0 n, 1 o, 2 e, 3 m, 4 s |
| 2 | data bits (3) | value + 5 -> 2 = 7 bits, 3 = 8 bits |
| 3 | stop bits (1) | value + 1 -> 1 = 2 stop bits |
| 4 | handshaking (0) | written to the MhComm handshake property when non-zero; 1 = XON/XOFF |
| 5-6 | int16 (0) | (used with the end characters in receive; not decoded) |
| 7 / 8 | XON 0x11 / XOFF 0x13 | flow-control characters |
| 9 | wait-for-machine flag (0) | 1 = show " Waiting for Machine... " and wait for the XON char before sending |
| 10.. | 2 length-prefixed strings | file extension filters (original / edited) |
| then | 3 bytes, '(' ')', ... , '%' '%' | comment delimiters, start/end characters (receive stops at the 2nd '%') |
| +3x512 | 3 int16 tables | identity, parity (send: add even parity / receive: strip), ASCII<->EIA |
| end | length-prefixed path | NC directory |
So the shipped channels are:
* CAM Progs (Send001): **9600, 7 data bits, even, 2 stop, XON/XOFF, no wait, file sent as-is**. That is the same
  as the DOS ATSEND measured in DOSBox-X -> `alphalink.py send-alphat`.
* Man Ops send (Send002): 9600 8N2 + even-parity table (same frame on the wire as 7E2), XON/XOFF.
* Man Ops receive (Recv001/002): 9600 8E2, ends at the 2nd '%'.
* ISO Progs (Send/Recv003): 9600 7E2.
"Wait for machine XON" exists but is off in every shipped config; `alphalink.py --wait-xon` does the same if it
is ever needed.

### Progress 22 - the other Alphalink-T transfer modules (measured in DOSBox-X)
| module (menu) | direction | line | data on the wire |
|---|---|---|---|
| ATSEND (2, CAM to machine) | PC -> lathe | 9600 7E2 | .PRG unchanged (CR LF kept) |
| ATMANOP 1 "Save Manual Ops from ALPHA" | lathe -> PC | 9600 7E2 | stored raw until the 2nd '%' (`MANOPS\NAME.MOP`) |
| ATMANOP 2 "Load Manual Ops to ALPHA" | PC -> lathe | 9600 7E2 | even-parity table in bit 7 (no effect on a 7-bit line), LF kept |
| ATMACROS (9, Manuelle Macros an ALPHA senden) | PC -> lathe | 9600 7E2 | `MACROS\MACROS.MOP`, same parity table, sent without any prompt |
All of them use XON 0x11 / XOFF 0x13 (`cmp al,11` / `cmp al,13` in the ISR). Unlike DOS V1.81, receive is also 7E2,
not 8E2. `!` on the ATMANOP menu selects the port.
* `alphalink.py recv-alphat PORT FILE` = ATMANOP Save (backup of the lathe's manual operations, read-only for
  the lathe; a safe first test once the cable is right). Sample: alphat_samples/atmacros_capture_dummy.bin.
* HELP/ENG/SD248 (Alpha-T): FACE CLEAR is meant for material on a face that is "both external AND internal" (it
  "digs in"). That explains the odd results in Progress 9; use AREA CLEAR for ordinary turning.

### Progress 23 - "AlphaLink for Windows Training Manual" (alpha-work/AlphaWin Training Manual.pdf, 2010, 26 p.)
* AlphaLink for Windows = "AlphaWin-S", for the Alpha-**S** (and -U) series. Program output: "Alpha" (CAM mode,
  file name shown on the lathe) or ISO G-code (program number, Max speed; also writes NAME.TXT with the moves).
  The output page differs per lathe type. Programs go to \AlphaLink-S\Programs and ISO files to \AlphaLink-S\ISO.
  This confirms that Alpha-w1 is not the T version (see Progress 19).
* Units: internally always mm. If a file **Inch.op** exists in the program folder, the Alpha and G-code output is
  in inches; otherwise mm.
* Tool start position: "when you run the program on the Alpha lathe the tool must be within 5 mm of the point you
  input for it to continue". This is what the #123/#124 (T) or #503/#504 (S) start values are checked against.
* **Communication test recommended by Superdraft (no motion, nothing overwritten):** on the lathe, open the
  Manual Operations page (Stops, Taper or Thread) -> "Edit" -> Delete / **Save** / Load Manual Ops. Start
  the PC receive first (`alphalink.py recv-alphat`), then on the lathe "Save Operations" + Enter. If data
  arrives, the cable and port work. Then "Load" on the lathe and send the same file back (`send-alphat`) to check
  the other direction.
* Comm problems: use the supplied cable (pinout in the AlphaLink manual); if the PC sends too fast, switch the
  UART FIFO buffers off / to the lowest setting (-> alphalink.py --char-delay).
* Area clear parameters (S version): Depth of cut, Feedrate, Retract (Straight/Follow), Finish offset X/Z,
  External/Internal (retract direction - wrong setting retracts INTO the part), Towards/Away (from chuck),
  Front/Face (longitudinal or facing cuts), Comment. Facing in the tutorial is done with FINISH PASS "a Face" along
  a line X80,Z0 -> X0,Z0.
* DOS drawings can be opened in the Windows version (the datum must be moved); machining moves are not converted.
  The DOS ATOOLIB.TL1/TL2 can be copied over.

### Progress 24 - Alpha-T work session 2 (unpacking, X/Z constant hunt, more samples)
* Unpacked: alphat_unpacked/{ALPHA-T,ATPRINT,ATTOOLS,ATSETUP}.EXE (EXEPACK). ALPHA-T and ATTOOLS have MS-LINK
  overlays (ALPHA-T: 17 overlays from file offset 0x3F200). `ALPHA-T_OVL.EXE` = unpacked main padded to 512 +
  original overlays; runs normally in DOSBox-X.
* 2.0-constant patching (tools/patch_consts.py, f32 occurrences in ALPHA-T_OVL.EXE): f32 #19/#20 (file 0x60f2c /
  0x60f5c, i.e. main image, load-image offsets 0x5d61c / 0x5d64c) -> contour X doubled in the .PRG (X40 -> 80;
  approach offsets not). No single constant found yet that removes the Z x2. Code xref by displacement is
  ambiguous (many segments); next: find the segment base of 0x5d61c via the relocation that loads it.
* Tool change in ALPHA-T: CAM -> CHANGE TOOL (580,100) -> tool picker 6 per page, Right/Left, Enter -> "MC
  OPTIONS" (FEEDRATE ...) -> Down + Enter = EXECUTE. Tool numbers = ATTOOLS list (16 = 2mm Groove/Parting).
  **The .PRG carries the tool number: `#122=16`** (unlike the V1.81 .600), and the feed `#130=0.2`.
* AREA CLEAR sample (alphat_samples/AREA1_metric.*, 26 passes, 409 lines): N20 header `#120=26` (passes),
  `#121=1` (pass no., re-set `#121=n` before every pass), `#122=3` tool, `#123/#124` tool start, `#125/#126`
  limit, **`#127=6` (area clear; finish pass = 14)**, `#128=1`, `#129=2` (block), `#130` feed, `#131=#131+1`.
  Every pass: `N5nn` label, `#121=n`, `M66`, `IF[#140EQn] GOTO 5nn`, `M63`, rapid `M60 G0 .. M61`, feed `M62 G1 ..`
  (M64 closes the operation), so the control can restart at any pass via #140.
  Area clear menu: DEPTH CUT / OFFSET X,Z / FEEDRATE / RETRACT DIST / EXTERNAL / TOWARDS / FRONT / FOLLOW NO /
  EXECUTE (Down x8).
* GROOVING (menu DATUM LEFT / OFFSET / PECK DEPTH / STEPOV/TIPWD / FEEDRATE / FULL RET / EXTERNAL / FRONT /
  Z-SHF/REP / EXECUTE; then <1>Area <2>Finish, Dwell time [revs], "Input complete") failed in automation with
  "Tool tip width is greater than the total length of groove" even for a 20 mm groove - contour selection in the
  script is probably wrong for this geometry. Not resolved.
* tools/mk_at_script.py generates ALPHA-T xkeys scripts (draw part, optional tool change, one CAM op, save).

### Progress 25 - ALPHA-T: all CAM operations sampled; feed and pitch also x2; machine handshake (2026-10-12)
Samples in alphat_samples/ (scripts tools/ac_scripts/at_*.ks, METRIC=1 MMETRIC=1):
| sample | op | #127 | notes |
|---|---|---|---|
| MET2_finish_* | finish pass | 14 | |
| AREA1_metric | area clear | 6 | 26 passes |
| FACE_metric | face clear | 8 | depth 2 -> passes at Z-4, -8 ... (x2); #125/#126 = billet limit X-1.6 / Z-101.6 (= 2 x -50.8); every pass has its own N5nn restart label |
| GROOVE_metric | grooving | 9 | stepover 2, tip 3, area (1); plunges G1 in / G1 out (+2 dia); SD1 G75 ... G175 |
| DRILL_DWELL_metric | drilling + dwell | 18 | pecks written out as G1 moves; dwell G4P<revs*1000> (SD1 G4P<revs>); SD1 G74 ... G174 |
| THREAD_metric | threading | 20 | pitch 1.5 -> F3. and #130=3.; G32 X Z F per pass; retract U21. W-3. one pitch before the end (V1.21 note); SD1 G76 ... G176 |
| MOVE_RAPID_metric | move / rapid to cursor | - | appended to the tool block: M62 G1 X Z F / M60 G0 X Z M61, no N20 header |
(#127=1 in every N10 tool block.)

Scaling, now confirmed on every op: **Z, feed (F and #130) and thread pitch = 2 x drawing value; X (diameter) 1:1;
SD1 unscaled** - in mm and inch drawings. Feed per rev scaled with Z means the whole Z axis is in half units on
the T control/macros (e.g. Z axis in "diameter" units). Most likely intended; still verify on the control
(CAM load, look at the graphic/Z display, no cycle start). [Settled in Progress 29: confirmed for Z and feed.]

Key sequences learnt: grooving is not contour-driven (STEPOV/TIPWD sets both values; after EXECUTE answer
1/2 area/finish, Enter for dwell, Enter for "Input complete", wait ~25 s). Face clear: EXECUTE = Down x5,
Enter "Input complete", Space = no pre-finish. Threading: Pitch, EXTERNAL toggle, then identify outer and
inner diameter lines with the cursor. After any op go straight to EXIT DRAWING - an extra Enter re-enters the op.
xkeys drops 'A' in typed names (FAC -> FC).

Machine side (user measurements on the machine DB25, pin 7 = ground): pins 2 and 3 both -10 V (idle mark),
pins 5 (CTS) and 6 (DSR) = 0 V -> the control sees "not ready" and neither sends nor accepts. Fix in progress:
bridges 4-5 and 6-8-20 in the machine-side plug (manual p.61), so the control satisfies its own handshake.
[Settled in Progress 29: with the bridges the control sends XON and accepts programs.]

### Progress 26 - FANUC Power Mate 0 connection manual (B-63443EN/01), RS-232-C (2026-10-12)
Source: user's copy `~/Documents/fanuc/583648381-FANUC-Power-Mate-0.pdf` (not in the repo), section 7.3 (p.62-70)
and appendix H.2 (p.240-243).
* Relay connector DBM-25S (the cabinet DB25): 1 FG, **2 SD (NC out)**, **3 RD (NC in)**, 4 RS, 5 CS, 6 DR, 7 SG, 8 CD,
  20 ER, 25 +24 V. Same as our cable.json (DTE, so the PC needs a crossover).
* The manual's own rule for a device without handshake: "When CS is not used, connect CS to RS" (4-5), "when DR is
  not used, connect DR to ER" (6-20), and CD must always be tied to ER (8-20). This is exactly the planned bridge
  4-5 + 6-8-20, so after it pins 5/6/8 should read positive (ON is +3 V or higher) once the NC raises ER/RS.
  Note: RS (4) only turns on while the NC is outputting, so with 4-5 bridged CS is driven by the NC itself.
* NC sending (punch, Manual Ops Save): needs CS and DR on; DR dropping mid-transfer = alarm 086. The NC sends DC2
  first and DC4 at the end; it stops within 2 characters when CS goes off, or within 10 characters after a DC3.
* NC reading (CAM load / Manual Ops Load): the NC sends **DC1** to start, **DC3** to pause; the device must stop within
  **10 characters** of DC3 or the NC raises alarm 087, and resumes on the next DC1. Implication for USB adapters:
  keep writes small (alphalink.py already writes 4-byte chunks, or 1 byte with --char-delay) and prefer
  `--wait-xon` so nothing is sent before the first DC1.
* Character frame: 1 start, 8 information bits including parity, 2 stop bits = our 7E2. ISO code has even parity in
  bit 8. Baud rates 600-19200.
* Parameters to check on the control if the link still fails: 0020 I/O CHANNEL (0), 0102 device = 0 (RS232C),
  0103 baud = 11 (9600), 0101 bit SB2 = 1 (2 stop bits), ASI = 0 (ISO/EIA auto), NFD = 1 (no feed). 0100 NCR selects
  the EOB output (0: LF CR CR, 1: LF only). Harrison's macros may drive these, so read them, don't change them.
  User (2026-10-12): the Harrison setup exposes very few parameters, so these may not be visible at all; treat them as
  background, not a test step.

### Progress 27 - ALPHA-T post rules from the cloud samples; app post byte-identical on 13 samples (2026-10-13)
New samples (generated in a cloud DOSBox-X run, copied to samples/alphat_v12; scripts in alpha-work/cloud-run/samples):
ARC_finish/ARC_area (R5 convex, R3 concave corners), SLOPE_area (taper X40 Z-25 -> X60 Z-40), TWOOP_area_finish,
OFFSET_finish/OFFSET_area (X0.5 Z0.2), INT_finish/INT_area (tool 12 boring bar), TOOLCHG_rough1_finish3.
* **The PRG contour is the imaginary tool-tip path**: the SD1 nose-centre path shifted by (-r, -r) in (radius, Z),
  (+r, -r) internal. Axis-parallel lines stay on the drawing (why MET2 looked "uncompensated"); arcs become
  R+r (convex) / R-r (concave) with the centre shifted; slopes move (SLOPE pass ends -37.279 = shifted slope).
  First and last points stay the drawing's end points (lead-in/out start there).
* Arcs: `G2/G3 X Z I K F`, I = centre - start in **diameter**, K = centre - start in Z (x2). G3 = SDG "CCW".
* Area clear: passes stop on the tip path with half-open spans on the approach side; internal (#127=7) steps up
  from Xmin by 2*depth to Xmax (no 2r overshoot, unlike external Xmin - 2r), retract -X.
* Internal finish (#127=15): lead-in/out go -X (X47.172 from X50).
* Offsets translate the path (X + 2*ox, Z + oz; axis points stay at X0); #125/#126 and the area floor do not move.
* Every operation is followed by an empty block; before a tool change that block holds `M60 G0 X<tc> Z<tc> M61`.
  A later tool change is another tool block (#127=1, #129 = block no.).
* Still without a sample: arc-to-arc corners, offsets on internal ops, away/pre-finish, moves after an operation,
  inch arcs.

### Progress 28 - post for face, groove, drill, thread, pre-finish (2026-10-13)
All 18 samples in samples/alphat_v12 now post byte-identical (app/tests/test_post.py). Rules (details in
data/program_formats.json and the app/alphacam/toolpath/*.py docstrings):
* Face clear: zones between the profile's faces, depth steps clamped onto each face; pull-off 0.2 at 45 deg, then
  rapid to (start X - 0.8, Z + 0.6) - the 0.8/0.6 split between retract (0.2) and nose radius (0.4) is ambiguous.
* Grooving: datum = tip's -Z corner; plunges from top Z - tip width every stepover, plus one on each left wall;
  depth = highest **drawn** diameter under the tip (not the tip path).
* Drilling: pecks measured from the previous peck, back-off 2, dwell at the bottom; limits = start X / bottom Z.
* Threading: cut n at first * sqrt(n), depth rounded to 3 decimals before doubling; U = outer - inner + 1 assumed.
* Area pre-finish (cloud sample PREFINISH_area_metric): extra block #127=10, finish path with a 1 mm pull-off.
* "Away" area clear stays refused: the cloud AWAY sample (left-hand tool 5) gouges past the dia-100 shoulder.

### Progress 29 - first real CAM load on the Alpha 400T (2026-10-13)
* `alphalink.py -v --wait-xon --char-delay 2 send-alphat /dev/ttyUSB0 MET2_finish_metric.PRG`: the control sent
  XON (0x11), all 636 bytes went out, and the control accepted the program (user report). The cable fix
  (bridges 4-5, 6-8-20) works.
* Re-sent with `#132=5` (MET2_finish_metric_P5.PRG, posted by app/alphacam): the control then showed program
  number 5, so **#132 is the program number on the control**; `O2` stays constant.
* **Z x2 confirmed for positions**: with program 0005 loaded, the control's run screen shows `OP. 0  TOOL 03
  TOOL START X 100.000 Z 50.000` while the PRG has `#123=100.` `#124=100.` - the macros halve Z. So the ALPHA-T
  scaling is intended and the app's default z_scale = 2 is right (user photo, 2026-10-13).
* **Feed x2 confirmed too**: the screen first showed `FEEDRATE 0.11` with the feed override knob below 100%; at 100%
  it shows `FEEDRATE 0.15` for `#130=0.3`. The displayed feed includes the override. The same screen shows
  `LIMITS X 0.000 Z 0.000` for the tool block (no #125/#126 there) and the turret on T01 while the program wants T03.
* So C4 is settled for Z and feed on the control display: ALPHA-T's x2 is what the T macros expect. Thread pitch x2
  is assumed by the same logic but not yet seen on the control.
* Machine fact (user, 2026-10-13): the turret is **manual** (indexed by hand), but the control has **tool memory**
  (offsets stored per tool number). So `#122=<tool>` selects the stored offsets and a tool block means "operator
  changes the tool at the tool change point" (X100 Z50 by default). The tool numbers in the CAM must match the
  control's tool memory slots (e.g. T03 = the 55 deg finisher with its measured offsets). MET2 and MOVE_RAPID were
  run on the machine; the user judged the result close, with any remaining differences easy to fix later.

### Progress 30 - more cloud samples: internal/pitch-2 threads, face offsets, inch set (2026-10-13)
* Byte-identical now: THREAD_internal (cuts from the inner line out to the outer one, U-21., #125 = outer),
  THREAD_p2_len30 (W = -pitch x2), FACE_offset (offsets translate the profile like area clear), FACE_depth3_start110
  (pull-off rapid again start X - 0.8, Z + 0.6: independent of depth and start).
* **Area clear zones** (seen only in inch, where steps no longer land on the shoulders): every Z-parallel diameter
  of the profile between start and limit gets its own pass and the stepping restarts from it
  (... 2.426, **2.4**, 2.2426 ...). Metric AREA1/ARC/SLOPE unchanged because 100-4k hits 60 and 40 exactly.
* Inch rounding: the toolpaths use the nose radius rounded to 4 decimals (0.4 mm -> 0.0157: arc R 0.2157, area
  floor -0.0314), the #125/#126 limits the exact one (-0.063). The pre-finish pull-off is the area retract distance
  as entered (0.0394 in) at 45 degrees -> 0.0279.
* samples/alphat_v12_inch: 10 inch samples, 8 byte-identical. Known small differences: ARC_area_inch (area pass
  ends on arcs 0.0001 shallower) and TOOLCHG_inch (finish Z +0.0002 after a tool change). The machine runs metric
  (M71), so these are parked; REA on ALPHA-T's arc code would settle them.
* PECK_groove / PECK_fullret_groove arrived truncated at 12288 bytes with empty SDGs - regenerate before use.

### Progress 31 - peck grooving (2026-10-13)
Regenerated cloud samples PECK_groove_metric (PECK DEPTH 2, FULL RET NO, SD1 L110) and PECK_fullret_groove_metric
(FULL RET YES, L111) are byte-identical with the post:
* pecks at start X - 4k (peck 2 on radius) while not below the plunge depth, then the normal final plunge (so a
  peck landing exactly on the bottom is followed by the same plunge again: G1X40 G1X42 G1X40 G1X42);
* FULL RET NO: after each peck G1 back 2 on diameter, continue;
* FULL RET YES: G1 back one peck, rapid out to the start X, rapid in to one peck above the last peck (x + 8),
  G1 to the next peck; the final plunge goes in from the last peck + 4 and backs off 4.
* ALPHA-T's default is FULL RET NO; the app model default was changed to match.

### Progress 32 - operations live only in the .SD1/.PRG; SD1 import (2026-10-13)
* The .SDG holds geometry and billet only; the .SDT is byte-identical for area, face and finish samples, so ALPHA-T
  does not save the operations with the drawing.
* app/alphacam/io/sd1.py rebuilds them from the .SD1 cycles (G70 finish, G71 E100/-100/401 area/face, G75 groove,
  G76 thread, G74 drill, G4 dwell, G0/G1 moves, G99 T tool change, G70 R1 = pre-finish). For all 24 metric samples,
  SDG + SD1 -> post is byte-identical to ALPHA-T's .PRG (app/tests/test_post.py FromSD1). Opening an .SDG in the
  app does this automatically and reports whether the result matches the .PRG next to it.

### Progress 33 - 16 more option samples (2026-10-13)
New samples in samples/alphat_v12 (made by the cloud ALPHA-T run, menu values in samples/alphat_v12/README_options_2026-10-13.md).
All but two are byte-identical via SDG + SD1 -> post:
* Area clear: start on a diameter (AREA_from_dia20); FACE direction instead of FRONT (AREA_front_face, SD1 G71
  E101) is posted as #127=8 with face-clear passes, but the area retract (1 mm) and an outward 45-degree pull-off
  (+2R in X, +R in Z); face clear proper pulls off inwards (-2R, +R) with R 0.2.
* Internal finish/area with OFFSET X0.5 Z0.2: X moves inwards for internal work (INT_*_offset).
* Face clear with the 0.8R rougher: the rapid after the pull-off goes to (start X - 2r, pull-off Z + r).
* Face clear over a slope (FACE_slope) and corner radii (FACE_arcs): zones split at faces only; each pass ends on the
  tip path's diameter at that Z. ALPHA-T differs from the exact value by up to 0.001 on diameter in either direction
  (a numeric search with a tolerance, presumably); test_post SD1_KNOWN_DIFF allows that.
* Corner radii meeting each other (ARC_touching_R5_R3, the diameter between them has zero length): G3 then G2.
* Grooving: DATUM RIGHT = SD1 L100, plunges +tip width; Z-SHIFT 10 / REPEATS 1 = SD1 R10 C2, a second pass with
  the plunges shifted (above the start Z here); DWELL 2 = SD1 A2, G4 at every plunge bottom; FINISH mode = SD1
  G78 ... G178, #127=17: one pass along the drawn profile, the vertices of walls where the profile steps down move
  -tip width (datum left), rapid in at (start X, first Z), feed out to the start X.
* Threading: LEAD IN (default 5, SD1 I) moves the start Z to thread top + lead in; LEAD OUT (default 0, SD1 L) extends
  the end; SPRING PASS 1 (SD1 K1) is its own block #127=21 with one cut at the final diameter, and the thread block
  then ends without M64.
* Drill without peck or dwell: one G1 to the bottom and back. Moves/dwell after an operation (MOVE TO CUR, RAPID TO
  CUR, DWELL) go into the empty block after it (FINISH_then_move_rapid_dwell: G1X80.Z10. G0X80.Z-30. G4P2000).

### Progress 34 - SD1 writer (2026-10-13)
app/alphacam/post/alphat_sd1.py writes ALPHA-T's .SD1 listing; all 41 samples with an .SD1 come out byte-identical
(app/tests/test_sd1_write.py). Rules worth knowing:
* 4 decimals, no trailing point; N steps by 5, M30 without N; G30 metric / G31 inch on N10; G92X163.3803Z120 always.
* Motion lines leave out a coordinate equal to the previous one, but the first motion line after any other line
  has both.
* G70/G71 list the nose-centre path with the exact nose radius: first point offset along its element's normal, last
  point offset and then carried r further along its element (not for face clear, E401/E101). Approach G0 from 2 mm
  at 45 degrees; pull-off finish G1 0.2, area G0 1 (=R), face G0 0.2 inwards, area FACE G0 1 outwards, pre-finish
  G1 1. Finish passes list the path moved by the offsets (first point only across its element); area/face don't.
* G71 P = the G21 line, Q = the G0 back to the start, H = passes. G75 P/Q = first/last profile line, H = plunges.
* G76 Q = thread depth on radius from the drawing (THREAD_depth6: lines at 40 and 28 -> Q6). G76 D = first cut
  depth; with NO. OF CUTS n instead of CUT DEPTH it is depth / sqrt n rounded to 4 decimals (THREAD_6cuts: n 6 ->
  D4.0825, 6 passes, Q stays 10). The app has this as Thread.cuts.

### Progress 35 - control screens in English (photos from Eduard, 2026-10-13)
* CAM menu: 1 RUN EXISTING FILE SINGLE OP, 2 RUN EXISTING FILE AUTO CYCLE, 3 SELECT REQUIRED PASS (SET CORRECT
  TOOL/CSS FIRST), 4 LOAD CAM FILE. LOAD CAM FILE: "TOUCH ARROW TO START DOWNLOAD", one ← arrow (PC -> lathe),
  "WARNING: LOAD ONLY ALPHA-T PROGRAMS".
* Manual Ops screen (X/Z, T, S, Op.No, X1 Z1 D U W T2 F2 fields) has a bottom row of icons: hand (manual), move,
  taper, thread, ops file (list <- clipboard), coolant. The ops file icon opens 1 OP. SAVE/RESTORE, 2 DELETE
  CURRENT OP., 3 DELETE ALL OP.S; OP. SAVE/RESTORE shows → (lathe -> PC, save) and ← (PC -> lathe, restore).
* The app's machine page instructions now use these names.

### Progress 36 - the last options: pre-finish variants, internal FACE, grooving, AWAY (2026-10-13)
Samples in samples/alphat_v12 (second table of README_options_2026-10-13.md). Byte-identical (PRG and SD1):
* Pre-finish after internal area clear: #127=11; after area clear with offsets: the finish path moved by the offsets
  (SD1 G70 R1 U W); after face clear: #127=12, SD1 G70 R1 E2, the path ends on the offset end (X99.2, not the drawn
  X100) and pulls off 0.2 mm inwards like face clear.
* Area clear FACE on a bore (E-101): #127=7, passes feed outwards from the start X to the bore wall, pull-off and
  rapid towards the axis; the first zone starts at the bore's top (no face there). SD1 lists G21 E-1 Q3, and in face
  mode the first point is carried r back along its element (X49.2 Z0.4).
* Grooving internal (tool 14, SD1 L-110): X mirrored; plunges go out to the nearest bore wall under the tip and back
  off 2 towards the axis; the SD1 lists only the picked elements (P/Q), so the importer reads them from there.
* Grooving FINISH OFFSET X/Z (U W): every side of the profile keeps the allowance (diameters +2U, faces W towards the
  open side), and a plunge is inserted at a groove's +Z wall - tip width when a step would jump over it.
* Grooving finish mode on a slope: both ends of a descending side move by -tip width, like a face.
* Refused by the app, because ALPHA-T's own output is unsafe on these parts: area clear AWAY (passes end 1.6 mm
  past the shoulder; the rerun is byte-identical), face clear AWAY (E411: one pass at Z0 only, tools 3/9/19 refused
  with "Invalid tip angle"), threading AWAY (E2: cuts start at Z-5 inside the thread, W+ pull-out), grooving from
  the BACK (L2xx: face grooving that plunges through the turned profile). A real face-groove drawing would be the
  sample to try BACK again.

### Progress 37 - German ALPHALINK (ALPHA-T) manual, LNK/T-G1.2-JAN/2002 (scan from Eduard, 2026-10-13)
88 scanned pages (~/alpha-work/vendor/Скановано_20261013-2132.pdf, not in the repo), OCR'd with tesseract (deu).
What it adds to the samples, by chapter:
* CAM menu: tool change, area clear, finish, grooving, face clear, drilling, threading, move/rapid to cursor, dwell,
  delete last move, hide moves, single step, delete all moves (with a new tool change position), shaded animation,
  print screen, exit (saves the drawing, asks a 4-digit program number, the output units, and can renumber tools).
* Tool start: the macros only allow the program to start with the tool within about 5 mm of this point.
* Contour identification: the cursor walks the contour point by point from the picked start; 1 skips a point, 3
  ends; at a branch 0 switches. **The direction in which the contour is identified sets the cutting direction.**
  For face clear: identified from the outside diameter in, the passes cut inwards, and the other way round outwards.
  This likely explains the odd AWAY samples (Progress 36): they were identified from the front, i.e. the "wrong"
  direction for AWAY. To check with AWAY samples identified from the chuck end.
* Area clear options not in the app yet (done since, Progress 38): **retract distance** (Rückzug-Weg; default 1 mm = SD1 G71 R) and **follow
  the contour** yes/no (Der Kontur folgen; no = 45-degree retract after each pass, presumably SD1 A45; yes = the tool
  follows the profile before retracting). It ignores pockets in arcs shallower than the depth of cut, and keeps clear
  of the tool's rear clearance angle (collision avoidance).
* Cutting direction options: EXTERN/INTERN (sets the retract direction and the side the allowance is on), ZUM/
  ENTGEGEN (towards / away from the headstock), VORDERSEITIG/STIRNFLÄCHE (front / face). "Any combination" is
  allowed; a wrong one for the geometry gives odd toolpaths rather than an error.
* Finish pass: the same options, including STIRNFLÄCHE (face) - SD1 G70 E2, seen so far only as the face clear
  pre-finish. Approach in rapid and retract by a fixed amount; nose radius and tool angles compensated.
* Grooving: datum left/right becomes top/bottom and Z-shift becomes X-shift with STIRNFLÄCHE (face grooving).
  Peck retract 1 mm (fixed) when full retract is NO. Finish mode (2) = one pass along the contour.
* Drilling options not in the app yet (done since, Progress 38): **full retract** yes/no (out to the start point after each peck) and the
  **retract distance** when not full; 0 disables the retract. Drilling is always on the centre line.
* Threading: number of cuts OR first cut depth (as implemented), lead in / lead out (added to the inner diameter
  line's length), **crest height** (Spitzenhöhe, SD1 G76 B?), **angle**: 0 = straight (radial) infeed, > 0 = flank
  infeed (SD1 G76 A; every sample has A0), **finishing passes** (Schlichtspäne, SD1 K, can be more than 1: the last
  pass repeated to remove tool spring). Tapered threads by drawing the two lines at the taper. On the control,
  "re-run operation" repeats only the spring passes (for size correction with the offset).
* CAD commands added to the app from this (More menu): circle, tangent (point to arc, arc to arc), tangent to an arc at an angle,
  parallel line, split arc, mirror, blend between two points.
* App changes from this: the thread "angle" field is now ALPHA-T's infeed angle (default 0, written to SD1 G76 A;
  non-zero was refused until there was a sample; sampled and supported since Progress 38); parts saved with the old 60 load as 0.

## Progress 38 (2026-10-13 night): the German manual's options, sampled
Samples (cloud run, scripts in cloud-run/samples/scripts/manual_de, README there with the exact menu values) are in
`samples/alphat_v12`; every one is checked by `tests/test_post.py` FromSD1 and `tests/test_sd1_write.py`.
* Area clear FOLLOW YES (AREA_follow_contour, SD1 G71 E300): after a pass reaches the profile it feeds along the
  profile towards the chuck to where the previous pass stopped (X56 at Z-25: up to X60 Z-25, along X60 to Z-50), then
  the usual 45-degree retract. The first pass goes up to the top of the profile (X100 Z-50).
* Area clear RETRACT DIST (AREA_retract2, SD1 R2): the pass retract and the SD1 lead-out use it; default 1.
* Finish FACE (FINISH_face, the EXTERNAL item cycles EXTERNAL / INTERNAL / FACE): #127=16, SD1 G70 E2; the same pass
  as external with the 45-degree pull-off towards the axis (X99.718 instead of 100.282).
* Drilling RETRACT DIST (DRILL_retract1): the back-off after each peck and at the bottom (default 2). FULL RET YES
  (DRILL_fullret, distance left at 2): back off 1, rapid out to the start Z, rapid back in to 0.5 above the last
  depth. Half / quarter of the distance is inferred from that one sample. The SD1 has no parameter for either; the
  importer reads them from the listed moves.
* Threading ANGLE (THREAD_angle30, SD1 A30): only the start Z of each cut moves, by depth_n * tan(angle / 2) towards
  the chuck, with depth_n the exact first * sqrt(n) (not the rounded diameter); the real Z is rounded to 3 decimals.
  SPRING PASS 2 (THREAD_spring2, SD1 K2): the #127=21 block has #120=2 and two cuts at the final diameter.
* Grooving FRONT -> FACE (GROOVE_face, SD1 L210, DATUM becomes BTM/TOP, Z-SHF becomes X-SHF): face grooving. Plunges
  along -Z from the start Z to the shallowest profile Z under the tip, stepping out from the smallest diameter by the
  stepover, the last clamped to the largest diameter - 2 x tip width; back off 1 in +Z, rapid to the start Z. The
  older GROOVE_back (L210 with the whole turned profile picked) is the same option: ALPHA-T plunges X32..38 to Z-30
  through the dia-40 part there; the app's plunges stop on the first face under the tip (DIFFERS_ON_PURPOSE).
* Picking from the chuck end: ALPHA-T's area clear then only skims the front face (AREA_towards_from_chuck_end, H10)
  and face clear makes one cut at the shoulder (FACE_towards_from_chuck_end, H1). Face clear picked from the axis
  (FACE_identified_inside_out) is the default. AWAY picked from the chuck end: area AWAY feeds to Z0 and pulls off
  into -Z (AREA_away_from_chuck_end), face AWAY runs Z-48 -> 0 but cuts to X56.8 inside the dia-60 shoulder
  (FACE_away_from_chuck_end), thread AWAY is identical to the front-picked one. So the app cuts area and face clear
  as if picked from the front (contour.front_first) and still refuses AWAY.
* No crest height item on this ALPHA-T's threading menu. MIRROR IMAGE asks nothing: it mirrors the whole drawing
  about the spindle axis (X100 Z-50 -> X-100 Z-50) and asks "Is this correct?"; the app now does the same.

## Progress 39 (2026-10-14): writing .SDG drawings, program units
* .SDG writer (`app/alphacam/io/sdg_write.py`): every sample's segment table, point arrays, billet + jaws layer and
  settings come back byte for byte (inch drawings: float32 last-bit differences only). Point numbering: a new
  element's end point is numbered before its start point (AREA1: line 0 = P1 -> P0), shared points are reused.
  The billet layer is 18 lines in a fixed order (outline, then each side's three jaw steps). Settings view:
  scale = 425 / max(dia + 30, length + 30), [1] = -(534 / scale - (length + 30)) / 2, [2] = [13] - 5 / scale,
  [13]/[15] = Xref/2 -/+ (dia + 30) / 2, [14] = length + 30. The rest of the 0x2700 table after its 18 entries and
  of the 0x4B00 table (an empty third layer, header 0x3000) is leftover screen memory with ALPHA-T's menu texts,
  identical in every sample; written as zeros. The .SDT of a drawing without text: word 0 = element count, word 1
  = 18, words 112, 40, 76 at 0x80, 12 at 0xCC. The reader now divides the billet by 25.4 in inch drawings (it is
  stored in mm).
* Program units: ALPHA-T's "Select Units To Store Output In" (samples/alphat_units, cloud run 2026-10-13 late).
  ALPHA-T computes in the drawing's units and converts each value of the .PRG at the end, rounding the real value
  to the output decimals before the x2 scaling (Z-25 mm -> 0.9843 -> Z-1.9686; X96 -> radius 1.8898 -> X3.7796);
  the feed is converted too (0.15 mm/rev -> #130=0.0118). The .SD1 stays in the drawing's units (G30, F0.15, mm).
  The app does the same; remaining differences: one 0.0002 value (metric drawing, inch program) and ALPHA-T's own
  0.002 low Z on an inch drawing with a metric program. Area RETRACT DIST in an inch drawing is entered in inch
  (SD1 R0.0079).
* ALPHA-T bug seen in that run: any drawing ALPHA-T retrieves (its own too) loses its reference: SD1 G92X0Z0 and
  the contour shifted by +163.3803 in X and +120 in Z, while the typed tool start stays; programs made on a
  retrieved drawing are unusable. Only freshly drawn parts give correct programs. App-written .SDG files load and
  redraw in ALPHA-T (APPMET2, APPARC, APPBIL80); a finish pass made on APPMET2 is byte-identical to the same pass on
  ALPHA-T's own MET2.

## Progress 40 (2026-10-14): first Manual Ops backup from the 400T, and what a restore sends
* Backup (lathe -> PC, the app's Back Up Manual Ops, 7E2 9600): 4871 bytes in ~40 s, 25 operations. The control
  sends **DC2** first and **DC4** last (as the Power Mate 0 manual says for punch output) and **no '%' at all**:
  `12 | O1001 LF ... M29 ... 25 x "G65P63A..B..C..I..J..K..I.." ... M30 | 14`. Every EOB is
  `LF NUL NUL NUL LF LF NUL NUL NUL CR LF` (feed characters and CR from the control's EOB/feed settings). It pauses
  after each operation (the lathe shows the program number), several seconds each. The first app version stopped
  at a 10 s pause and threw the bytes away; the app now ends at DC4 or the second '%', has no idle limit, and keeps
  partial data (Stop and Keep).
* ATMANOP.EXE (Alpha-T V1.2) decompiled with REA's Ghidra DOS path (Ghidra 12.1.4, MzLoader, x86 real mode; data
  segment at linear 0x1a080; sha256 851b9f04...). Save is FUN_1122_000e (linear 0x1122e): read a character from the
  port (FUN_1975_000d), write it raw to the file, end at the **second '%'** or when the operator presses **Esc**;
  a read timeout (status 0x0a) just waits again (FUN_1122_05c2); no DC2/DC4 handling, no idle limit; a file with no
  bytes is deleted. On this lathe (no '%') ALPHA-T's save therefore ran until the operator pressed Esc and kept
  everything, DC2 ... DC4 and the NULs included: what the app's Stop and Keep does, and the app's end at DC4 does by
  itself. Load is FUN_10c5_000a (0x10c5a): fgetc to EOF, each byte through the parity table (FUN_10c5_05ac) and out
  (FUN_10c5_0510), Esc aborts, then it waits for the transmit queue to drain; nothing is added or stripped, so a
  restore sends the saved file byte for byte, as the app does. (The two `cmp al,25h` found by byte search earlier
  are in printf and a DOS set-vector wrapper; the save's '%' test is a compare on the stored character.)
* Restore (PC -> lathe) sends the file as saved (ATMANOP LOAD did the same: raw bytes, parity table, LF kept).
  FANUC reader rules (Series 21 operator's manual B-62704EN/03, ch. 12.1 and appendix A): NUL and CR are not
  registered; codes not in the ISO table (DC2, DC4) are ignored when their parity is right (the UART adds even
  parity on 7E2); everything before the first LF is label-skipped. The control's own output is therefore valid
  input. Not yet seen on the machine: whether the Harrison restore stops at M30 without a '%' (reading normally
  ends at '%'), and whether it replaces an existing O1001. Try it only with this lathe's own backup, after a fresh
  backup, and watch the screen; Reset ends a read that waits.
