"""Locate the first stable sheet edge from either outside of the image field."""

import numpy as np

from .apertures import source_gray


def topmost(dark, stable=5):
    height, width = dark.shape
    if height < stable + 1:
        return np.full(width, -1)
    sum_to_row = np.cumsum(np.pad(dark, ((1, 0), (0, 0))), axis=0)
    long_dark = (sum_to_row[stable:] - sum_to_row[:-stable]) == stable
    first = np.argmax(long_dark, axis=0)
    has_transition = long_dark.any(axis=0) & (first > 0)
    return np.where(has_transition, first, -1)


def measure(image, settings):
    box = settings["box"]
    left, right = box["left"], box["right"]
    gray = source_gray(image[:, left:right])
    depth = settings["edge_margin"]
    height = image.shape[0]
    top_end = min(height, box["top"] + depth)
    bottom_start = max(0, box["bottom"] - depth)
    top_start = max(0, box["top"] - depth)
    bottom_end = min(height, box["bottom"] + depth)
    top = topmost(gray[top_start:top_end] < settings["threshold"])
    bottom = topmost(gray[bottom_start:bottom_end][::-1] < settings["threshold"])
    scale = settings["scale"]
    points = [{"x": x + left,
               "top": int(top[x] + top_start) if top[x] >= 0 else None,
               "bottom": int(bottom_end - 1 - bottom[x]) if bottom[x] >= 0 else None}
              for x in range(right - left)]

    heights = [(p["bottom"] + 1 - p["top"]) / scale for p in points
               if p["bottom"] is not None and p["top"] is not None and p["bottom"] >= p["top"]]
    return {"points": points, "valid": len(heights),
            "minimum": round(min(heights), 3) if heights else None,
            "maximum": round(max(heights), 3) if heights else None}
