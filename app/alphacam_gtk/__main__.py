"""python -m alphacam_gtk [PART.alcam | DRAWING.SDG] [--select N]"""

import sys

from .application import main

sys.exit(main(sys.argv))
