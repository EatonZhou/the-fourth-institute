"""Account-owned PDF highlights, isolated by Paper and PDF fingerprint."""
from __future__ import annotations

import json
import math
import re

from management_portal import presentation_store
from management_portal.resources import get_resource_service

NAMESPACE = "paper-highlights"


def _scope(actor, paper_id, document):
    if not isinstance(actor, str) or not actor.strip():
        raise PermissionError("authenticated actor required")
    if not isinstance(paper_id, str) or not re.fullmatch(r"paper_[0-9a-f]{12}", paper_id):
        raise ValueError("valid paper_id required")
    if not isinstance(document, str) or not re.fullmatch(r"[0-9a-f]{16,128}", document):
        raise ValueError("valid PDF fingerprint required")
    get_resource_service().get(paper_id, actor=actor)
    return f"{paper_id}:{document}:"


def list_highlights(actor, paper_id, document):
    prefix = _scope(actor, paper_id, document)
    with presentation_store.database().read() as db:
        rows = db.execute("""SELECT value_json FROM associations
            WHERE actor=%s AND namespace=%s AND left(association_key,length(%s))=%s
            ORDER BY association_key""", (actor, NAMESPACE, prefix, prefix))
        return {"highlights": [row[0] for row in rows]}


def _highlight(value):
    if not isinstance(value, dict):
        raise ValueError("highlight required")
    identity = value.get("id")
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f-]{36}", identity):
        raise ValueError("valid highlight id required")
    text = value.get("text")
    rects = value.get("rects")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 20000:
        raise ValueError("highlight text must contain 1–20000 characters")
    if not isinstance(rects, list) or not 1 <= len(rects) <= 500:
        raise ValueError("highlight must contain 1–500 rectangles")
    clean = []
    for rect in rects:
        if not isinstance(rect, dict) or type(rect.get("page")) is not int or not 1 <= rect["page"] <= 100000:
            raise ValueError("valid page required")
        coords = [rect.get(k) for k in ("x", "y", "width", "height")]
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in coords):
            raise ValueError("rectangles must use normalized page coordinates")
        x, y, width, height = coords
        if width <= 0 or height <= 0 or x + width > 1.000001 or y + height > 1.000001:
            raise ValueError("rectangle outside page")
        clean.append(dict(page=rect["page"], x=x, y=y, width=width, height=height))
    result = {"id": identity, "text": text.strip(), "rects": clean}
    if "native" in value:
        result["native"] = _native_highlights(value["native"], {r["page"] for r in clean})
    return result


def _native_highlights(parts, pages):
    """Allow only PDF.js highlight geometry, never arbitrary editor/action objects."""
    if not isinstance(parts, list) or not 1 <= len(parts) <= 500:
        raise ValueError("invalid native highlights")
    result = []

    def numbers(values, size=None, maximum=40000):
        if (not isinstance(values, list) or not 1 <= len(values) <= maximum
                or (size is not None and len(values) != size)
                or any(type(n) not in (int, float) or not math.isfinite(n) or abs(n) > 1e7 for n in values)):
            raise ValueError("invalid native highlight coordinates")
        return values

    for part in parts:
        if not isinstance(part, dict) or part.get("annotationType") != 9:
            raise ValueError("only native highlights supported")
        page = part.get("pageIndex")
        if type(page) is not int or page + 1 not in pages:
            raise ValueError("native highlight page mismatch")
        rotation = part.get("rotation")
        if type(rotation) is not int or rotation not in (0, 90, 180, 270):
            raise ValueError("invalid highlight rotation")
        rect = numbers(part.get("rect"), 4)
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            raise ValueError("invalid native highlight bounds")
        color = numbers(part.get("color"), 3)
        if any(type(n) is not int or not 0 <= n <= 255 for n in color):
            raise ValueError("invalid highlight color")
        opacity = part.get("opacity", 1)
        thickness = part.get("thickness", 12)
        for number, maximum in ((opacity, 1), (thickness, 1000)):
            if type(number) not in (int, float) or not math.isfinite(number) or not 0 < number <= maximum:
                raise ValueError("invalid highlight style")
        clean = dict(annotationType=9, pageIndex=page, rotation=rotation, rect=rect,
                     color=color, opacity=opacity, thickness=thickness)
        quads = part.get("quadPoints")
        if quads is not None:
            numbers(quads, maximum=4000)
            if len(quads) % 8:
                raise ValueError("invalid highlight quadrilaterals")
            clean["quadPoints"] = quads
        else:
            outlines = part.get("outlines")
            if not isinstance(outlines, dict) or not isinstance(outlines.get("points"), list) or len(outlines["points"]) != 1:
                raise ValueError("free highlight points required")
            points = numbers(outlines["points"][0])
            if len(points) < 4 or len(points) % 2:
                raise ValueError("invalid free highlight points")
            clean["outlines"] = {"points": [points]}
        result.append(clean)
    if {part["pageIndex"] + 1 for part in result} != pages:
        raise ValueError("native highlight pages mismatch")
    return result


def save_highlight(actor, payload):
    prefix = _scope(actor, payload.get("paper_id"), payload.get("document"))
    operation = payload.get("operation")
    if operation == "add":
        value = _highlight(payload.get("highlight"))
        identity = value["id"]
    elif operation == "delete":
        identity = payload.get("id")
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f-]{36}", identity):
            raise ValueError("valid highlight id required")
    else:
        raise ValueError("operation must be add or delete")
    with presentation_store.database().transaction(f"{NAMESPACE}:{actor}:{prefix}") as db:
        key = prefix + identity
        if operation == "delete":
            db.execute("DELETE FROM associations WHERE actor=%s AND namespace=%s AND association_key=%s",
                       (actor, NAMESPACE, key))
        else:
            db.execute("""INSERT INTO associations(actor,namespace,association_key,value_json,updated_at)
                VALUES (%s,%s,%s,%s::jsonb,now()) ON CONFLICT(actor,namespace,association_key)
                DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                       (actor, NAMESPACE, key, json.dumps(value)))
    return {"id": identity, "operation": operation}
