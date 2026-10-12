# Handover: AlphaLink / Harrison Alpha 400T (2026-10-12)

From session `claude-86` to the coordinator. Repo `github.com/0wn3r/alphalink`, `main` = `7e4ed7e` (pushed by the user)
plus this file.

## Goal
Get CAM programs from a PC into a Harrison Alpha 400T lathe (FANUC Power Mate 0, Harrison macros B703-0703).
Then replace the DOS CAM (ALPHA-T / AlphaLink V1.2) with a GNOME app.

## State
| Track | Status | Next |
|---|---|---|
| Right software | ALPHA-T (DOS) is the CAM for the T control. ALPHACAM V1.81 and AlphaWin-S are for other Alpha models; do not use them. | - |
| Serial settings | 9600 7E2, XON 0x11 / XOFF 0x13, CRLF kept. Tool: `tools/alphalink.py send-alphat / recv-alphat`. | - |
| **Cable** (blocking) | The PL2303 adapter loopback is OK. With pins 2/3 crossed in the machine plug, nothing arrives. Measured on the machine DB25 (to pin 7): pins 2 and 3 = -10 V, pins 5 (CTS) and 6 (DSR) = 0 V, so the control is "not ready". The user is rewiring: bridges 4-5 and 6-8-20 in the machine plug (manual p.61). | Re-measure 5/6/8 (should be positive). Then the read-only test: `serial_diag.py PORT listen 60` while the lathe does Manual Ops > Edit > Save. Then `recv-alphat` backup, load it back, and CAM load of `MET2_finish_metric.PRG` with **no cycle start**. |
| PRG format | Every op sampled with ALPHA-T under DOSBox-X: `samples/alphat_v12/`. `#127` = 1 tool, 6 area, 8 face, 9 groove, 14 finish, 18 drill, 20 thread. Spec in `data/program_formats.json`, `docs/RE_NOTES.md` Progress 15-25. | - |
| Z ×2 | Z, feed and thread pitch are written ×2 in the PRG; X 1:1; SD1 unscaled. Probably intended for the T control. | Check on the control (CAM load, graphic/Z display) before any cutting. |
| Macro risk | Programs not made by ALPHA-T corrupt the T macros. Restoring needs MACROS.MOP, which we do NOT have. | Back up Manual Ops first. Ask Harrison/dealer for B703-0703. |
| GNOME app | Design draft `app/DESIGN.md` (Python + PyGObject + libadwaita). No code. | M0: model + `.alcam` JSON + SDG import + PRG parser. M1: PRG writer byte-identical to the MET2/AREA1 samples. |

## Environment
- The user's computer: `~/alpha-work/` (cleaned up 2026-10-12, usable as a Cowork project folder). Layout:
  - `alphalink/`: clone of this repo
  - `re-notes/`: working notebook, samples, `_shots/` and working copies of `tools/`
  - `vendor/`: Alpha181, AlphaT, Alpha-t, Alpha-w1 and the manuals (never modify)
  - `tools-bin/dosbox-x`
  - `_to_delete/`: old bundles, tgz, .deb packages and the dosbox-x source, for the user to remove
  - `HANDOVER.md`
- Automation: `tools/atsession.sh` (Xvfb + DOSBox-X + xkeys, scratch copy `~/atwork`), with scripts in `tools/ac_scripts/at_*.ks`.
- Quirks:
  - xkeys drops 'A' in typed names.
  - After a CAM op, go straight to EXIT DRAWING.
  - Startup sometimes needs a longer first sleep.
- Rules: the user allows all tasks, use 8 build threads, never modify vendor folders, no deleting in the connected folders
  (use `_to_delete`).
- GitHub: this session can read but not push (repo not in session sources). Hand-off so far: git bundle, then the user pushes.
- Vendor binaries and manuals are not in the repo; see `docs/VENDOR_FILES_SHA256.txt`.
