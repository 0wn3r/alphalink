# Handover: AlphaLink / Harrison Alpha 400T (2026-10-13)

Repo `github.com/0wn3r/alphalink`; work is pushed straight to `main` (the user allows it, no PRs).

## Goal
Get CAM programs from a PC into a Harrison Alpha 400T lathe (FANUC Power Mate 0, Harrison macros B703-0703), and
replace the DOS CAM (ALPHA-T / AlphaLink V1.2) with a GNOME app.

## State
| Track | Status |
|---|---|
| Lathe link | Works with the manual's cable and bridges. Programs load and run (Progress 29). Details in `docs/ALPHA-T_STATUS.md`. |
| GNOME app | `app/` (AlphaLink CAM): drawing, all ALPHA-T operations, simulation, setup sheet, tool library, sending, Manual Ops backup/restore, Ukrainian translation, in-app help, Flatpak. Status and design in `app/DESIGN.md`. |
| Post | .PRG and .SD1 byte-identical to ALPHA-T on every metric sample (`app/tests`); a few sub-micron known differences are listed in `app/tests/test_post.py`. |
| Options | Every ALPHA-T option is sampled; AWAY and back grooving are refused on purpose (ALPHA-T's own output is unsafe). |

## Rules (from the user)
- Use 8 build threads (`--jobs=8`).
- Never modify vendor folders; never delete in the user's folders (move to `~/alpha-work/_to_delete`).
- No serial traffic to the lathe unless the user asks; the app and tools never start a cycle.

## Build and test
- Tests: `cd app && python3 -m unittest discover -s tests -t .`
- Flatpak: `flatpak-builder --user --install-deps-from=flathub --jobs=8 --install --force-clean BUILD-DIR
  app/build-aux/flatpak/io.github._0wn3r.AlphaLinkCAM.json`
- After UI string changes: `python3 tools/update_translations.py` (Ukrainian strings in `app/po/uk_translations.py`).
- Vendor binaries and manuals are not in the repo; see `docs/VENDOR_FILES_SHA256.txt`.
- User manuals: Training Manual and Reference Manual (English and Ukrainian) are written as Claude Docs, exported
  to PDF into `app/alphacam_gtk/manuals/` and opened from the main menu. Screenshots: `tools/manual_shots.py`.
