"""Recommend one straight top-trim line over witnessed native plate pixels."""

import numpy as np


def runs(flags):
    changes = np.diff(np.pad(flags.astype(np.int8), 1))
    return np.flatnonzero(changes == 1), np.flatnonzero(changes == -1) - 1


def describe(settings, top, row, start, stop):
    """Turn zero-based box columns back into native coordinates and readings."""
    box, scale = settings["box"], settings["scale"]
    segment = top[start:stop + 1]
    deepest = int(np.argmin(segment))
    columns = int(stop - start + 1)
    cut = int(row - segment[deepest])
    return {"row": int(row), "x0": int(start + box["left"]), "x1": int(stop + box["left"]),
            "columns": columns, "width_mm": round(columns / scale, 2),
            "cut": cut, "cut_mm": round(cut / scale, 3), "cut_x": int(start + deepest + box["left"])}


def recommend(settings, openings, top, bottom):
    """Pick the straight line that keeps the most witnessed plate pixels.

    A column joins the line only when both sheet edges are witnessed and the
    line falls on plate pixels: at or below the top edge, at or above the
    bottom edge, and clear of every lit opening.  A candidate is one unbroken
    run, so a missing edge or an opening mouth simply ends it rather than
    bridging the gap.  Runs are ranked by the plate pixels the sample keeps
    below the line inside the current box; a shallower deepest cut breaks ties.
    """
    box = settings["box"]
    witnessed = (top >= 0) & (bottom >= 0) & (bottom >= top)
    kept_bottom = np.minimum(bottom + 1, box["bottom"])
    prefix = np.concatenate(([0], np.cumsum(kept_bottom)))
    best = None
    for row in range(box["top"], box["bottom"]):
        on_plate = witnessed & (top <= row) & (row <= bottom) & ~openings.light[row - box["top"]]
        for start, stop in zip(*runs(on_plate)):
            start, stop = int(start), int(stop)
            kept = int(prefix[stop + 1] - prefix[start]) - row * (stop - start + 1)
            line = describe(settings, top, row, start, stop)
            rank = (kept, -line["cut"], line["columns"], -line["x0"])
            if best is None or rank > best[0]:
                best = (rank, line)
    return None if best is None else best[1]


def holds(settings, openings, top, bottom, line):
    """Whether an adopted line still lies on witnessed plate along every column."""
    box = settings["box"]
    x0, x1, row = line["x0"], line["x1"], line["row"]
    inside = box["left"] <= x0 <= x1 < box["right"] and box["top"] <= row < box["bottom"]
    if not inside:
        return False
    columns = slice(x0 - box["left"], x1 - box["left"] + 1)
    witnessed = (top[columns] >= 0) & (bottom[columns] >= top[columns])
    on_plate = (top[columns] <= row) & (row <= bottom[columns])
    clear = ~openings.light[row - box["top"], columns]
    return bool(np.all(witnessed & on_plate & clear))


def report(settings, openings, top, bottom):
    """Recommend a line and keep track of what an adopted one still measures."""
    adopted, stale = None, False
    requested = settings.get("trim")
    if requested:
        if holds(settings, openings, top, bottom, requested):
            start = requested["x0"] - settings["box"]["left"]
            stop = requested["x1"] - settings["box"]["left"]
            adopted = describe(settings, top, requested["row"], start, stop)
        else:
            stale = True
    return {"proposal": recommend(settings, openings, top, bottom), "adopted": adopted, "stale": stale}
