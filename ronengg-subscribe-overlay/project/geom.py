"""Measured geometry of the source artwork (2000x668 px source coordinates)."""
import numpy as np

SRC_W, SRC_H = 2000, 668

# Logo ring (fitted)
RING_C = (357.8, 348.65)
RING_R = 266.9

# Main subscribe button
BTN_OUT = (629, 226, 1678, 478, 54)      # outer frame rounded rect x0,y0,x1,y1,r (overlaps box by 4px: no seam)
FACE = (642.5, 246.5, 1655.5, 449.5, 40)  # glossy purple face

# Bottom text bar (neon centre line hexagon); dilated for outer edge
BAR_LINE = [(765, 451), (1775, 451), (1804, 503), (1771, 551), (770, 551), (731, 503)]
BAR_DILATE = 9.5

# Bell box
BOX_OUT = (1674.0, 240.5, 1960.5, 496.5, 50)
BOX_AXIS = 1818.0

# YouTube play tile inside the face
TILE = (663, 281, 885, 433, 34)
TRI = [(747, 322), (747, 390), (814, 356)]

# click targets
CLICK_SUB = (1300, 330)
BELL_PIVOT = (1808, 290)
BELL_CENTER = (1817, 372)
