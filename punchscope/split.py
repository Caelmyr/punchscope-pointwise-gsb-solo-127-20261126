"""Evidence-based split advice for openings joined by a bright bridge.

A bright bridge between two punched holes merges them into one connected
component.  This module proposes a split only when geometry is backed by
gray-level or shape evidence, so genuinely one-piece narrow-waist openings
stay intact.  Bridge pixels whose ownership is unclear are marked
*unassigned*: they still count toward the open fraction of the measurement
box, but never toward any single hole's width or area.
"""

from itertools import combinations

import cv2
import numpy as np
from scipy import ndimage as ndi
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from image_lab.validation import Invalid

# Evidence scale.  Narrow alone must not convict: SPLIT_SCORE is unreachable
# from the neck ratio by itself.
NECK_STRONG = 0.35   # saddle depth vs. the smaller core radius: extremely thin waist
NECK_CLEAR = 0.6
NECK_WEAK = 0.8
DIM_STRONG = 0.85    # bridge gray vs. interior gray: clearly darker bridge
DIM_HINT = 0.95
ROUND_OK = 0.55      # both parts reasonably round once separated
SPLIT_SCORE = 4
UNSURE_SCORE = 2

SQRT2 = 1.4142135623730951

# Split analysis stays local and cheap: huge merged blobs (glare, reflections)
# are refused instead of flooding the desk with a graph of millions of pixels.
MAX_SPLIT_PIXELS = 100_000
MAX_PEAK_WINDOW = 31


def _line_saddle(dist, p, q):
    steps = int(max(abs(p[0] - q[0]), abs(p[1] - q[1]), 1)) + 1
    ys = np.linspace(p[0], q[0], steps).round().astype(int)
    xs = np.linspace(p[1], q[1], steps).round().astype(int)
    return float(dist[ys, xs].min())


def _peaks(dist, mask):
    """Significant distance-transform maxima: candidate hole cores.  A
    candidate that barely rises above the saddle toward a deeper core is a
    shelf on a one-piece waist, not a core of its own."""
    top = float(dist.max())
    if top < 2:
        return []
    neighborhood = min(MAX_PEAK_WINDOW, max(3, int(round(top * 0.6))))
    local = dist == ndi.maximum_filter(dist, size=neighborhood, mode="constant")
    local &= mask & (dist >= max(2.0, top * 0.4))
    marks, count = ndi.label(local)
    candidates = []
    for index in range(1, count + 1):
        ys, xs = np.nonzero(marks == index)
        best = int(np.argmax(dist[ys, xs]))
        candidates.append((float(dist[ys[best], xs[best]]), int(ys[best]), int(xs[best])))
    candidates.sort(reverse=True)
    accepted = []
    for depth, y, x in candidates:
        if all(_line_saddle(dist, (y, x), (ay, ax)) < depth * 0.7 for _, ay, ax in accepted):
            accepted.append((depth, y, x))
    return [(y, x) for _, y, x in accepted]


def _geodesic_labels(mask, seeds):
    """Assign every pixel to the nearest seed along the sheet, so a noisy
    threshold edge cannot bend the meeting line."""
    height, width = mask.shape
    index = np.full(mask.shape, -1, np.int64)
    index[mask] = np.arange(int(mask.sum()))
    ys, xs = np.nonzero(mask)
    rows, cols, data = [], [], []
    for dy, dx, weight in ((1, 0, 1.0), (0, 1, 1.0), (1, 1, SQRT2), (1, -1, SQRT2)):
        ny, nx = ys + dy, xs + dx
        valid = (0 <= ny) & (ny < height) & (0 <= nx) & (nx < width)
        src = index[ys[valid], xs[valid]]
        dst = index[ny[valid], nx[valid]]
        both = (src >= 0) & (dst >= 0)
        rows.append(np.concatenate([src[both], dst[both]]))
        cols.append(np.concatenate([dst[both], src[both]]))
        data.append(np.full(int(both.sum()) * 2, weight))
    count = int(index.max()) + 1
    graph = csr_matrix((np.concatenate(data), (np.concatenate(rows), np.concatenate(cols))),
                       shape=(count, count))
    dists = dijkstra(graph, indices=[index[y, x] for y, x in seeds])
    labels = np.zeros(mask.shape, np.int32)
    labels[mask] = np.argmin(dists, axis=0) + 1
    return labels


def _boundary_from(labels):
    """Pixels that touch a differently owned neighbor: the meeting line."""
    boundary = np.zeros(labels.shape, bool)
    meet = (labels[:-1] != labels[1:]) & (labels[:-1] > 0) & (labels[1:] > 0)
    boundary[:-1] |= meet
    boundary[1:] |= meet
    meet = (labels[:, :-1] != labels[:, 1:]) & (labels[:, :-1] > 0) & (labels[:, 1:] > 0)
    boundary[:, :-1] |= meet
    boundary[:, 1:] |= meet
    return boundary


