"""Revisioned inspection cards and their native-orientation source photographs."""

from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

import json
import re

from werkzeug.exceptions import NotFound

from image_lab.validation import Conflict, Invalid, integer, number

KEY = re.compile(r"[0-9a-f]{20}\Z")
BOX = ("left", "top", "right", "bottom")
FIELDS = ("box", "scan_y", "threshold", "min_area", "scale", "edge_margin")


def atomic_json(path, value):
    draft = path.with_suffix(".tmp")
    draft.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    draft.replace(path)


def normalized_settings(settings, size):
    width, height = size
    bounds = settings.get("box", {})
    if not isinstance(bounds, dict):
        raise Invalid("请提供有效量区")
    default = dict(zip(BOX, (0, 0, width, height)))
    box = {
        edge: integer(bounds.get(edge, default[edge]), edge, 0, width if index % 2 == 0 else height)
        for index, edge in enumerate(BOX)
    }
    if box["left"] >= box["right"] or box["top"] >= box["bottom"]:
        raise Invalid("量区至少要有一个像素")
    return {
        "box": box,
        "scan_y": integer(settings.get("scan_y", height // 2), "原片截线 Y", 0, height - 1),
        "threshold": integer(settings.get("threshold", 158), "透光阈值", 1, 254),
        "min_area": integer(settings.get("min_area", 18), "最小孔面积", 1, width * height),
        "scale": number(settings.get("scale", 1), "像素/毫米", 0.0001, 100000),
        "edge_margin": integer(settings.get("edge_margin", min(80, height)), "板边搜寻宽度", 1, height),
    }


class Cards:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def folder(self, key):
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise NotFound("检查卡编号无效")
        return self.root / key

    def list(self):
        cards = []
        with self.lock:
            for path in self.root.glob("*/card.json"):
                if not KEY.fullmatch(path.parent.name):
                    continue
                card = json.loads(path.read_text())
                cards.append({key: card[key] for key in ("id", "name", "revision", "updated", "synthetic")})
        return sorted(cards, key=lambda card: card["updated"], reverse=True)

    def create(self, name, source_name, size, image, settings=None, synthetic=False):
        info = normalized_settings(settings or {}, size)
        with self.lock:
            if len(self.list()) >= 120:
                raise Invalid("检查卡已达 120 张")
            key = uuid4().hex[:20]
            folder = self.folder(key)
            folder.mkdir()
            (folder / "source.png").write_bytes(image)
            now = datetime.now(timezone.utc).isoformat()
            card = {
                "id": key, "name": str(name).strip()[:80] or "未命名检查",
                "source_name": str(source_name)[:120], "size": list(size), "updated": now,
                "synthetic": synthetic, "revision": 1, "settings": info,
            }
            atomic_json(folder / "card.json", card)
            return card

    def read(self, key):
        with self.lock:
            path = self.folder(key) / "card.json"
            if not path.is_file():
                raise NotFound("检查卡不存在")
            return json.loads(path.read_text())

    def save(self, key, body):
        with self.lock:
            card = self.read(key)
            current = integer(body.get("revision"), "检查卡版本", 1)
            if current != card["revision"]:
                raise Conflict("此检查卡已经更新，请刷新后重试")
            changes = {field: body[field] for field in FIELDS if field in body}
            card["settings"] = normalized_settings({**card["settings"], **changes}, card["size"])
            card["revision"] += 1
            card["updated"] = datetime.now(timezone.utc).isoformat()
            atomic_json(self.folder(key) / "card.json", card)
            return card

    def source(self, key):
        self.read(key)
        return self.folder(key) / "source.png"
