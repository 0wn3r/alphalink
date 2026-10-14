# End-to-end UI tests

The installed Flatpak in a private, headless GNOME session, driven with real input. They run at the
Surface Pro 7's logical sizes: 2736×1824 at 200 % gives 1368×912 landscape, 912×1368 portrait and 684×912
for half the screen.

```
flatpak-builder --user --install ...            # build and install the app first (see docs/HANDOVER.md)
python3 -m tests.e2e.run                        # all 29 tests, each in its own process, 3 at once (about 5 minutes)
python3 -m tests.e2e.run Touch SmallWindow      # some classes or test names
python3 -m tests.e2e.run -j 1 FileDialogs       # one at a time
```

Screenshots of every step go to `app/e2e-out/<test>/` (git-ignored). Set `ALPHALINK_E2E_CURSOR=1` to draw
the pointer in them.

## How it works

- **`harness.py`** starts the session with its own HOME and XDG directories, its own D-Bus session bus, PipeWire,
  and `gnome-shell --headless --virtual-monitor WxH`. The keyring and hot corners are switched off, and the keyboard
  layout is US.
  - The app starts with default settings every time.
  - Your desktop, keyring and app settings are never touched. Only the Flatpak installation is shared, read only.
- **`ei.py`** sends input through [libei](https://gitlab.freedesktop.org/libinput/libei), the input emulation
  protocol of GNOME and other Wayland compositors. Mutter's remote desktop session hands out an EIS socket
  (`ConnectToEIS`), and the client gets real virtual devices from it:
  - a keyboard that sends evdev key codes;
  - an absolute pointer with buttons;
  - a multi-touch touchscreen, which makes two-finger pinch tests possible.

  It uses ctypes over the system's libei.so.1, so it needs no headers and no Python bindings.
- **`remote.py`** grabs the screen through Mutter's screen cast API and PipeWire. It also keeps the older remote
  desktop `Notify*` D-Bus input as a fallback (`ALPHALINK_E2E_INPUT=dbus`).
- **`a11y.py`** finds widgets by their accessible name (AT-SPI), as a screen reader does. A test therefore also checks
  that controls have names.
  - Buttons and menu items are pressed through their accessible action. GTK 4 reports header bar widgets' positions a
    few pixels off, and its popover menu items have no names.
  - The drawing, list rows and the hold-to-start buttons get real pointer and touch events at their positions.
- **The send test** runs over a `socat` pty pair standing in for the lathe. The app is started with
  `ALPHALINK_TEST_PORTS=<pty>`, which makes it offer only that port, never a real adapter. The test checks that
  the program arrives byte for byte.

## What they cover

- **Keyboard:**
  - Training exercise 1: drawing, chamfer and fillet by keys.
  - The cursor keys against a resting mouse.
  - Undo and redo.
  - The simulation pass by pass.
- **Mouse:**
  - Exercise 2: the + menu, and the Program page against the posted program.
  - The main menu order and the Keyboard Shortcuts window.
- **Touch (libei touchscreen):**
  - Line by taps and the on-screen keypad.
  - Picking a contour by taps.
  - Touch Lock.
  - One-finger pan and two-finger pinch zoom.
  - An editor switch.
  - The hold-to-send button.
- **Surface layouts:**
  - Half screen (the editor takes the page).
  - Portrait.
  - The real 200 % scale (a 2736×1824 monitor).
  - Turning the tablet while the app runs (Mutter's DisplayConfig API).
- **Files:** the real Open, Save As and Export dialogs (GNOME's file chooser portal), with the exported program
  checked byte for byte.
- **The lathe, faked:**
  - A socat pty with a fake control: the program arrives byte for byte.
  - Nothing goes out before DC1.
  - DC3 (0x93 and 0x13) stops the send within 10 characters (FANUC alarm 087), and DC1 resumes it.
- **ALPHA-T itself (local only):** the DOS program in DOSBox-X inside the session, driven through libei.
  - It opens the app's .SDG and shows the same profile as for its own drawing.
  - A part drawn in the app and saved with Save as ALPHA-T Drawing… opens without an error.
  - These tests need `~/alpha-work/vendor/AlphaT`, or `ALPHALINK_ALPHAT=<dir>`, and dosbox-x. They use a scratch
    copy, so the vendor folder is never touched.
- **English and Ukrainian.**

## Bugs these tests found (2026-10-14)

- Enter in the chamfer/fillet size field did not apply the size. A spin button's Enter only commits the value.
- Quick touch taps at different points counted as a double tap, so with the Line tool every second point was lost.
- A narrow window opened on the operations list when a part was given at start (before the breakpoint applied).
- In portrait (912 px wide) the page tabs were cut to "Prog…" and "Mac…", so the narrow layout now starts at 1000.
- The drawing had no accessible name, and a tap with the Select tool did not show its position in the read-out.
- With libei input: a pointer that had entered the drawing but not moved since counted as sitting at 0,0. The next
  motion event GTK sent then took the cursor back from the arrow keys, and the read-out showed the pointer while the
  cross showed the key cursor.
- Touch Lock never blocked anything: PyGObject hands EventControllerLegacy handlers no GdkEvent, so the guard
  raised and every touch went through.
- Not ours, and still open upstream: GTK 4 (4.22 and the GNOME 51 runtime) leaves libadwaita's AdwSpinRow out of the
  accessibility tree. The same happens in a minimal app. Screen readers cannot reach the editor's number rows, so the
  tests use the rows' switches instead.