def _unsure_band(mask, dist, boundary):
    """Waist pixels no hole can claim.  The safe cores (deeper than the
    saddle) grow back over their own skirts by the same depth, so what
    remains shallow and unclaimed is the bridge itself."""
    if not boundary.any():
        return np.zeros(mask.shape, bool)
    saddle = float(dist[boundary].max())
    depth = saddle * 1.2 + 0.5
    protect = mask & (dist > depth)
    for _ in range(int(np.ceil(depth)) + 1):
        protect = ndi.binary_dilation(protect, structure=np.ones((3, 3))) & mask
    return mask & (dist <= depth) & ~protect


def _roundness(region):
    area = int(region.sum())
    if area < 3:
        return 0.0
    contours, _ = cv2.findContours(np.pad(region.astype(np.uint8), 1),
                                   cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    perimeter = sum(cv2.arcLength(contour, True) for contour in contours)
    if perimeter <= 0:
        return 0.0
    return round(min(1.0, 4 * np.pi * area / perimeter ** 2), 3)


def _boundary_sides(labels, boundary):
    """Region ids touching each boundary pixel."""
    sides = []
    ys, xs = np.nonzero(boundary)
    for y, x in zip(ys, xs):
        ids = set()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < labels.shape[0] and 0 <= nx < labels.shape[1]:
                value = int(labels[ny, nx])
                if value > 0:
                    ids.add(value)
        sides.append(((int(y), int(x)), ids))
    return sides


def _evidence(gray, dist, mask, labels, boundary, band, seeds):
    """Score every meeting line between two candidate cores."""
    sides = _boundary_sides(labels, boundary)
    pairs = sorted({pair for _, ids in sides for pair in combinations(sorted(ids), 2)})
    items = []
    worst = None
    for first, second in pairs:
        segment = [point for point, ids in sides if first in ids and second in ids]
        seg_ys, seg_xs = zip(*segment)
        saddle = float(dist[seg_ys, seg_xs].max())
        peak = min(float(dist[y, x]) for y, x in (seeds[first - 1], seeds[second - 1]))
        neck = saddle / peak if peak > 0 else 1.0
        inner = mask & (dist >= 0.6 * peak) & ((labels == first) | (labels == second))
        bridge = band & mask
        inner_gray = float(np.median(gray[inner])) if inner.any() else 0.0
        bridge_gray = float(np.median(gray[bridge])) if bridge.any() else inner_gray
        dim = bridge_gray / inner_gray if inner_gray > 0 else 1.0
        roundness = (_roundness((labels == first) & ~band),
                     _roundness((labels == second) & ~band))

        score = 0
        if neck <= NECK_STRONG:
            score += 3
        elif neck <= NECK_CLEAR:
            score += 2
        elif neck <= NECK_WEAK:
            score += 1
        if dim <= DIM_STRONG:
            score += 2
        elif dim <= DIM_HINT:
            score += 1
        if min(roundness) >= ROUND_OK:
            score += 1
        items.append({
            "between": [first, second],
            "score": score,
            "lines": [
                {"label": "腰部深度", "supports": neck <= NECK_CLEAR,
                 "detail": f"鞍部 {saddle:.1f} px，为较小核心半径 {peak:.1f} px 的 {neck:.0%}"},
                {"label": "桥区亮度", "supports": dim <= DIM_HINT,
                 "detail": f"桥区灰度中位 {bridge_gray:.0f}，为孔内 {inner_gray:.0f} 的 {dim:.0%}"},
                {"label": "分开后形状", "supports": min(roundness) >= ROUND_OK,
                 "detail": f"两侧圆度 {roundness[0]:.2f} / {roundness[1]:.2f}"},
            ],
        })
        worst = score if worst is None else min(worst, score)
    return items, (worst or 0)


def _parts(labels, band, seeds, origin, box):
    """Preview of the separated pieces in native coordinates."""
    off_y, off_x = origin
    parts = []
    for index, (sy, sx) in enumerate(seeds, 1):
        region = (labels == index) & ~band
        area = int(region.sum())
        if area < 3:
            continue
        ys, xs = np.nonzero(region)
        parts.append({
            "area": area,
            "center": {"x": round(float(xs.mean()) + off_x + box["left"], 2),
                       "y": round(float(ys.mean()) + off_y + box["top"], 2)},
            "roundness": _roundness(region),
            "seed": {"x": sx + off_x + box["left"], "y": sy + off_y + box["top"]},
        })
    return parts


def _pixels(points, origin, box):
    off_y, off_x = origin
    return [[int(x + off_x + box["left"]), int(y + off_y + box["top"])] for y, x in points]


def _geometry(mask):
    """Seeds, ownership, meeting line, and the unowned waist of one mask."""
    dist = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    seeds = _peaks(dist, mask)
    if len(seeds) < 2:
        return dist, seeds, None, None, None
    labels = _geodesic_labels(mask, seeds)
    boundary = _boundary_from(labels) & mask
    band = _unsure_band(mask, dist, boundary)
    return dist, seeds, labels, boundary, band


def advise(openings, settings, hole_id):
    """Describe a possible split of one listed opening, with the evidence."""
    box = settings["box"]
    if hole_id not in openings.by_id:
        raise Invalid("孔件不在当前测量表内，请刷新测量")
    info = openings.by_id[hole_id]
    bounds = info["box"]
    y0, y1 = bounds["top"] - box["top"], bounds["bottom"] - box["top"]
    x0, x1 = bounds["left"] - box["left"], bounds["right"] - box["left"]
    origin = (y0, x0)
    mask = openings.labels[y0:y1, x0:x1] == hole_id
    gray = openings.gray[y0:y1, x0:x1]
    if int(mask.sum()) > MAX_SPLIT_PIXELS:
        raise Invalid("该孔件连通范围过大，请先调整阈值或量区再分孔")
    result = {"hole": hole_id, "verdict": "keep", "seeds": [], "boundary": [],
              "unsure": [], "parts": [], "evidence": [],
              "message": "只见到一个成形核心，没有分孔依据，建议保留一体。"}
    dist, seeds, labels, boundary, band = _geometry(mask)
    if labels is None:
        return result

    evidence, score = _evidence(gray, dist, mask, labels, boundary, band, seeds)
    if score >= SPLIT_SCORE:
        verdict = "split"
        message = f"见到 {len(seeds)} 个各自成形的核心，且腰部证据支持，建议分成 {len(seeds)} 孔。"
    elif score >= UNSURE_SCORE:
        verdict = "uncertain"
        message = "有细腰但佐证不足，可能是真一体细腰孔；请对照原片上的标记再决定。"
    else:
        verdict = "keep"
        message = "细腰处亮度与孔内一致、分开后形状不圆，更像一体细腰孔，建议保留。"
    result.update({
        "verdict": verdict,
        "message": message,
        "seeds": [{"x": x + origin[1] + box["left"], "y": y + origin[0] + box["top"],
                   "depth": round(float(dist[y, x]), 2)} for y, x in seeds],
        "boundary": _pixels(zip(*np.nonzero(boundary)), origin, box),
        "unsure": _pixels(zip(*np.nonzero(band)), origin, box),
        "parts": _parts(labels, band, seeds, origin, box),
        "evidence": evidence,
    })
    return result


def apply(gray, labels, accepted, splits, box):
    """Apply adopted splits to a fresh detection.

    Returns (labels, accepted, unassigned, applied).  Light pixels are never
    removed: bridge pixels become label 0 plus a mark in ``unassigned`` so the
    open fraction stays exactly as measured, while no hole claims them.
    """
    unassigned = np.zeros(gray.shape, bool)
    applied = []
    if not splits:
        return labels, accepted, unassigned, applied
    labels = labels.copy()
    accepted = accepted.copy()
    height, width = gray.shape
    next_key = int(labels.max()) + 1
    grown = []
    for position, split in enumerate(splits):
        seeds = [(int(y) - box["top"], int(x) - box["left"]) for x, y in split.get("seeds", [])]
        if len(seeds) < 2:
            continue
        keys = set()
        for y, x in seeds:
            if not (0 <= y < height and 0 <= x < width):
                keys = set()
                break
            keys.add(int(labels[y, x]))
        if len(keys) != 1:
            continue  # seeds no longer share one component: the split is moot
        key = keys.pop()
        if key <= 0 or not accepted[key]:
            continue
        ys, xs = np.nonzero(labels == key)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        mask = labels[y0:y1, x0:x1] == key
        if int(mask.sum()) > MAX_SPLIT_PIXELS:
            continue
        local = [(y - y0, x - x0) for y, x in seeds]
        dist = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        # Run the ownership contest with the stored seeds, so the split
        # lands exactly where the inspector adopted it.
        ownership = _geodesic_labels(mask, local)
        boundary = _boundary_from(ownership) & mask
        band = _unsure_band(mask, dist, boundary)
        regions = [(ownership == index) & ~band for index in range(1, len(local) + 1)]
        if any(int(region.sum()) < 3 for region in regions):
            continue
        accepted[key] = False
        window = labels[y0:y1, x0:x1]
        for region in regions:
            window[region] = next_key
            grown.append(next_key)
            next_key += 1
        window[band] = 0
        unassigned[y0:y1, x0:x1][band] = True
        applied.append({
            "index": position,
            "seeds": [[int(x), int(y)] for x, y in split["seeds"]],
            "boundary": _pixels(zip(*np.nonzero(boundary)), (y0, x0), box),
            "unsure": _pixels(zip(*np.nonzero(band)), (y0, x0), box),
        })
    if grown:
        accepted = np.concatenate([accepted, np.zeros(max(grown) + 1 - len(accepted), bool)])
        accepted[grown] = True
    return labels, accepted, unassigned, applied
