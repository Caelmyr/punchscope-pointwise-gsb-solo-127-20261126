"""Deterministic synthetic work samples for the inspection desk."""

import numpy as np
from PIL import Image, ImageDraw

from .photo import png

WIDTH, HEIGHT = 900, 535
BOX = {"left": 83, "top": 101, "right": 817, "bottom": 449}


def lighting():
    yy, xx = np.indices((HEIGHT, WIDTH))
    noise = np.random.default_rng(19).normal(0, 1.6, (HEIGHT, WIDTH))
    backlight = 217 + 17 * xx / WIDTH - 16 * yy / HEIGHT + noise
    metal = 53 + 23 * xx / WIDTH + 5.5 * np.sin(xx / 46) + noise
    return backlight, metal


def make(sample):
    backlight, metal = lighting()
    silhouette = Image.new("1", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(silhouette)
    border = [(61, 90), (238, 86), (302, 82), (386, 87)]
    if sample == "panel":
        border.extend([(392, 88), (401, 124), (417, 125), (428, 86)])
    border += [(530, 72), (618, 76), (676, 68), (753, 75), (839, 66), (839, 473), (61, 473)]
    draw.polygon(border, fill=1)

    openings = Image.new("1", (WIDTH, HEIGHT))
    punch = ImageDraw.Draw(openings)
    centers = [123, 192, 263, 334, 406, 477, 552, 624, 700, 776]
    for row, center_y in enumerate((146, 214, 285, 352, 417)):
        for col, center_x in enumerate(centers):
            if sample == "staggered" and row == 2 and col in (4, 7):
                continue
            dy = (col % 3 - 1) * 2
            radius = 15 if row != 1 else 12
            if sample == "staggered" and col > 4:
                center_x -= (col - 4) * 3
            if sample == "slots" and row in (1, 2, 3):
                end = center_x + 17
                punch.rounded_rectangle((center_x - 19, center_y - 10 + dy, end, center_y + 10 + dy),
                                        radius=8, fill=1)
                if col in (2, 5, 7):
                    punch.ellipse((center_x + 6, center_y - 12 + dy, center_x + 18, center_y + 4 + dy), fill=0)
            else:
                punch.ellipse((center_x - radius, center_y - radius + dy, center_x + radius,
                               center_y + radius + dy), fill=1)

    if sample == "staggered":
        punch.ellipse((437, 281, 445, 290), fill=1)
        punch.ellipse((609, 283, 617, 292), fill=1)
    if sample == "panel":
        punch.ellipse((494, 109, 516, 125), fill=1)

    bright = ~np.asarray(silhouette, dtype=bool) | np.asarray(openings, dtype=bool)
    field = np.where(bright, backlight, metal)
    if sample == "glare":
        yy, xx = np.indices((HEIGHT, WIDTH))
        strip = (xx > 434) & (xx < 583)
        field = np.where(strip & bright, field - 59 + 7 * np.sin(yy / 18), field)
        field = np.where(strip & ~bright, field + 62 + 37 * np.exp(-((yy - 287) / 18) ** 2), field)

    image = Image.fromarray(np.clip(np.dstack((field, field * .98, field * .91)), 0, 255).astype(np.uint8))
    options = {"box": BOX, "scan_y": 286, "threshold": 158, "min_area": 28,
               "scale": 6.4, "edge_margin": 115}
    names = {"panel":"整板练习", "staggered":"缺孔与偏位练习", "slots":"长孔练习", "glare":"眩光练习"}
    return names[sample], image.size, png(image), options
