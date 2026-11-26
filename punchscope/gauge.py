"""Fit a straight flat gauge (扁规) inside one listed opening.

The desk already reports the inscribed circle, which only witnesses the widest
local patch.  This module instead fits an oriented straight rectangle that stays
entirely on the selected connected component, and walks the two cross-wise
clearance rays until the original hole wall is met.  Every distance here is a
discrete pixel estimate, matching the rest of the desk.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from image_lab.validation import Invalid


def _mm(pixels, scale):
    return round(float(pixels) / scale, 3)


def _component(openings, hole_id):
    hole = openings.by_id.get(int(hole_id))
    if hole is None:
        raise Invalid("孔件不在本次孔件表中，请先更新测量或改选表内孔件")
    return hole


def _choose_hole(openings):
    """Recommend the most slot-like visible hole when no id is supplied."""
    if not openings.by_id:
        raise Invalid("孔件表为空，请先调整量区、阈值或最小面积")

    def aspect(hole):
        width = hole["box"]["right"] - hole["box"]["left"]
        height = hole["box"]["bottom"] - hole["box"]["top"]
        return max(width, height) / max(1, min(width, height))

    visible = [hole for hole in openings.by_id.values() if hole["visible"]]
    pool = visible or list(openings.by_id.values())
    return max(pool, key=lambda hole: (round(aspect(hole), 3), hole["area"], -hole["id"]))


def summary(openings, settings, hole):
    """Compact report entry; skip eager fitting for pathologically large holes."""
    if not hole.get("visible"):
        return {"fits": False, "reason": "clipped", "width_px": None, "depth_px": None,
                "width_mm": None, "depth_mm": None}
    width = hole["box"]["right"] - hole["box"]["left"]
    height = hole["box"]["bottom"] - hole["box"]["top"]
    if width * height > 300_000:
        return None
    fitted = fit(openings, settings, hole["id"])
    gauge = fitted.get("gauge")
    if not fitted.get("fits") or gauge is None:
        return {"fits": False, "reason": fitted.get("reason") or "no_room",
                "width_px": None, "depth_px": None,
                "width_mm": None, "depth_mm": None}
    return {"fits": True, "reason": None,
            "width_px": gauge["width_px"], "depth_px": gauge["depth_px"],
            "width_mm": gauge["width_mm"], "depth_mm": gauge["depth_mm"]}


def _crop_bounds(hole, box):
    return (hole["box"]["left"] - box["left"], hole["box"]["top"] - box["top"],
            hole["box"]["right"] - box["left"], hole["box"]["bottom"] - box["top"])


def _axis(mask):
    """Long direction of the outer shape, as a unit vector with positive X.

    Returns ``(ux, uy, elongated)``.  Compact (near-round) holes have an
    unstable minimum-area-rectangle angle, so callers treat them as horizontal.
    """
    ys, xs = np.nonzero(mask.astype(np.uint8))
    if len(xs) < 3:
        return 1.0, 0.0, False
    points = np.column_stack((xs.astype(np.float32), ys.astype(np.float32)))
    rect = cv2.minAreaRect(points)
    (rw, rh) = rect[1]
    aspect = max(rw, rh) / max(1.0, min(rw, rh))
    elongated = aspect >= 1.28
    corners = cv2.boxPoints(rect)
    edges = [corners[1] - corners[0], corners[2] - corners[1]]
    edge = max(edges, key=lambda vector: float(np.hypot(vector[0], vector[1])))
    length = float(np.hypot(edge[0], edge[1]))
    if length < 1e-6:
        return 1.0, 0.0, elongated
    ux, uy = edge[0] / length, edge[1] / length
    if ux < -1e-9 or (abs(ux) <= 1e-9 and uy < 0):
        ux, uy = -ux, -uy
    return float(ux), float(uy), bool(elongated)


def _histogram_records(grid):
    """Maximal all-ones rectangles keyed by their exact normal height.

    Returns ``height -> (axis_span, left, right_exclusive, bottom_row)`` using
    the standard monotonic-stack largest-rectangle scan.
    """
    rows, cols = grid.shape
    heights = np.zeros(cols, dtype=np.int32)
    records = {}
    for bottom in range(rows):
        heights = np.where(grid[bottom], heights + 1, 0)
        stack = []  # (start_column, height)
        for col in range(cols + 1):
            current = int(heights[col]) if col < cols else 0
            start = col
            while stack and stack[-1][1] > current:
                left, high = stack.pop()
                right = col
                axis_span = right - left
                candidate = (axis_span, left, right, bottom)
                old = records.get(high)
                if old is None or axis_span > old[0]:
                    records[high] = candidate
                start = left
            if current and (not stack or stack[-1][1] < current):
                stack.append((start, current))
    return records


class GridFit:
    """Width/depth frontier plus centred placement for a derotated bool grid."""

    def __init__(self, grid):
        self.grid = grid
        self.rows, self.cols = grid.shape
        ys, xs = np.nonzero(grid)
        self.cy = float(ys.mean()) if len(ys) else self.rows / 2.0
        self.cx = float(xs.mean()) if len(xs) else self.cols / 2.0
        self.records = _histogram_records(grid)
        self.max_span = max(self.records) if self.records else 0
        self._depth = {}
        best = 0
        for span in range(self.max_span, 0, -1):
            record = self.records.get(span)
            if record is not None and record[0] > best:
                best = record[0]
            self._depth[span] = best

    def spans(self):
        return list(range(1, self.max_span + 1))

    def depth(self, span):
        return int(self._depth.get(int(span), 0))

    def entry(self, span):
        span = int(span)
        depth = self.depth(span)
        best = None
        for high, record in self.records.items():
            if high < span or record[0] != depth:
                continue
            _, left, right, bottom = record
            top0 = bottom - high + 1
            top = top0 + (high - span) // 2
            normal_center = top + (span - 1) / 2.0
            axis_center = (left + right - 1) / 2.0
            drift = abs(normal_center - self.cy) * 0.35 + abs(axis_center - self.cx) * 0.05
            if best is None or drift < best[0]:
                best = (drift, int(top), int(left), int(right))
        _, top, left, right = best
        return {"width_px": span, "depth_px": depth, "top": top,
                "left": left, "right": right, "area_px2": span * depth}


def _axis_aligned_grid(mask, vertical):
    """Grid columns are the hole axis, rows the normal direction."""
    return GridFit(mask.T if vertical else mask)


def _axis_bounds(entry, vertical):
    n0, n1 = entry["top"], entry["top"] + entry["width_px"]
    a0, a1 = entry["left"], entry["right"]
    if vertical:
        # grid rows index original x (normal), grid columns original y (axis).
        return n0, a0, n1, a1  # left(x), top(y), right(x), bottom(y)
    return a0, n0, a1, n1


def _rotated_grid(labels, key, x0, y0, x1, y1, ux, uy):
    """Derotate a tilted hole onto a local grid."""
    mask = labels[y0:y1, x0:x1] == key
    ys, xs = np.nonzero(mask)
    ox = (x1 - x0) / 2.0
    oy = (y1 - y0) / 2.0
    sx = (xs - ox) * ux + (ys - oy) * uy
    sy = -(xs - ox) * uy + (ys - oy) * ux
    smin, smax = float(sx.min()), float(sx.max())
    tmin, tmax = float(sy.min()), float(sy.max())
    gw = int(math.ceil(smax - smin)) + 5
    gh = int(math.ceil(tmax - tmin)) + 5
    ic = -smin + 2.0
    jc = -tmin + 2.0
    jj, ii = np.indices((gh, gw))
    ds = ii - ic
    dt = jj - jc
    lx = np.rint(ox + ds * ux - dt * uy).astype(int)
    ly = np.rint(oy + ds * uy + dt * ux).astype(int)
    valid = (lx >= 0) & (lx < mask.shape[1]) & (ly >= 0) & (ly < mask.shape[0])
    grid = np.zeros((gh, gw), dtype=bool)
    grid[valid] = mask[ly[valid], lx[valid]]

    def to_corners(entry):
        s_left = entry["left"] - ic - 0.5
        s_right = entry["right"] - ic - 0.5
        t_top = entry["top"] - jc - 0.5
        t_bottom = entry["top"] + entry["width_px"] - jc - 0.5

        def point(s, t):
            return (x0 + ox + s * ux - t * uy, y0 + oy + s * uy + t * ux)

        return [point(s_left, t_top), point(s_right, t_top),
                point(s_right, t_bottom), point(s_left, t_bottom)]

    return GridFit(grid), to_corners


def _native_rectangle(entry, hole, box, ux, uy, corners=None):
    if corners is not None:
        native_corners = [{"x": round(float(x + box["left"]), 2),
                           "y": round(float(y + box["top"]), 2)} for x, y in corners]
        xs = [p["x"] for p in native_corners]
        ys = [p["y"] for p in native_corners]
        center_x = sum(xs) / 4
        center_y = sum(ys) / 4
        width_px = entry["width_px"]
        depth_px = entry["depth_px"]
        bounds = {"left": round(min(xs)), "top": round(min(ys)),
                  "right": round(max(xs)), "bottom": round(max(ys))}
    else:
        bx0, by0, bx1, by1 = entry["_bounds"]
        bounds = {"left": box["left"] + bx0, "top": box["top"] + by0,
                  "right": box["left"] + bx1, "bottom": box["top"] + by1}
        native_corners = [
            {"x": bounds["left"], "y": bounds["top"]},
            {"x": bounds["right"], "y": bounds["top"]},
            {"x": bounds["right"], "y": bounds["bottom"]},
            {"x": bounds["left"], "y": bounds["bottom"]},
        ]
        center_x = (bounds["left"] + bounds["right"]) / 2
        center_y = (bounds["top"] + bounds["bottom"]) / 2
        width_px = entry["width_px"]
        depth_px = entry["depth_px"]
    return {
        "center": {"x": round(center_x, 2), "y": round(center_y, 2)},
        "width_px": int(width_px),
        "depth_px": int(depth_px),
        "area_px2": int(entry["area_px2"]),
        "bounds": bounds,
        "corners": native_corners,
        "axis": {"ux": round(ux, 6), "uy": round(uy, 6)},
    }


def fit(openings, settings, hole_id=None, width_px=None):
    """Return the recommended gauge, width/depth frontier and alternatives."""
    box = settings["box"]
    scale = float(settings["scale"])
    hole = _choose_hole(openings) if hole_id is None else _component(openings, hole_id)
    hole_id = hole["id"]
    response = {"hole": hole["id"], "scale": scale, "fits": False, "eligible": True,
                "reason": None, "message": "", "orientation": None,
                "gauge": None, "recommended": None, "widest": None, "frontier": []}

    if not hole["visible"]:
        response.update(reason="clipped",
                        message="这个孔件被量区边缘裁断，不能作为完整扁规通过；请放宽量区后再选。")
        return response

    x0, y0, x1, y1 = _crop_bounds(hole, box)
    mask = openings.labels[y0:y1, x0:x1] == hole["id"]
    ux, uy, elongated = _axis(mask)
    vertical = abs(uy) > abs(ux)
    cardinal = abs(uy) <= 0.02 or abs(ux) <= 0.02
    if not elongated:
        # Round / squat holes give no stable long direction; use image X so the
        # section-chart click maps cleanly onto the gauge length.
        ux, uy = 1.0, 0.0
        vertical = False
        axis_aligned = True
    elif cardinal:
        # Snap the cardinal directions so raster bounds and rays stay exact.
        if abs(uy) <= abs(ux):
            ux, uy = 1.0, 0.0
            vertical = False
        else:
            ux, uy = 0.0, 1.0
            vertical = True
        axis_aligned = True
    else:
        axis_aligned = False
    angle = math.degrees(math.atan2(uy, ux))
    response["orientation"] = {"ux": round(ux, 6), "uy": round(uy, 6),
                               "angle_deg": round(angle, 2), "vertical": bool(vertical)}

    corner_builder = None
    if axis_aligned:
        grid_fit = _axis_aligned_grid(mask, vertical)

        def prepare(entry):
            bx0, by0, bx1, by1 = _axis_bounds(entry, vertical)
            # Convert hole-sub-mask bounds to crop bounds.
            entry["_bounds"] = (bx0 + x0, by0 + y0, bx1 + x0, by1 + y0)
            return entry
    else:
        grid_fit, corner_builder = _rotated_grid(
            openings.labels, hole["id"], x0, y0, x1, y1, ux, uy)

        def prepare(entry):
            return entry

    if grid_fit.max_span <= 0:
        response.update(reason="no_room", eligible=True,
                        message="孔件内找不到放得下的直矩形。")
        return response

    spans = grid_fit.spans()
    frontier = [{
        "width_px": span, "depth_px": grid_fit.depth(span),
        "width_mm": _mm(span, scale), "depth_mm": _mm(grid_fit.depth(span), scale),
        "area_px2": span * grid_fit.depth(span),
    } for span in spans]
    response["frontier"] = frontier

    widest_span = grid_fit.max_span
    recommended_span = max(spans, key=lambda s: (s * grid_fit.depth(s), s))

    def describe(span):
        entry = prepare(grid_fit.entry(span))
        corners = corner_builder(entry) if corner_builder else None
        gauge = _native_rectangle(entry, hole, box, ux, uy, corners=corners)
        gauge["width_mm"] = _mm(gauge["width_px"], scale)
        gauge["depth_mm"] = _mm(gauge["depth_px"], scale)
        gauge["length_px"] = gauge["depth_px"]
        gauge["length_mm"] = gauge["depth_mm"]
        # Convenience aliases: bare width/depth/length values are millimetres,
        # matching the desk's other reported measurements.
        gauge["width"] = gauge["width_mm"]
        gauge["depth"] = gauge["depth_mm"]
        gauge["length"] = gauge["depth_mm"]
        gauge["valid"] = True
        gauge["pass"] = True
        gauge["hole"] = hole["id"]
        return gauge

    response["widest"] = describe(widest_span)
    longest_span = max(spans, key=lambda s: (grid_fit.depth(s), s))
    response["longest"] = describe(longest_span)

    if width_px is not None:
        wanted = max(1, int(width_px))
        chosen_span = min(spans, key=lambda s: abs(s - wanted))
    else:
        chosen_span = recommended_span

    gauge = describe(chosen_span)
    response["gauge"] = gauge
    response["recommended"] = gauge
    response["flat_gauge"] = gauge
    response["rectangle"] = gauge
    response["fits"] = True
    response["message"] = (
        f"扁规宽 {gauge['width_px']} px / {gauge['width_mm']} mm，"
        f"进深 {gauge['depth_px']} px / {gauge['depth_mm']} mm；"
        "矩形内全部为本孔透光证据。")
    return response


def _ray(labels, key, box, start_x, start_y, dx, dy):
    """Walk unit steps; count consecutive this-hole cells, stop at first wall."""
    height, width = labels.shape
    count = 0
    x, y = start_x, start_y
    last_open = None
    while True:
        x += dx
        y += dy
        ix, iy = x - box["left"], y - box["top"]
        if not (0 <= ix < width and 0 <= iy < height):
            return count, last_open, {"x": x, "y": y}, "box"
        if int(labels[iy, ix]) != int(key):
            return count, last_open, {"x": x, "y": y}, "wall"
        count += 1
        last_open = {"x": x, "y": y}


def validate_rectangle(openings, settings, hole_id, rectangle):
    """Check a proposed native bounds rectangle is entirely this hole's evidence."""
    box = settings["box"]
    hole = _component(openings, hole_id)
    result = {"hole": hole["id"], "valid": False, "pass": False,
              "eligible": bool(hole["visible"]), "reason": None,
              "message": "", "bad_point": None, "checked_px": 0}
    if not hole["visible"]:
        result.update(reason="clipped",
                      message="这个孔件被量区边缘裁断，矩形不能作为完整扁规通过。")
        return result

    try:
        left = int(round(float(rectangle["left"])))
        top = int(round(float(rectangle["top"])))
        right = int(round(float(rectangle["right"])))
        bottom = int(round(float(rectangle["bottom"])))
    except (KeyError, TypeError, ValueError):
        raise Invalid("矩形需要 left/top/right/bottom 原片坐标")
    if right <= left or bottom <= top:
        raise Invalid("矩形至少要有一个像素")

    crop_h, crop_w = openings.labels.shape
    x0 = max(0, left - box["left"])
    y0 = max(0, top - box["top"])
    x1 = min(crop_w, right - box["left"])
    y1 = min(crop_h, bottom - box["top"])
    if x1 <= x0 or y1 <= y0 or x1 != right - box["left"] or y1 != bottom - box["top"]:
        result.update(reason="outside_box",
                      message="矩形超出量区，缺少透光证据，不能通过。")
        return result

    patch = openings.labels[y0:y1, x0:x1]
    result["checked_px"] = int(patch.size)
    bad = np.argwhere(patch != hole["id"])
    if len(bad):
        by, bx = bad[0]
        bad_x = int(bx + x0 + box["left"])
        bad_y = int(by + y0 + box["top"])
        label = int(patch[by, bx])
        kind = "缺口或挡料" if label == 0 else "碰到其他孔件或亮点"
        result.update(reason="blocked", bad_point={"x": bad_x, "y": bad_y},
                      message=f"矩形内在 ({bad_x}, {bad_y}) 遇到{kind}，不能整块通过。")
        return result

    result["valid"] = True
    result["pass"] = True
    result["message"] = "矩形内整块都是本孔透光证据，通过。"
    return result


