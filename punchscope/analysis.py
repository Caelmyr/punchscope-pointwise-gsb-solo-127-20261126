"""Preview, source measurements, and the shared opening map."""

import numpy as np
from PIL import Image

from . import apertures, edges, section


def report(image, settings, openings):
    return {
        "area": apertures.summary(openings),
        "holes": list(openings.by_id.values()),
        "section": section.scan(openings, settings),
        "edges": edges.measure(image, settings),
        "splits": openings.applied,
    }


def overlay(image, settings, openings):
    box = settings["box"]
    view = image[box["top"]:box["bottom"], box["left"]:box["right"]]
    marked = np.zeros((*view.shape[:2], 4), dtype=np.uint8)
    accepted = openings.accepted[openings.labels]
    marked[openings.light & ~accepted & ~openings.unassigned] = (246, 179, 65, 145)
    marked[accepted] = (42, 208, 188, 113)
    marked[openings.unassigned] = (197, 148, 250, 160)
    result = Image.new("RGBA", (image.shape[1], image.shape[0]))
    result.paste(Image.fromarray(marked), (box["left"], box["top"]))
    return result
