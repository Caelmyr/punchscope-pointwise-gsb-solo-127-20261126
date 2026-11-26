"""Identify witnessed openings and describe native contours."""

from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy import ndimage as ndi

from image_lab.arrays import luminance

from . import split


@dataclass
class Openings:
    gray: np.ndarray
    light: np.ndarray
    labels: np.ndarray
    accepted: np.ndarray
    by_id: dict
    unassigned: np.ndarray = field(default=None)
    applied: list = field(default_factory=list)


def source_gray(image):
    return np.rint(luminance(image.astype(np.float32) / 255.0) * 255.0).astype(np.uint8)


def component_outline(labels, key, x, y, width, height):
    cut = (labels[y:y + height, x:x + width] == key).astype(np.uint8)
    contours, _ = cv2.findContours(cut, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    perimeter = sum(cv2.arcLength(contour, True) for contour in contours)
    distances = cv2.distanceTransform(np.pad(cut, 1), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    _, radius, _, (column, row) = cv2.minMaxLoc(distances)
    return perimeter, (column + x - 1, row + y - 1, radius)


def component_stats(labels, count):
    """Area, centroid, and window per label in one pass, valid after splits
    relabel parts of the map (cv2's own stats would be stale)."""
    height, width = labels.shape
    flat = labels.ravel()
    rows = np.repeat(np.arange(height, dtype=np.int64), width)
    cols = np.tile(np.arange(width, dtype=np.int64), height)
    areas = np.bincount(flat, minlength=count)
    sum_y = np.bincount(flat, weights=rows, minlength=count)
    sum_x = np.bincount(flat, weights=cols, minlength=count)
    return areas, sum_y, sum_x, ndi.find_objects(labels)


def detect(image, settings):
    left, top, right, bottom = (settings["box"][edge] for edge in ("left", "top", "right", "bottom"))
    gray = source_gray(image[top:bottom, left:right])
    light = gray >= settings["threshold"]
    _, labels, stats, _ = cv2.connectedComponentsWithStats(light.astype(np.uint8), connectivity=8)
    accepted = stats[:, cv2.CC_STAT_AREA] >= settings["min_area"]
    accepted[0] = False
    labels, accepted, unassigned, applied = split.apply(
        gray, labels, accepted, settings.get("splits", []), settings["box"])

    areas, sum_y, sum_x, windows = component_stats(labels, len(accepted))
    selected = [int(key) for key in np.flatnonzero(accepted)]
    largest = sorted(selected, key=lambda key: (-areas[key], key))[:500]

    by_id = {}
    for key in sorted(largest):
        slice_y, slice_x = windows[key - 1]
        y, x = slice_y.start, slice_x.start
        height, width = slice_y.stop - y, slice_x.stop - x
        area = int(areas[key])
        center_x, center_y = sum_x[key] / area, sum_y[key] / area
        visible = x > 0 and y > 0 and x + width < gray.shape[1] and y + height < gray.shape[0]
        perimeter, (gauge_x, gauge_y, radius) = component_outline(labels, key, x, y, width, height)
        by_id[key] = {
            "id": key,
            "visible": bool(visible),
            "area": area,
            "box": {"left": left + x, "top": top + y, "right": left + x + width, "bottom": top + y + height},
            "center": {"x": round(float(left + center_x), 2), "y": round(float(top + center_y), 2)},
            "gauge": {"x": gauge_x + left, "y": gauge_y + top,
                      "diameter": round(radius * 2 / settings["scale"], 3)} if visible else None,
            "roundness": round(min(1.0, 4 * np.pi * area / perimeter**2), 3) if perimeter else None,
        }

    return Openings(gray, light, labels, accepted, by_id, unassigned, applied)


def summary(openings):
    usable = openings.accepted[openings.labels] | openings.unassigned
    return {
        "fraction": round(float(np.mean(usable)), 5),
        "raw_fraction": round(float(np.mean(openings.light)), 5),
        "pixels": int(np.count_nonzero(usable)),
        "count": int(np.count_nonzero(openings.accepted)),
        "listed": len(openings.by_id),
    }


def probe(image, settings, openings, x, y):
    box = settings["box"]
    result = {"x": x, "y": y, "rgb": image[y, x].tolist(), "inside": False, "status": "量区外", "hole": None}
    if not (box["left"] <= x < box["right"] and box["top"] <= y < box["bottom"]):
        return result
    ix, iy = x - box["left"], y - box["top"]
    key = int(openings.labels[iy, ix])
    state = "板面"
    if openings.unassigned[iy, ix]:
        state = "桥区（归属未定）"
    elif openings.light[iy, ix]:
        state = "开口" if openings.accepted[key] else "微小亮点"
    return {**result, "inside": True, "status": state, "gray": int(openings.gray[iy, ix]),
            "hole": openings.by_id.get(key)}
