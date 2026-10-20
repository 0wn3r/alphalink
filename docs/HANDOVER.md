# Handover: AlphaLink / Harrison Alpha 400T (2026-10-18)

Repo `github.com/0wn3r/alphalink`; work is pushed straight to `main` (the user allows it, no PRs).

## Goal
Get CAM programs from a PC into a Harrison Alpha 400T lathe (FANUC Power Mate 0, Harrison macros B703-0703), and
replace the DOS CAM (ALPHA-T / AlphaLink V1.2) with a GNOME app.

## State
| Track | Status |
|---|---|
| Lathe link | Works with the manual's cable and bridges. Programs load and run (Progress 29). Details in `docs/ALPHA-T_STATUS.md`. |
| GNOME app | Since 2026-10-16 the app is the Rust build in `rust/` (PRs #6–#10): the same features, the same interface files (`app/alphacam_gtk/ui/`) and help, plus the mouse, keyboard and pen design (`app/POINTER_KEYBOARD.md`) and a decluttered UI. The Python app and core were removed on 2026-10-19; the fixtures they exported stay as the Rust tests' reference. Before that, `app/` (AlphaLink CAM): drawing (with ALPHA-T's construction commands and one-letter keys), all ALPHA-T operations, simulation, setup sheet, tool library, sending, Manual Ops backup/restore, program units, ALPHA-T .SDG save, Ukrainian translation, in-app help and PDF manuals, Flatpak. Since 2026-10-14: touch work from `app/TOUCH.md`, the workflow improvements' quick wins, a safety check before export, standard threads (metric, UNC, UNF) and corners in one go, right-click menu. Status and design in `app/DESIGN.md`. |
| Manual Ops | First real backup works (25 ops, O1001, DC2 ... DC4, no %; RE_NOTES Progress 40). Restore of such a file not yet tried on the machine. |
| Workflow plan | Claude Doc https://claude.ai/code/artifact/8b56ba44-0ceb-4639-bdd9-9030c2ce306a (steps 1-6 done 2026-10-15; left: stock-aware roughing, which needs a dry run on the machine first). |
| Post | .PRG and .SD1 byte-identical to ALPHA-T on every metric sample (`app/tests`) except the 7 refused AWAY samples and 3 that differ on purpose (chuck-end picks, GROOVE_back); sub-micron differences on FACE_slope/FACE_arcs and two inch samples. All listed in `app/tests/test_post.py`. |
| Options | Every ALPHA-T option is sampled. AWAY (area, face, thread) is refused on purpose (ALPHA-T's own output is unsafe); contours picked from the chuck end are cut as if picked from the front. |

## Rules (from the user)
- Use 8 build threads (`--jobs=8`).
- Never modify vendor folders; never delete in the user's folders (move to `~/alpha-work/_to_delete`).
- No serial traffic to the lathe unless the user asks; the app and tools never start a cycle.

## Build and test
- Flatpak (the app): `flatpak-builder --user --install-deps-from=flathub --jobs=8 --install --force-clean BUILD-DIR
  rust/build-aux/io.github._0wn3r.AlphaLinkCAM.json`. A single-file bundle for the Surface: add `--repo=REPO`, then
  `flatpak build-bundle REPO AlphaLinkCAM.flatpak io.github._0wn3r.AlphaLinkCAM`.
- Rust tests: `cd rust && cargo test --workspace --exclude alphacam-gtk` (the post against every ALPHA-T sample,
  through fixtures the retired Python core exported).
- End-to-end tests on the installed Flatpak (headless GNOME session, every input through libei, Surface Pro 7 sizes,
  the app read through its test bus with ALPHALINK_TEST=1, socat ports for the lathe, DOSBox-X running ALPHA-T where
  installed): `cd app && ALPHALINK_E2E_BRANCH=master python3 -m tests.e2e.run -j 3` (`app/tests/e2e/README.md`).
  GitHub Actions builds the Flatpak and runs them on every push.
- Follow the GNOME HIG for every UI change; every UI string needs a Ukrainian translation.
- The Flatpak has the app (`alphalink-cam`) and the command line (`alphacam`). About shows the commit: a build
  without .git (the Flatpak) reads it from `rust/build-aux/commit`, so write it first:
  `git rev-parse --short=12 HEAD > rust/build-aux/commit`.
- Layouts are Blueprint files in `app/alphacam_gtk/ui/`: after editing a `.blp` run `python3 tools/build_ui.py`
  (needs `blueprint-compiler`) and commit the `.ui` it writes.
- After UI string changes: `python3 tools/update_translations.py` (Ukrainian strings in `app/po/uk_translations.py`).
- Vendor binaries and manuals are not in the repo; see `docs/VENDOR_FILES_SHA256.txt`.
- User manuals: Training Manual and Reference Manual (English and Ukrainian) are written as Claude Docs, exported
  to PDF into `app/alphacam_gtk/manuals/` and opened from the help (F1). Screenshots: take them from the app (the Python `tools/manual_shots.py` was removed with the Python app).
  Training: https://claude.ai/code/artifact/c21b95e7-67ca-46aa-8c3d-633ed77f4bd8 · Reference:
  https://claude.ai/code/artifact/b0e78fdb-72a0-4624-910b-cd4c108c830c. After editing a doc, export each tab to PDF
  again and replace `manuals/<training|reference>.<en|uk>.pdf`.
- One-letter keys follow ALPHA-T (`rust/alphacam-gtk/src/hit_keys.rs`); added where ALPHA-T had none: E edit element,
  B Chamfer tool, W Arc tool. O is ALPHA-T's 2 PKT RADIUS (Blend Arc). Only J is still free.
