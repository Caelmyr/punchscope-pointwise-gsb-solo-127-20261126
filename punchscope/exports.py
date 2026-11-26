"""Produce an original-pixel crop and portable evidence for the saved inspection."""

import csv
import json
from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
from PIL import Image, ImageDraw

from . import analysis, photo


def csv_bytes(report):
    stream = StringIO()
    writer = csv.writer(stream)
    writer.writerow(("x", "row", "gray", "opening"))
    row = report["section"]["row"]
    writer.writerows((point["x"], row, point["gray"], int(point["open"]))
                     for point in report["section"]["points"])
    return stream.getvalue().encode("utf-8")


def report_image(image, settings, openings, trim=None):
    source = Image.fromarray(image).convert("RGBA")
    source.alpha_composite(analysis.overlay(image, settings, openings))
    draw = ImageDraw.Draw(source)
    box = settings["box"]
    draw.rectangle((box["left"], box["top"], box["right"] - 1, box["bottom"] - 1),
                   outline=(22, 191, 182), width=2)
    row = settings["scan_y"]
    draw.line((box["left"], row, box["right"] - 1, row), fill=(253, 192, 83), width=1)
    if trim is None:
        trim = analysis.report(image, settings, openings)["trim"]
    for key, color, width in (("proposal", (255, 249, 223), 1), ("adopted", (214, 238, 131), 2)):
        line = trim[key]
        if line:
            draw.line((line["x0"], line["row"], line["x1"], line["row"]), fill=color, width=width)
    return photo.png(source.convert("RGB"))


def trim_alpha(line, settings, height):
    """Alpha for the box view: keep the adopted strip below the line and between its ends."""
    box = settings["box"]
    rows, columns = np.indices((box["bottom"] - box["top"], box["right"] - box["left"]))
    x, y = columns + box["left"], rows + box["top"]
    on_row = 0 <= line["row"] < height and box["top"] <= line["row"] < box["bottom"]
    good_columns = (x >= line["x0"]) & (x <= line["x1"])
    return np.where(on_row & good_columns & (y >= line["row"]), 255, 0).astype(np.uint8)


def crop(image, settings, line=None):
    """Box-size native crop; with an adopted line, only its covered strip stays opaque."""
    box = settings["box"]
    view = Image.fromarray(image[box["top"]:box["bottom"], box["left"]:box["right"]]).convert("RGBA")
    if line:
        view.putalpha(Image.fromarray(trim_alpha(line, settings, image.shape[0])))
    return photo.png(view)


def receipt(card, image, openings):
    report = analysis.report(image, card["settings"], openings)
    target = BytesIO()
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("card.json", json.dumps(card, ensure_ascii=False, indent=2))
        archive.writestr("summary.json", json.dumps(report, ensure_ascii=False, indent=2))
        archive.writestr("cross-section.csv", csv_bytes(report))
        archive.writestr("proof.png", report_image(image, card["settings"], openings, report["trim"]))
        archive.writestr("crop.png", crop(image, card["settings"], report["trim"]["adopted"]))
    return target.getvalue()
