# Handover: AlphaLink / Harrison Alpha 400T (2026-10-13)

Repo `github.com/0wn3r/alphalink`; work is pushed straight to `main` (the user allows it, no PRs).

## Goal
Get CAM programs from a PC into a Harrison Alpha 400T lathe (FANUC Power Mate 0, Harrison macros B703-0703), and
replace the DOS CAM (ALPHA-T / AlphaLink V1.2) with a GNOME app.

## State
| Track | Status |
|---|---|
| Lathe link | Works with the manual's cable and bridges. Programs load and run (Progress 29). Details in `docs/ALPHA-T_STATUS.md`. |
| GNOME app | `app/` (AlphaLink CAM): drawing (with ALPHA-T's construction commands and one-letter keys), all ALPHA-T operations, simulation, setup sheet, tool library, sending, Manual Ops backup/restore, program units, ALPHA-T .SDG save, Ukrainian translation, in-app help and PDF manuals, Flatpak. Status and design in `app/DESIGN.md`. |
| Post | .PRG and .SD1 byte-identical to ALPHA-T on every metric sample (`app/tests`) except the 7 refused AWAY samples and 3 that differ on purpose (chuck-end picks, GROOVE_back); sub-micron differences on FACE_slope/FACE_arcs and two inch samples. All listed in `app/tests/test_post.py`. |
| Options | Every ALPHA-T option is sampled. AWAY (area, face, thread) is refused on purpose (ALPHA-T's own output is unsafe); contours picked from the chuck end are cut as if picked from the front. |

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
  Training: https://claude.ai/code/artifact/c21b95e7-67ca-46aa-8c3d-633ed77f4bd8 · Reference:
  https://claude.ai/code/artifact/b0e78fdb-72a0-4624-910b-cd4c108c830c. After editing a doc, export each tab to PDF
  again and replace `manuals/<training|reference>.<en|uk>.pdf`.
- One-letter keys follow ALPHA-T (`app/alphacam_gtk/hit_keys.py`); added where ALPHA-T had none: E edit element,
  B Chamfer tool, W Arc tool. O is ALPHA-T's 2 PKT RADIUS (Blend Arc). Only J is still free.
