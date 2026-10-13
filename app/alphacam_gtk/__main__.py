"""python -m alphacam_gtk [PART.alcam | DRAWING.SDG | DRAWING.DXF] [--select N] [--page NAME] [--touch]"""

import sys

from .application import main

sys.exit(main(sys.argv))
