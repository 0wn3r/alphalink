# Design Review: AlphaLink CAM

Done 2026-10-14 with the Design plugin's critique, accessibility (WCAG 2.1 AA) and UX copy checklists. I looked at every page
in English and Ukrainian, in dark and light themes, at three window sizes: wide (1368 px), the narrow layout (≤ 900 px)
and the small layout (≤ 700 px, touch). Items marked **Fixed** are in the same commit as this file.

## Overall Impression

The app reads as a GNOME tool. The drawing dominates the Part page, the next step is obvious (Export PRG, Send to the
Lathe), and hold-to-start protects the machine. The biggest problem was the small window: with an operation open,
the editor pushed itself off the screen.

## Usability

| Finding | Severity | Recommendation |
|---|---|---|
| Small window (≤ 700 px): opening an operation squeezed the drawing and pushed the editor, the main menu and the Machine tab off the screen | 🔴 Critical | **Fixed:** below 700 px the editor takes the whole page while it is open. Close (×) or Pick Contour brings the drawing back |
| Narrow window opened on the operations list with no menu and no page tabs, so it looked like the drawing was missing | 🟡 Moderate | **Fixed:** a narrow window opens on the drawing, and Back shows the operations |
| The operation editor stayed open beside the Sheet, Tools and Machine pages, where it does nothing and takes a third of the width | 🟡 Moderate | **Fixed:** the editor shows only on the Part and Program pages (on Program it follows the highlighted block) |
| Keyboard: the arrows did nothing until the drawing had the focus, and nothing showed which widget had it | 🟡 Moderate | **Fixed** earlier today: the drawing takes the focus, and an accent frame shows it |
| Keyboard: Enter did nothing with Chamfer, Fillet or Corner | 🟡 Moderate | **Fixed** earlier today |
| Serial port row truncates "(not present)" | 🟢 Minor | Leave: the subtitle below says "Port not found…", which carries the meaning |
| Restore Manual Ops explains a lot in a small subtitle | 🟢 Minor | Leave: it is a warning about corrupting the control's macros; it must be read once |

## Visual Hierarchy

- **What draws the eye first:** the drawing with the toolpaths. That's right for a CAM tool.
- **Reading flow:** operations (left) → drawing → editor (right) → simulation → drawing bar. It matches the order of the work.
- **Emphasis:** the accent colour is used for the primary action on each page (Send to the Lathe, Create, Apply) and for
  the simulation slider. Restore is the only destructive button and is styled that way.

## Consistency

| Element | Issue | Recommendation |
|---|---|---|
| Row subtitles | Half started lower case ("per revolution", "off = internal"), the Machine page used sentences | **Fixed:** all subtitles use sentence capitalization, in both languages (HIG) |
| Key hints | "D: length,angle" did not read as a key | **Fixed** earlier today: "Press D to …", "(key B)" |
| Header bar | Suggested-style label button, linked undo/redo | **Fixed** earlier today (HIG pass) |

## Accessibility

- **Color contrast:**
  - **Drawing grid labels:** about 1.8:1 in the light theme. That fails 4.5:1. **Fixed:** they now have their own colour, about 6.4:1 in light and 6.8:1 in dark.
  - **Part outline, feed and rapid moves:** pass 3:1 for graphics in both themes.
  - **Text in the libadwaita widgets:** uses the theme's colours, which pass.
- **Touch targets:** the touch layout uses 48 px buttons (TOUCH.md). In the pointer layout the toolbar buttons are 34 px. That's fine for a mouse, and the app switches to touch by itself.
- **Keyboard:**
  - Every command has a key.
  - Ctrl+? lists all of them.
  - Esc closes popups and dialogs.
  - The focus is visible on the drawing.
- **Screen reader:** icon buttons all have tooltips. GTK uses those as the accessible name, so each is announced.
- **Text readability:** the size is the system font size. The program listing is monospace.

## What Works Well

- Hold to start on Send and Restore, plus the summary before sending. Nothing reaches the lathe by accident.
- The ALPHA-T keys keep an old operator's habits, and the GNOME menus, tooltips and Ctrl+? keep new users from getting lost.
- The editor shows units and meaning under each value ("Finish allowance, radius"). That is where lathe mistakes happen.
- English and Ukrainian are complete, and both languages fit the layouts.

## Priority Recommendations (open)

1. **Show the active tool's key hint on the drawing bar.** A small label like "Line: X type · D length,angle" next to the
   entry would teach the keys without tooltips, which a touch user never sees.
2. **First-run hint.** The empty start screen could link the Training Manual ("New to AlphaLink? Open the Training Manual").
3. **Program page:** give the line numbers (N10, N20…) a muted colour, so the blocks stand out as you scroll. It's minor.
