"""Collect reported values from one native horizontal cross-section."""

import numpy as np


def solid_intervals(values):
    transitions = np.diff(np.pad(np.asarray(values, dtype=np.int8), 1))
    return zip(np.flatnonzero(transitions == 1), np.flatnonzero(transitions == -1))


def scan(openings, settings):
    box = settings["box"]
    row = settings["scan_y"]
    scale = settings["scale"]
    if not box["top"] <= row < box["bottom"]:
        return {"row": row, "points": [], "runs": [], "gaps": [], "holes": []}

    offset = box["left"]
    gray = openings.gray[row - box["top"]]
    labels = openings.labels[row - box["top"]]
    usable = openings.accepted[labels]
    runs = [{"from": int(start + offset), "to": int(end + offset),
             "width": round((end - start) / scale, 3), "center": float((start + end - 1) / 2 + offset)}
            for start, end in solid_intervals(usable)]
    holes = sorted(
        (openings.by_id[key] for key in np.unique(labels) if key in openings.by_id),
        key=lambda item: item["center"]["x"],
    )
    gaps = [{"from": before["center"]["x"], "to": after["center"]["x"],
             "pitch": round((after["center"]["x"] - before["center"]["x"]) / scale, 3)}
            for before, after in zip(holes, holes[1:])]

    return {"row": row, "points": [{"x": x + offset, "gray": int(value), "open": bool(usable[x])}
                                    for x, value in enumerate(gray)],
            "runs": runs, "gaps": gaps, "holes": [hole["id"] for hole in holes]}
