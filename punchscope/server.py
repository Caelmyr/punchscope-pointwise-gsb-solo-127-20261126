"""Serve the inspection desk and the existing image preparation workspace."""

import os
from io import BytesIO
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from image_lab import create_app as preparation_app
from image_lab.validation import Invalid, choice, integer, object_value

from . import analysis, apertures, demo, exports, photo
from .store import Cards


def download(data, mime, filename=None):
    return send_file(BytesIO(data), mimetype=mime, max_age=0, download_name=filename,
                     as_attachment=filename is not None)


def create_app(directory=None):
    root = Path(directory or os.environ.get("PUNCHSCOPE_DATA", ".punchscope-data")).resolve()
    desk = preparation_app(root / "prepare")
    cards = Cards(root / "cards")
    api = Blueprint("punchscope", __name__, url_prefix="/punchscope")

    @desk.get("/")
    def home():
        return render_template("punchscope.html")

    @api.get("/health")
    def health():
        return jsonify(status="ok", app="punchscope")

    @api.get("/cards")
    def listing():
        return jsonify(cards=cards.list())

    @api.post("/cards")
    def create():
        request.max_content_length = 19 * 1024 * 1024
        upload = request.files.get("source")
        if not upload or not upload.filename:
            raise Invalid("请选择背光原片")
        size, content = photo.decode(upload.stream.read(photo.MAX_IMAGE_BYTES + 1))
        name = Path(upload.filename).name[:120]
        return jsonify(cards.create(request.form.get("name", ""), name, size, content)), 201

    @api.post("/practice")
    def practice():
        body = object_value(request.get_json(silent=True), "练习选项")
        sample = choice(body.get("sample"), "练习类型", ("panel", "staggered", "slots", "glare"))
        name, size, content, settings = demo.make(sample)
        return jsonify(cards.create(name, f"{sample}.png", size, content, settings, synthetic=True)), 201

    @api.route("/cards/<key>", methods=["GET", "PATCH"])
    def card(key):
        if request.method == "PATCH":
            return jsonify(cards.save(key, object_value(request.get_json(silent=True), "检查设置")))
        return jsonify(cards.read(key))

    @api.get("/cards/<key>/source.png")
    def source(key):
        return send_file(cards.source(key), mimetype="image/png", conditional=True)

    def readings(key):
        card = cards.read(key)
        image = photo.pixels(cards.source(key))
        openings = apertures.detect(image, card["settings"])
        return card, image, openings

    @api.get("/cards/<key>/report")
    def report(key):
        card, image, openings = readings(key)
        return jsonify(revision=card["revision"], **analysis.report(image, card["settings"], openings))

    @api.get("/cards/<key>/probe")
    def probe(key):
        card, image, openings = readings(key)
        x = integer(request.args.get("x"), "原片列 X", 0, image.shape[1] - 1)
        y = integer(request.args.get("y"), "原片行 Y", 0, image.shape[0] - 1)
        return jsonify(revision=card["revision"], **apertures.probe(image, card["settings"], openings, x, y))

    @api.get("/cards/<key>/overlay.png")
    def overlay(key):
        card, image, openings = readings(key)
        return download(photo.png(analysis.overlay(image, card["settings"], openings)), "image/png")

    @api.get("/cards/<key>/section.csv")
    def section_csv(key):
        card, image, openings = readings(key)
        return download(exports.csv_bytes(analysis.report(image, card["settings"], openings)), "text/csv",
                        "punchscope-section.csv")

    @api.get("/cards/<key>/proof.png")
    def proof_png(key):
        card, image, openings = readings(key)
        return download(exports.report_image(image, card["settings"], openings), "image/png",
                        "punchscope-proof.png")

    @api.get("/cards/<key>/crop.png")
    def crop_png(key):
        card, image, openings = readings(key)
        trim = analysis.report(image, card["settings"], openings)["trim"]
        return download(exports.crop(image, card["settings"], trim["adopted"]), "image/png",
                        "punchscope-crop.png")

    @api.get("/cards/<key>/receipt.zip")
    def receipt_zip(key):
        card, image, openings = readings(key)
        return download(exports.receipt(card, image, openings), "application/zip",
                        f"punchscope-r{card['revision']}.zip")

    @api.post("/cards/<key>/prepare")
    def prepare(key):
        card = cards.read(key)
        image = photo.pixels(cards.source(key)).astype("float32") / 255
        services = desk.extensions
        asset = services["assets"].put(image, card["source_name"])
        project = services["store"].create(f"{card['name']} · 原片准备")
        services["frames"].add(project["id"], project["revision"], [asset["id"]])
        return jsonify(url=url_for("index", project=project["id"])), 201

    desk.register_blueprint(api)
    return desk