def clearance(openings, settings, hole_id, click_x, click_y=None, width_px=None):
    """Two cross-wise gaps located by a clicked native X on the section chart."""
    fitted = fit(openings, settings, hole_id, width_px=width_px)
    if not fitted["fits"]:
        return {"hole": int(hole_id), "x": int(click_x), "y": click_y,
                "inside": False, "station_px": None, "point": None,
                "gauge": fitted.get("gauge"), "sides": [], "gaps_px": [],
                "gaps_mm": [], "message": fitted["message"]}
    gauge = fitted["gauge"]
    ux, uy = gauge["axis"]["ux"], gauge["axis"]["uy"]
    cx, cy = gauge["center"]["x"], gauge["center"]["y"]
    if click_y is None:
        click_y = settings["scan_y"]
    station = (click_x - cx) * ux + (click_y - cy) * uy
    return _station_clearance(openings, settings, hole_id, fitted, station,
                              click_x=click_x, click_y=click_y)


def clearance_at_station(openings, settings, hole_id, station, click_y=None, width_px=None):
    """Two cross-wise gaps at an explicit along-gauge station (px from centre)."""
    fitted = fit(openings, settings, hole_id, width_px=width_px)
    if not fitted["fits"]:
        return {"hole": int(hole_id), "x": None, "y": click_y,
                "inside": False, "station_px": round(float(station), 2), "point": None,
                "gauge": fitted.get("gauge"), "sides": [], "gaps_px": [],
                "gaps_mm": [], "message": fitted["message"]}
    return _station_clearance(openings, settings, hole_id, fitted, float(station),
                              click_x=None, click_y=click_y)


