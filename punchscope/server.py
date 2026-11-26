"""Serve the inspection desk and the existing image preparation workspace."""

import os
from io import BytesIO
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from image_lab import create_app as preparation_app
from image_lab.validation import Invalid, choice, integer, number, object_value

from . import analysis, apertures, demo, exports, gauge, photo
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

    def gauge_arguments(card, image, required=True):
        raw_hole = request.args.get("hole", request.args.get("hole_id",
                                    request.args.get("holeId", request.args.get("id"))))
        if raw_hole in (None, ""):
            if required:
                raise Invalid("请先在孔件表中选择一个孔件")
            hole_id = None
        else:
            hole_id = integer(raw_hole, "孔件编号", 1, image.shape[0] * image.shape[1])
        raw_width = request.args.get("width", request.args.get("width_px",
                                     request.args.get("widthPx")))
        width_px = integer(raw_width, "扁规宽度 px", 1, max(image.shape[:2])) if raw_width not in (None, "") else None
        raw_width_mm = request.args.get("width_mm")
        if width_px is None and raw_width_mm not in (None, ""):
            width_mm = number(raw_width_mm, "扁规宽度 mm", 0.0001, 100000)
            width_px = max(1, int(round(width_mm * card["settings"]["scale"])))
        raw_y = request.args.get("y")
        y = integer(raw_y, "原片行 Y", 0, image.shape[0] - 1) if raw_y not in (None, "") else None
        return hole_id, width_px, y

    @api.get("/cards/<key>/gauge")
    @api.get("/cards/<key>/flat-gauge")
    @api.get("/cards/<key>/flat-rectangle")
    @api.get("/cards/<key>/slot-gauge")
    @api.get("/cards/<key>/feeler")
    @api.get("/cards/<key>/rectangle-fit")
    def flat_gauge(key):
        card, image, openings = readings(key)
        hole_id, width_px, _ = gauge_arguments(card, image, required=False)
        return jsonify(revision=card["revision"], **gauge.fit(openings, card["settings"], hole_id, width_px))

    @api.get("/cards/<key>/holes/<int:hole_ref>/gauge")
    def hole_gauge(key, hole_ref):
        card, image, openings = readings(key)
        raw_width = request.args.get("width", request.args.get("width_px"))
        width_px = integer(raw_width, "扁规宽度 px", 1, max(image.shape[:2])) if raw_width not in (None, "") else None
        return jsonify(revision=card["revision"], **gauge.fit(openings, card["settings"], hole_ref, width_px))

    def merged_params():
        params = dict(request.args)
        if request.method == "POST":
            body = request.get_json(silent=True)
            if isinstance(body, dict):
                params.update({key: value for key, value in body.items() if value is not None})
        return params

    @api.route("/cards/<key>/gauge/clearance", methods=["GET", "POST"])
    @api.route("/cards/<key>/clearance", methods=["GET", "POST"])
    @api.route("/cards/<key>/gap", methods=["GET", "POST"])
    @api.route("/cards/<key>/gaps", methods=["GET", "POST"])
    @api.route("/cards/<key>/feeler/clearance", methods=["GET", "POST"])
    def gauge_clearance(key):
        card, image, openings = readings(key)
        params = merged_params()

        def param(*names):
            for name in names:
                if name in params and params[name] not in (None, ""):
                    return params[name]
            return None

        raw_hole = param("hole", "hole_id", "holeId", "id")
        if raw_hole in (None, ""):
            raise Invalid("请先在孔件表中选择一个孔件")
        hole_id = integer(raw_hole, "孔件编号", 1, image.shape[0] * image.shape[1])
        raw_width = param("width", "width_px", "widthPx")
        width_px = integer(raw_width, "扁规宽度 px", 1, max(image.shape[:2])) if raw_width is not None else None
        raw_x = param("x", "native_x", "nativeX", "column")
        raw_y = param("y", "native_y", "nativeY", "row")
        x = integer(raw_x, "原片列 X", 0, image.shape[1] - 1) if raw_x is not None else None
        y = integer(raw_y, "原片行 Y", 0, image.shape[0] - 1) if raw_y is not None else None
        raw_station = param("station", "t", "along")
        station = number(raw_station, "沿规位置 px") if raw_station is not None else None
        if x is None and station is None:
            raise Invalid("请在截线图上给出点选的原片列 X")
        if station is not None:
            result = gauge.clearance_at_station(openings, card["settings"], hole_id,
                                                station, y, width_px)
        else:
            result = gauge.clearance(openings, card["settings"], hole_id, x, y, width_px)
        return jsonify(revision=card["revision"], **result)

    @api.post("/cards/<key>/gauge/validate")
    def gauge_validate(key):
        card, image, openings = readings(key)
        body = object_value(request.get_json(silent=True), "扁规矩形")
        hole_id = integer(body.get("hole", body.get("hole_id")), "孔件编号", 1,
                          image.shape[0] * image.shape[1])
        rectangle = object_value(body.get("rectangle", body), "扁规矩形")
        result = gauge.validate_rectangle(openings, card["settings"], hole_id, rectangle)
        return jsonify(revision=card["revision"], **result)

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
        card = cards.read(key)
        image = photo.pixels(cards.source(key))
        return download(exports.crop(image, card["settings"]), "image/png", "punchscope-crop.png")

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
