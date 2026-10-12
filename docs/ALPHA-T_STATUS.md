# Alpha-T (AlphaLink V1.2 for Harrison Alpha "T") - status and resume plan

State as of 2026-10-12. Target machine: Harrison Alpha 400T, control FANUC Power Mate 0
(A02B-0166-B591, 2002-11), Harrison macro package B703-0703.

## What is known

| Area | Status | Where |
|---|---|---|
| Which CAM software is right for the 400T | ALPHA-T (DOS, AlphaLink V1.2/1.21). NOT DOS ALPHACAM V1.81 (`.600`, old Alpha) and NOT AlphaLink for Windows / AlphaWin-S (Alpha-S/U). | RE_NOTES Progress 14-19, 23 |
| Manual warning | Programs not created with Alphalink T corrupt the T macros; fix = reload B703-0703 (`MACROS.MOP`, ATMACROS). We do NOT have that file. | RE_NOTES Progress 16 |
| Serial line, all T modules | 9600 7E2, XON 0x11 / XOFF 0x13 (measured in DOSBox-X) | `data/line_settings.json`, Progress 15, 22 |
| Cable | DB9 2-2, 3-3, 5-7 + bridges; a plain 9-to-25 adapter does not work (no crossover) | `data/cable.json`, Progress 17, 20 |
| PC tool | `tools/alphalink.py send-alphat` / `recv-alphat`, `--char-delay`, `--wait-xon`; `tools/serial_diag.py` | tools/ |
| Real-machine test | All sends on 2026-10-12 went through the wrong adapter -> nothing reached the control. Loopback of the PL2303 adapter OK. Measured on the machine DB25: pins 2/3 -10 V, pins 5 (CTS) / 6 (DSR) 0 V -> control not ready; fix = bridges 4-5 and 6-8-20 in the machine plug. | Progress 14, 20, 25 |
| .PRG format | sample `samples/alphat_v12/MET2_finish_*`; structure in `data/program_formats.json` | Progress 15 |
| Z x2 | every Z, every feed and the thread pitch in .PRG = 2 x drawing value (mm and inch); X (diameter) 1:1; SD1 not scaled. Consistent across finish/area/drill/thread/move samples -> most likely intended for the T control, still verify on the control | Progress 15/16 + below |
| Windows transfer prog (SerioAlpha) | config format decoded; CAM channel = 7E2 XON/XOFF, no wait | Progress 13, 21 |

## Next steps on the machine (safe order)
1. Null-modem adapter or manual cable in place. `serial_diag.py PORT loop` passes on the adapter.
2. **Read-only test:** lathe Manual Operations page -> Edit -> Save; PC `alphalink.py -v recv-alphat PORT manops_backup.MOP`.
   Proves cable + settings and gives a backup.
3. Load the same MOP back (`send-alphat`), check pages are unchanged.
4. Only then: CAM load (CAM menu -> 4 CAM-DATEN LADEN -> arrow) of `MET2_finish_metric.PRG` with
   `send-alphat --char-delay 2`; **do not cycle-start**; check on the control graphics whether Z is shown 1:1.

## Z x2 hunt (in progress)
Method: patch float32/float64 constants 2.0 -> 1.0 in the unpacked ALPHA-T (`tools/patch_consts.py`,
unpacked main + original overlays = `ALPHA-T_OVL.EXE`, built from `tools/unexepack.py` + appending the
overlays at the next 512-byte boundary from file offset 0x3F200 of the original), regenerate MET2 with
`tools/ac_scripts/at_met2f.ks`, compare the .PRG.
Results so far (indices into the 59 float32 2.0 occurrences / 13 float64):
* f64 all 13 -> no change.
* f32 0..14 -> no change.
* f32 0..29 -> X doubled in the PRG (X40 -> 80), Z unchanged => one of f32 15..29 (0x48704, 0x60e28 .. 0x613bc;
  0x60xxx-0x61xxx are inside overlay 17 at file offset 0x5FA00) is an X "/2" in the writer path.