def _station_clearance(openings, settings, hole_id, fitted, station,
                       click_x=None, click_y=None):
    box = settings["box"]
    scale = float(settings["scale"])
    result = {"hole": int(hole_id), "x": click_x, "y": click_y,
              "inside": False, "station_px": round(station, 2), "point": None,
              "gauge": fitted.get("gauge"), "sides": [], "gaps_px": [],
              "gaps_mm": [], "message": ""}
    gauge = fitted["gauge"]
    ux, uy = gauge["axis"]["ux"], gauge["axis"]["uy"]
    # Normal chosen so that minus->plus reads top->bottom for horizontal holes.
    nx, ny = -uy, ux
    if abs(uy) < 0.02:
        nx, ny = 0.0, 1.0
    cx, cy = gauge["center"]["x"], gauge["center"]["y"]

    half_depth = gauge["depth_px"] / 2.0
    if abs(station) > half_depth + 0.5:
        result["message"] = "请在扁规长度范围内点选截线位置，这里已经超出规端。"
        return result

    qx = cx + station * ux
    qy = cy + station * uy
    result["point"] = {"x": round(qx, 2), "y": round(qy, 2)}
    result["inside"] = True

    horizontal = abs(uy) < 0.02
    vertical = abs(ux) < 0.02
    if horizontal:
        column = int(round(qx))
        top = gauge["bounds"]["top"]
        bottom = gauge["bounds"]["bottom"]
        specs = [("minus", "上侧", 0, -1, column, top),
                 ("plus", "下侧", 0, 1, column, bottom - 1)]
        # Ray starts from the outermost gauge cell; step once before testing.
        sides = []
        for side, label, dx, dy, sx, sy in specs:
            count, last_open, wall, stop = _ray(
                openings.labels, hole_id, box, sx, sy, dx, dy)
            edge = {"x": sx, "y": top if side == "minus" else bottom}
            sides.append(_side(side, label, count, scale, edge, wall, stop, last_open))
    elif vertical:
        row = int(round(qy))
        left = gauge["bounds"]["left"]
        right = gauge["bounds"]["right"]
        specs = [("minus", "左侧", -1, 0, left, row),
                 ("plus", "右侧", 1, 0, right - 1, row)]
        sides = []
        for side, label, dx, dy, sx, sy in specs:
            count, last_open, wall, stop = _ray(
                openings.labels, hole_id, box, sx, sy, dx, dy)
            edge = {"x": left if side == "minus" else right, "y": sy}
            sides.append(_side(side, label, count, scale, edge, wall, stop, last_open))
    else:
        sides = _rotated_sides(openings, box, scale, hole_id, gauge, qx, qy, nx, ny)

    result["sides"] = sides
    result["gaps_px"] = [s["gap_px"] for s in sides]
    result["gaps_mm"] = [s["gap_mm"] for s in sides]
    total = gauge["width_px"] + sum(result["gaps_px"])
    result["opening_px"] = total
    result["opening_mm"] = _mm(total, scale)
    result["message"] = (
        f"{sides[0]['label']}间隙 {sides[0]['gap_mm']} mm"
        f"（{sides[0]['gap_px']} px，离原孔壁），"
        f"{sides[1]['label']}间隙 {sides[1]['gap_mm']} mm"
        f"（{sides[1]['gap_px']} px，离原孔壁）；"
        "遇到原孔壁即停，隔着板料的亮点未计入间隙。")
    return result


