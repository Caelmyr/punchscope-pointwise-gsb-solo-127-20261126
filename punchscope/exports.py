"""Produce an original-pixel crop and portable evidence for the saved inspection."""

import csv
import json
from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile

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


def report_image(image, settings, openings):
    source = Image.fromarray(image).convert("RGBA")
    source.alpha_composite(analysis.overlay(image, settings, openings))
    draw = ImageDraw.Draw(source)
    box = settings["box"]
    draw.rectangle((box["left"], box["top"], box["right"] - 1, box["bottom"] - 1),
                   outline=(22, 191, 182), width=2)
    row = settings["scan_y"]
    draw.line((box["left"], row, box["right"] - 1, row), fill=(253, 192, 83), width=1)
    return photo.png(source.convert("RGB"))


def crop(image, settings):
    box = settings["box"]
    return photo.png(Image.fromarray(image[box["top"]:box["bottom"], box["left"]:box["right"]]))


def receipt(card, image, openings):
    report = analysis.report(image, card["settings"], openings)
    target = BytesIO()
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("card.json", json.dumps(card, ensure_ascii=False, indent=2))
        archive.writestr("summary.json", json.dumps(report, ensure_ascii=False, indent=2))
        archive.writestr("cross-section.csv", csv_bytes(report))
        archive.writestr("proof.png", report_image(image, card["settings"], openings))
        archive.writestr("crop.png", crop(image, card["settings"]))
    return target.getvalue()
