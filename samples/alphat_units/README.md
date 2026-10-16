# Program units other than the drawing's (ALPHA-T V1.2, cloud run 2026-10-13 late)

MET2 (dia 40/60/100, steps Z-25/-50), tool 3, finish pass then area clear with the menu defaults, tool start X120 Z5.

| Files | Drawing | "Select Units To Store Output In" | Result |
|---|---|---|---|
| UNITS_metricdwg_inchout.* | metric | 1 (inch) | PRG M70 in inch, feed converted (#130=0.0118); SD1 unchanged (G30, mm, F0.15) |
| UNITS_inchdwg_metricout.* | inch (dia 1.6/2.4/4, Z-1/-2, start X5 Z0.2) | 0 (metric) | PRG M71 in mm, #130=7.62; SD1 stays inch (G31, F0.15); ALPHA-T's contour Z comes out 0.002 low |

Checked by app/tests/test_post.py OutputUnits.