def _side(side, label, count, scale, edge, wall, stop, last_open):
    return {
        "side": side, "label": label,
        "gap_px": int(count), "gap_mm": _mm(count, scale),
        "edge": edge, "wall": wall if stop == "wall" else None,
        "limit": wall if stop != "wall" else None,
        "stop": stop, "last_open": last_open,
    }


def _rotated_sides(openings, box, scale, hole_id, gauge, qx, qy, nx, ny):
    """Sample both normal rays at half-pixel steps for tilted gauges."""
    labels = openings.labels
    height, width = labels.shape
    sides = []
    half_width = gauge["width_px"] / 2.0
    for sign, side, label in ((-1, "minus", "法向一侧"), (1, "plus", "法向另一侧")):
        ex = qx + sign * nx * half_width
        ey = qy + sign * ny * half_width
        distance = 0.0
        wall = None
        stop = "box"
        step = 0.5
        checked = 0.0
        while checked < max(width, height):
            checked += step
            px = int(round(ex + sign * nx * checked))
            py = int(round(ey + sign * ny * checked))
            ix, iy = px - box["left"], py - box["top"]
            if not (0 <= ix < width and 0 <= iy < height):
                stop = "box"
                wall = {"x": px, "y": py}
                break
            if int(labels[iy, ix]) != int(hole_id):
                stop = "wall"
                wall = {"x": px, "y": py}
                # Distance from gauge edge to the wall cell's near centre line.
                distance = max(0.0, checked - step / 2)
                break
            distance = checked
        sides.append({
            "side": side, "label": label,
            "gap_px": int(round(distance)), "gap_mm": _mm(distance, scale),
            "edge": {"x": round(ex, 2), "y": round(ey, 2)},
            "wall": wall if stop == "wall" else None,
            "limit": wall if stop != "wall" else None,
            "stop": stop, "last_open": None,
        })
    return sides