* f32 30..58 (overlay 17) -> geometry broken (CAM maths), not conclusive.
* Post experiment: ALPHTCNC.PRG Z register field 9 (1 -> 0) only makes Z modal (omitted when unchanged).
* f32 19/20 (0x60f2c / 0x60f5c = main load image 0x5d61c / 0x5d64c) are the X constants (contour X doubled when patched).
Next: find the segment base of 0x5d61c via the relocation/`mov ds` that loads it, disassemble the writer around it (overlay 17), see how Z is formed.
Alternative: compare with a known-good Alpha-T program from a real installation if one turns up.

## Samples / operations
* Samples (re-notes/alphat_samples, generated with the scripts in tools/ac_scripts/at_*.ks):
  MET2_finish_{metric,inch}, AREA1_metric, DRILL_DWELL_metric, THREAD_metric, MOVE_RAPID_metric, GROOVE_metric, FACE_metric.
* #127 op kind: 1 tool block, 6 area clear, 8 face clear, 9 grooving, 14 finish, 18 drilling, 20 threading.
* Drilling: pecks written out as G1 in/out moves; dwell G4P<revs*1000>.
* Threading (pitch 1.5, OUTER X40 / INNER X20 identified): #120=passes, #130=3. (pitch x2), each pass
  G32 X Z F3. then U21. W-3. retract (starts one pitch before the end, as in the V1.21 update notes).
* Move/rapid to cursor: appended to the current tool block, no N20 header.
* Grooving: is NOT contour-driven like area clear. Working key sequence (tools/ac_scripts/at_groove.ks):
  STEPOV/TIPWD -> stepover 2, tip width 3; EXECUTE; answer 1 (area clear) / 2 (finish); dwell revs Enter;
  "Input complete" Enter; wait ~25 s; EXIT DRAWING. Sample saved as GROOVE_metric (#127=9).
* Face clear: sampled (FACE_metric, #127=8). All CAM ops now have a sample.

## Other open items
* Meaning of #120..#140 and M60-M71 (need the macro listing B703-0703 or more samples: area clear, groove,
  thread, drill, dwell, move-to-cursor, rapid). Generate samples with atsession.sh (scripts in tools/ac_scripts/at_*.ks).
* ALPHA-T SDG/SD1 differences vs V1.81; ATOOLIB.IM1/IM2 (new tool image files).
* ATTOOLS needs WORKLIB.* copy in DOSBox-X (COMMAND.COM copy fails).

## Environment needed to resume
Vendor files are NOT in this repo (copyright). Identify them by `docs/VENDOR_FILES_SHA256.txt`.
Expected local layout (as on the original workstation, `~/alpha-work`):
```
alpha-work/Alpha181/        DOS ALPHACAM V1.81 install (original, never modified)
alpha-work/AlphaT/          ALPHA-T install set + unzipped ALPHTZIP.EXE / ALPHTMES.EXE
alpha-work/re-notes/        = this repo's docs/tools/samples (+ unpacked/, alphat_unpacked/, _shots/)
alpha-work/tools-bin/dosbox-x   DOSBox-X 2026.10.01 full edition, heavy-debug build (dbgtr/dbgreg serial logs)
```
Automation: Xvfb + `tools/xkeys` (build: `cc -o xkeys xkeys.c -lX11 -lXtst`), ImageMagick `import` for shots,
`acsession.sh` (ALPHACAM) / `atsession.sh` (ALPHA-T) run on scratch copies (~/acwork, ~/atwork).
Env: METRIC=1 (drawing mm, MES record 0x0D), MMETRIC=1 (.600 output mm, record 0x0C), PRE="cmd", RUN=name, EXTRACONF="dosbox-x conf lines".
Known quirk: xkeys drops the letter 'A' in typed file names.
