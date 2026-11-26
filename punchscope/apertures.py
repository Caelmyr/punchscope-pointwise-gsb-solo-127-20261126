"""Identify witnessed openings and describe native contours."""

from dataclasses import dataclass

import cv2
import numpy as np

from image_lab.arrays import luminance


@dataclass
class Openings:
    gray: np.ndarray
    light: np.ndarray
    labels: np.ndarray
    accepted: np.ndarray
    by_id: dict


def source_gray(image):
    return np.rint(luminance(image.astype(np.float32) / 255.0) * 255.0).astype(np.uint8)


def component_outline(labels, key, x, y, width, height):
    cut = (labels[y:y + height, x:x + width] == key).astype(np.uint8)
    contours, _ = cv2.findContours(cut, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    perimeter = sum(cv2.arcLength(contour, True) for contour in contours)
    distances = cv2.distanceTransform(np.pad(cut, 1), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    _, radius, _, (column, row) = cv2.minMaxLoc(distances)
    return perimeter, (column + x - 1, row + y - 1, radius)


def detect(image, settings):
    left, top, right, bottom = (settings["box"][edge] for edge in ("left", "top", "right", "bottom"))
    gray = source_gray(image[top:bottom, left:right])
    light = gray >= settings["threshold"]
    _, labels, stats, centers = cv2.connectedComponentsWithStats(light.astype(np.uint8), connectivity=8)
    accepted = stats[:, cv2.CC_STAT_AREA] >= settings["min_area"]
    accepted[0] = False
    selected = [int(key) for key in np.flatnonzero(accepted)]
    largest = sorted(selected, key=lambda key: (-stats[key, cv2.CC_STAT_AREA], key))[:500]

    by_id = {}
    for key in sorted(largest):
        x, y, width, height, area = map(int, stats[key])
        center_x, center_y = centers[key]
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

    return Openings(gray, light, labels, accepted, by_id)


def summary(openings):
    usable = openings.accepted[openings.labels]
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
    if openings.light[iy, ix]:
        state = "开口" if openings.accepted[key] else "微小亮点"
    return {**result, "inside": True, "status": state, "gray": int(openings.gray[iy, ix]),
            "hole": openings.by_id.get(key)}
