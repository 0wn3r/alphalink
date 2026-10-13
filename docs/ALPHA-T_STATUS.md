# Alpha-T (AlphaLink V1.2 for Harrison Alpha "T") - status

State as of 2026-10-13. Target machine: Harrison Alpha 400T, control FANUC Power Mate 0
(A02B-0166-B591, 2002-11), Harrison macro package B703-0703. Manual turret, with tool memory on the control.

## What is known

| Area | Status | Where |
|---|---|---|
| CAM software for the 400T | ALPHA-T (DOS, AlphaLink V1.2/1.21). NOT DOS ALPHACAM V1.81 (`.600`, old Alpha) and NOT AlphaLink for Windows / AlphaWin-S (Alpha-S/U). | RE_NOTES Progress 14-19, 23 |
| Replacement | The GNOME app in `app/` (AlphaLink CAM) writes .PRG and .SD1 files byte-identical to ALPHA-T for every sample except the refused AWAY ones and three deliberate fixes (`app/tests/test_post.py`), and sends them. | `app/DESIGN.md`, Progress 27-39 |
| Manual warning | Programs not created with Alphalink T corrupt the T macros; fix = reload B703-0703 (`MACROS.MOP`, ATMACROS). We do NOT have that file. | Progress 16 |
| Serial line, all T modules | 9600 7E2, XON 0x11 / XOFF 0x13; the control sends XON when it is ready. | `data/line_settings.json`, Progress 15, 22, 29 |
| Cable | DB9 2-2, 3-3, 5-7, with the manual's bridges in both plugs (machine DB25: 4-5 and 6-8-20). Without the bridges the control reports "not ready" (CTS/DSR off). | `data/cable.json`, Progress 17, 20, 25, 29 |
| Real machine | CAM load works (2026-10-13): MET2 and MOVE_RAPID were loaded and run. `#132` is the program number shown on the control; `O2` is constant. | Progress 29 |
| Z / feed x2 | Every Z and feed in the .PRG is 2 x the real value and the T macros halve them (confirmed on the control's run screen). Thread pitch x2 follows the same rule but has not been seen on the control. X (diameter) is 1:1; the SD1 is unscaled. | Progress 15, 16, 29 |
| Control screens | English menu names: CAM menu 4 "LOAD CAM FILE"; Manual Ops ops-file icon -> 1 "OP. SAVE/RESTORE" (-> lathe to PC, <- PC to lathe). | Progress 35 |
| Formats | .PRG structure `data/program_formats.json`; .SD1 listing rules Progress 34; samples `samples/alphat_v12/` (all operations and their options). | Progress 15-34 |

## Safe order on the machine
1. Back up Manual Ops before anything else (app: Machine page ▸ Back Up Manual Ops; lathe: OP. SAVE/RESTORE ▸ →).
2. Load programs only from ALPHA-T or this app (same bytes).
3. After a CAM load, check the program on the control, and run new programs with the feed override turned down.
   The app never starts a cycle.

## Open items
* Thread pitch x2 on the control display (first threading job).
* Every ALPHA-T option is sampled (Progress 38: follow, retract distances, finish FACE, full retract, thread angle,
  spring passes, face grooving). The app refuses area / face / thread AWAY, because ALPHA-T's own output for it is
  unsafe on these parts, whichever end the contour is picked from (Progress 36, 38).
* Program units other than the drawing's (Part Settings ▸ Program units) and writing .SDG drawings (main menu,
  or on export via Preferences) are done; both wait for an ALPHA-T check (samples asked for: does ALPHA-T convert
  the feed with the units; do app-written .SDG files open in ALPHA-T).
* Meaning of #120..#140, M60-M71 and #1005/#1006/#1009 (needs the B703-0703 macro listing).
* ATOOLIB.IM1/IM2 (tool images).

## Environment
Vendor files are NOT in this repo (copyright). Identify them by `docs/VENDOR_FILES_SHA256.txt`.
Local layout (`~/alpha-work`):
```
~/alphalink/                this repo (outside alpha-work)
alpha-work/vendor/          ALPHA-T, ALPHACAM V1.81, AlphaWin and the manuals (never modified)
alpha-work/re-notes/        working notebook and scratch copies
alpha-work/tools-bin/dosbox-x   DOSBox-X 2026.10.01, heavy-debug build
alpha-work/_to_delete/      files for the user to remove (nothing is deleted directly)
```
New ALPHA-T samples: `~/alpha-work/re-notes/tools/atsession.sh` (the repo copy `tools/atsession.sh` expects to sit in
alpha-work/re-notes/tools next to the built `xkeys`; Xvfb + DOSBox-X, scratch copy `~/atwork`) with key
scripts in `tools/ac_scripts/at_*.ks`. Known quirk: xkeys drops the letter 'A' in typed file names; after a CAM
op go straight to EXIT DRAWING.
