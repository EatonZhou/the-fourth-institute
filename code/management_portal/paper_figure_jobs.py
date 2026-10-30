"""Figure explanations and generated figures: background jobs run by the figure role Residents."""

from __future__ import annotations

import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import secrets

from PIL import Image

from management_common.format_tags import schema_matches
from management_portal.paper_figures import _figure_public, _figure_update, _figures_dir, _load_figures
from management_portal.paper_records import _now, _slugify
from management_portal.paper_store import (
    DATA_ROOT, LOCK, WORKSPACE_ROOT, _paper_by_id, _paper_root, _paper_store_file, _read_text_preview,
    _safe_file_path,
)
from management_portal.shared_state import atomic_replace_json


PAPER_FIGURE_IMAGE_MAX_BYTES = 25 * 1024 * 1024
PAPER_FIGURE_IMAGE_MAX_PIXELS = 40_000_000
FIGURE_EXPLANATION_RESULT_SCHEMA = "tfi.paper.figure-explanation.result.v1"


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default)) or str(default)))
    except ValueError:  # a setting that is not an integer
        return max(minimum, int(default))


FIGURE_DRAFT_STALE_SECONDS = _env_int("PAPER_FIGURE_DRAFT_STALE_SECONDS", 90 * 60, minimum=1)


def upload_figure_input_image(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    data = payload.get("data_bytes")
    filename = str(payload.get("filename") or "image.png").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("image file required")
    if len(data) > PAPER_FIGURE_IMAGE_MAX_BYTES:
        raise ValueError("image file is too large")
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    paper_root = _paper_root(paper)
    if not paper_root:
        raise ValueError("paper store root unavailable")
    try:
        image = Image.open(BytesIO(bytes(data)))
        width, height = image.size
        if (
            width <= 0
            or height <= 0
            or width * height > PAPER_FIGURE_IMAGE_MAX_PIXELS
        ):
            raise ValueError("image dimensions are too large")
        image.load()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("uploaded file is not a readable image") from exc
    target_dir = paper_root / "figures" / "input_images"
    target_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    target = target_dir / f"{token}.png"
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA")
    image.save(target, format="PNG")
    rel_path = target.relative_to(paper_root).as_posix()
    return {
        "paper_id": paper_id,
        "filename": filename,
        "path": str(target),
        "rel_path": rel_path,
        "created_by": str(actor or "").strip(),
        "created_at": _now(),
    }


def _figure_image_path(paper: dict, figure: dict) -> Path | None:
    root = _paper_root(paper)
    rel_path = str(figure.get("image_rel_path") or "").strip()
    if not root or not rel_path:
        return None
    path = Path(rel_path)
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            return None
        return resolved if resolved.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _run_figure_resident(prompt: str, paper: dict, figure: dict, kind: str, images: list[Path] | None = None, generated_id: str = "") -> tuple[str, dict]:
    """Explain a figure (``paper:figure``) or generate an image (``paper:figure-generation``) with the role Resident."""
    from management_portal.paper_projection import input_receipt, resume, turn

    paper_root = _paper_root(paper) or DATA_ROOT
    resident_role = "paper:figure" if kind == "explain" else "paper:figure-generation"
    runs_dir = paper_root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    safe_figure_id = _slugify(str(figure.get("figure_id") or "figure"))
    run_id = f"figure_{_slugify(kind)}_{safe_figure_id}_{secrets.token_hex(4)}"
    output_path = runs_dir / f"{run_id}.md"
    events_path = runs_dir / f"{run_id}.events.jsonl"
    stderr_path = runs_dir / f"{run_id}.stderr.txt"

    actor = str(paper.get("created_by") or "portal")
    paper_id = str(paper.get("id") or paper.get("title") or "paper")
    operation = next((item for item in figure.get("generated", []) if item.get("generated_id") == generated_id), {}) if generated_id else figure
    binding_key = "invocation" if generated_id else "explanation_invocation"
    binding = operation.get(binding_key) or {}
    receipt = input_receipt(binding) if binding else {}
    input_message_id = str(receipt.get("input_message_id") or "")
    message_id = str(binding.get("requested_message_id") or input_message_id or f"msg_{secrets.token_hex(16)}")

    def remember(handle: dict) -> None:
        updates = {binding_key: {**input_receipt(handle), "actor": actor}}
        if generated_id:
            _set_figure_generated_item(paper_id, figure["figure_id"], generated_id, updates)
        else:
            _set_figure_fields(paper_id, figure["figure_id"], updates)

    if binding.get("resident_id") and (not input_message_id or binding.get("input_identity_status") == "unconfirmed"):
        raise RuntimeError("Figure input delivery is unconfirmed; inspect the original workflow; no input was resubmitted")
    input_media = []
    if images and not binding.get("resident_id"):
        import base64
        from management_portal.society_service import get_society_service
        from management_portal.workbench_projections.native import ensure

        sources = [Path(image).resolve(strict=True) for image in images]
        for source in sources:
            if not source.is_file() or not source.is_relative_to(paper_root.resolve(strict=True)):
                raise PermissionError("Figure input image is outside this paper's artifacts")
        identity = ensure("paper", actor=actor, resident_scope=resident_role, cwd=str(WORKSPACE_ROOT))
        for source in sources:
            uploaded = get_society_service().workspace.save_workspace_pasted_image({
                "resident_id": identity["resident_id"], "filename": source.name,
                "data_base64": base64.b64encode(source.read_bytes()).decode("ascii"),
            }, actor=actor)
            input_media.append({
                "media_type": "image", "source_type": "local_path",
                "source": uploaded["path"], "name": source.name,
                "mime_type": uploaded["content_type"],
            })
    if binding.get("resident_id") and input_message_id:
        result = resume(
            actor=str(binding.get("actor") or actor), resident_id=binding["resident_id"],
            lineage_id=str(binding.get("lineage_id") or ""), message_id=input_message_id, receipt=binding, on_submitted=remember,
        )
    else:
        result = turn(
            actor=actor, prompt=prompt, cwd=str(WORKSPACE_ROOT), resident_scope=resident_role,
            input_media=input_media, business_ref=run_id, message_id=message_id,
            on_assigned=remember, on_submitted=remember,
            metadata={"business_type": "paper_figure", "kind": kind},
        )
    identity = input_receipt(result["invocation"])
    atomic_replace_json(events_path, identity, indent=None, sort_keys=False)
    stderr_path.write_text("", encoding="utf-8")
    content = str(result.get("message") or "").strip()
    if not content:
        raise RuntimeError(f"{resident_role} returned empty output for figure {kind}")
    output_path.write_text(content, encoding="utf-8")
    usage = {
        **dict(result.get("usage") or {}),
        **identity,
        "kind": kind,
        "events_path": str(events_path),
        "stderr_path": str(stderr_path),
        "output_path": str(output_path),
    }
    return content, usage


class FigureExplanationPersistenceError(RuntimeError):
    pass


def explain_figure(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    figure_id = str(payload.get("figure_id") or "").strip()
    if not paper_id or not figure_id:
        raise ValueError("paper_id and figure_id required")
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    figures = _load_figures(paper)
    figure = next((item for item in figures if str(item.get("figure_id") or "") == figure_id), None)
    if not figure:
        raise KeyError("figure not found")
    explanation, usage = _generate_figure_explanation(paper, figure)
    if not isinstance(explanation, str) or not explanation.strip():
        raise ValueError("Figure explanation returned no generated content")
    committed_at = _now()
    receipt = {
        "schema": "tfi.paper.figure-explanation.commit.v1", "commit_id": secrets.token_hex(12),
        "paper_id": paper_id, "figure_id": figure_id,
        "content_sha256": hashlib.sha256(explanation.encode("utf-8")).hexdigest(),
        "committed_by": actor, "committed_at": committed_at,
    }
    write_error = None
    try:
        _set_figure_fields(paper_id, figure_id, {
            "explanation": explanation, "explanation_usage": usage,
            "explanation_status": "done", "explanation_error": "",
            "explanation_generation_status": "generated", "explanation_persistence_status": "saved",
            "explanation_commit": receipt,
            "explained_by": actor, "explained_at": committed_at, "updated_at": committed_at,
        })
    except Exception as exc:
        write_error = exc
    try:
        persisted = next((item for item in _load_figures(paper) if item.get("figure_id") == figure_id), {})
    except Exception as exc:
        raise FigureExplanationPersistenceError(f"Figure explanation generated but persistence could not be verified: {exc}") from exc
    if (persisted.get("explanation_commit") != receipt or persisted.get("explanation") != explanation
            or persisted.get("explanation_status") != "done" or persisted.get("explanation_persistence_status") != "saved"):
        raise FigureExplanationPersistenceError(
            f"Figure explanation generated but persistence could not be verified: {write_error or 'commit receipt missing'}"
        ) from write_error
    result = {"figure": _figure_public(persisted, paper_id)}
    if write_error is not None:
        result["persistence_warning"] = f"Explanation persisted; post-write handling reported: {write_error}"
    return result

def start_figure_explanation(payload: dict, actor: str) -> dict:
    with LOCK:
        return _queue_figure_explanation(payload, actor)


def _queue_figure_explanation(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    figure_id = str(payload.get("figure_id") or "").strip()
    if not paper_id or not figure_id:
        raise ValueError("paper_id and figure_id required")
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    with _figure_update(paper) as figures:
        figure = next((item for item in figures if str(item.get("figure_id") or "") == figure_id), None)
        if not figure:
            raise KeyError("figure not found")
        if str(figure.get("explanation_status") or "") in {"queued", "running"}:
            return {"figure": _figure_public(figure, paper_id), "status": figure.get("explanation_status") or "queued"}
        figure["explanation_status"] = "queued"
        figure["explanation_generation_status"] = "queued"
        figure["explanation_persistence_status"] = "pending"
        figure["explanation_commit"] = {}
        figure["explanation_error"] = ""
        figure["explanation_invocation"] = {}
        figure["explained_by"] = actor
        figure["explain_requested_at"] = _now()
        figure["updated_at"] = _now()
    launch_error = _start_figure_explain_task(paper, figure_id, actor)
    if launch_error:
        _set_figure_fields(paper_id, figure_id, {
            "explanation_status": "error", "explanation_error": launch_error,
            "updated_at": _now(),
        })
        paper = _paper_by_id(paper_id) or paper
        figure = next((item for item in _load_figures(paper) if str(item.get("figure_id") or "") == figure_id), figure)
    return {"figure": _figure_public(figure, paper_id), "status": figure.get("explanation_status") or "queued"}


def generate_figure_draft(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    figure_id = str(payload.get("figure_id") or "").strip()
    request = str(payload.get("request") or payload.get("message") or "").strip()
    generated_id = str(payload.get("generated_id") or "").strip()
    source_generated_id = str(payload.get("source_generated_id") or "").strip()
    if not paper_id or not figure_id:
        raise ValueError("paper_id and figure_id required")
    if not request:
        raise ValueError("request required")
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    figures = _load_figures(paper)
    figure = next((item for item in figures if str(item.get("figure_id") or "") == figure_id), None)
    if not figure:
        raise KeyError("figure not found")
    if not generated_id:
        generated_id = f"gen-{secrets.token_hex(5)}"
    with _figure_update(paper) as current_figures:
        current_figure = next((item for item in current_figures if item.get("figure_id") == figure_id), None)
        if current_figure is None:
            raise KeyError("figure not found")
        generated = current_figure.setdefault("generated", [])
        if not any(item.get("generated_id") == generated_id for item in generated):
            generated.insert(0, {
                "generated_id": generated_id, "status": "queued", "kind": "image",
                "request": request, "created_by": actor, "created_at": _now(),
                "source_generated_id": source_generated_id,
                "extra_image_paths": [str(path) for path in _safe_figure_input_image_paths(paper, payload.get("extra_image_paths"))],
            })
        figure = current_figure
    extra_image_paths = _safe_figure_input_image_paths(paper, payload.get("extra_image_paths"))
    image_rel_path, resident_note, usage = _generate_figure_image(
        paper,
        figure,
        request,
        generated_id,
        source_generated_id=source_generated_id,
        extra_image_paths=extra_image_paths,
    )
    with _figure_update(paper) as figures:
        figure = next((item for item in figures if str(item.get("figure_id") or "") == figure_id), None)
        if not figure:
            raise KeyError("figure not found")
        generated = figure.get("generated") if isinstance(figure.get("generated"), list) else []
        item = {
            "generated_id": generated_id,
            "kind": "image",
            "status": "done",
            "request": request,
            "prompt": resident_note,
            "image_rel_path": image_rel_path,
            "source_generated_id": source_generated_id,
            "extra_image_paths": [str(path) for path in extra_image_paths],
            "usage": usage,
            "created_by": actor,
            "created_at": _now(),
            "updated_at": _now(),
        }
        replaced = False
        for index, existing in enumerate(generated):
            if str(existing.get("generated_id") or "") == str(item.get("generated_id") or ""):
                item["created_at"] = int(existing.get("created_at") or item["created_at"])
                generated[index] = {**existing, **item}
                item = generated[index]
                replaced = True
                break
        if not replaced:
            generated.insert(0, item)
        figure["generated"] = generated[:8]
        figure["updated_at"] = _now()
    return {"figure": _figure_public(figure, paper_id), "generated": item}


def start_figure_draft(payload: dict, actor: str) -> dict:
    with LOCK:
        return _queue_figure_draft(payload, actor)


def _queue_figure_draft(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    figure_id = str(payload.get("figure_id") or "").strip()
    request = str(payload.get("request") or payload.get("message") or "").strip()
    source_generated_id = str(payload.get("source_generated_id") or "").strip()
    if not paper_id or not figure_id:
        raise ValueError("paper_id and figure_id required")
    if not request:
        raise ValueError("request required")
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    extra_image_paths = _safe_figure_input_image_paths(paper, payload.get("extra_image_paths"))
    with _figure_update(paper) as figures:
        figure = next((item for item in figures if str(item.get("figure_id") or "") == figure_id), None)
        if not figure:
            raise KeyError("figure not found")
        generated = figure.get("generated") if isinstance(figure.get("generated"), list) else []
        now = _now()
        for existing in generated:
            if (
                isinstance(existing, dict)
                and str(existing.get("request") or "").strip() == request
                and str(existing.get("source_generated_id") or "").strip() == source_generated_id
                and [str(path) for path in extra_image_paths] == list(existing.get("extra_image_paths") or [])
                and str(existing.get("status") or "") in {"queued", "running"}
            ):
                touched = int(existing.get("updated_at") or existing.get("created_at") or 0)
                if touched > 0 and now - touched <= FIGURE_DRAFT_STALE_SECONDS:
                    return {"figure": _figure_public(figure, paper_id), "generated": existing, "status": existing.get("status") or "queued"}
                existing["status"] = "error"
                existing["error"] = "Previous figure generation business task became stale before finishing."
                existing["usage"] = {
                    **(existing.get("usage") if isinstance(existing.get("usage"), dict) else {}),
                    "error": existing["error"],
                    "stale_after_seconds": FIGURE_DRAFT_STALE_SECONDS,
                }
                existing["updated_at"] = now
        generated_id = f"gen-{secrets.token_hex(5)}"
        queued = {
            "generated_id": generated_id,
            "kind": "image",
            "status": "queued",
            "request": request,
            "prompt": "",
            "image_rel_path": "",
            "source_generated_id": source_generated_id,
            "extra_image_paths": [str(path) for path in extra_image_paths],
            "usage": {},
            "created_by": actor,
            "created_at": _now(),
            "updated_at": _now(),
        }
        generated.insert(0, queued)
        figure["generated"] = generated[:8]
        figure["updated_at"] = _now()
    launch_error = _start_figure_draft_task(
        paper,
        figure_id,
        generated_id,
        request,
        actor,
        source_generated_id=source_generated_id,
        extra_image_paths=extra_image_paths,
    )
    if launch_error:
        _set_figure_generated_item(
            paper_id,
            figure_id,
            generated_id,
            {
                "status": "error",
                "error": launch_error,
                "usage": {"error": launch_error},
                "updated_at": _now(),
            },
        )
        paper = _paper_by_id(paper_id) or paper
        figure = next((item for item in _load_figures(paper) if str(item.get("figure_id") or "") == figure_id), figure)
        queued = next((item for item in (figure.get("generated") or []) if str(item.get("generated_id") or "") == generated_id), queued)
    return {"figure": _figure_public(figure, paper_id), "generated": queued, "status": queued.get("status") or "queued"}


def delete_generated_figure(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    figure_id = str(payload.get("figure_id") or "").strip()
    generated_id = str(payload.get("generated_id") or "").strip()
    if not paper_id or not figure_id or not generated_id:
        raise ValueError("paper_id, figure_id and generated_id required")
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    paper_root = _paper_root(paper)
    with _figure_update(paper) as figures:
        updated_figure = None
        removed = None
        for figure in figures:
            if str(figure.get("figure_id") or "") != figure_id:
                continue
            generated = figure.get("generated") if isinstance(figure.get("generated"), list) else []
            kept = []
            for item in generated:
                if isinstance(item, dict) and str(item.get("generated_id") or "") == generated_id:
                    removed = item
                    continue
                kept.append(item)
            if removed is None:
                raise KeyError("generated figure not found")
            figure["generated"] = kept
            figure["updated_at"] = _now()
            updated_figure = figure
            break
        if not updated_figure:
            raise KeyError("figure not found")
        rel_path = str((removed or {}).get("image_rel_path") or "").strip()
        if paper_root and rel_path:
            try:
                path = (paper_root / rel_path).resolve(strict=False)
                if paper_root.resolve() in path.parents and path.is_file():
                    path.unlink()
            except (OSError, RuntimeError, ValueError):
                # Gone already or unremovable; resolve() raises RuntimeError on a
                # symlink loop and ValueError on a NUL character.
                pass
    return {"figure": _figure_public(updated_figure, paper_id), "deleted": generated_id}


def _start_figure_explain_task(paper: dict, figure_id: str, actor: str) -> str:
    from management_portal import business_tasks

    paper_id = str(paper.get("id") or "").strip()
    def fail(exc: BaseException) -> None:
        _set_figure_fields(paper_id, figure_id, {
            "explanation_status": "error",
            "explanation_error": str(exc)[:2000],
            "updated_at": _now(),
        })

    try:
        business_tasks.start(
            f"paper-figure-explain:{paper_id}:{figure_id}",
            run_figure_explain_job,
            paper_id,
            figure_id,
            actor,
            on_error=fail,
        )
    except Exception as exc:
        return f"Failed to run figure explanation business task: {exc}"
    return ""


def _recover_figure_tasks(paper: dict) -> None:
    with LOCK:
        _resume_figure_tasks(paper)


def _resume_figure_tasks(paper: dict) -> None:
    from management_portal import business_tasks

    paper_id = paper["id"]
    for figure in _load_figures(paper):
        figure_id = str(figure.get("figure_id") or "")
        if figure.get("explanation_status") in {"queued", "running"} and not business_tasks.is_active(f"paper-figure-explain:{paper_id}:{figure_id}"):
            binding = figure.get("explanation_invocation") or {}
            if (binding.get("resident_id") and binding.get("input_message_id")
                    and binding.get("input_identity_status") != "unconfirmed"):
                _start_figure_explain_task(paper, figure_id, str(figure.get("explained_by") or paper.get("created_by") or ""))
            else:
                _set_figure_fields(paper_id, figure_id, {
                    "explanation_status": "error",
                    "explanation_error": "Figure task interrupted before its Resident input binding was saved. Check the Resident before retrying; no input was resubmitted.",
                })
        for item in figure.get("generated") or []:
            generated_id = str(item.get("generated_id") or "")
            if item.get("status") not in {"queued", "running"} or business_tasks.is_active(f"paper-figure-draft:{generated_id}"):
                continue
            binding = item.get("invocation") or {}
            if (binding.get("resident_id") and binding.get("input_message_id")
                    and binding.get("input_identity_status") != "unconfirmed"):
                _start_figure_draft_task(
                    paper, figure_id, generated_id, str(item.get("request") or ""),
                    str(item.get("created_by") or paper.get("created_by") or ""),
                    source_generated_id=str(item.get("source_generated_id") or ""),
                    extra_image_paths=_safe_figure_input_image_paths(paper, item.get("extra_image_paths")),
                )
            else:
                _set_figure_generated_item(paper_id, figure_id, generated_id, {
                    "status": "error",
                    "error": "Figure task interrupted before its Resident input binding was saved. Check the Resident before retrying; no input was resubmitted.",
                })


def _start_figure_draft_task(
    paper: dict,
    figure_id: str,
    generated_id: str,
    request: str,
    actor: str,
    source_generated_id: str = "",
    extra_image_paths: list[Path] | None = None,
) -> str:
    from management_portal import business_tasks

    paper_id = str(paper.get("id") or "").strip()
    image_values = [str(path) for path in (extra_image_paths or [])]
    def fail(exc: BaseException) -> None:
        _set_figure_generated_item(
            paper_id,
            figure_id,
            generated_id,
            {
                "status": "error",
                "error": str(exc)[:2000],
                "updated_at": _now(),
            },
        )

    try:
        business_tasks.start(
            f"paper-figure-draft:{generated_id}",
            run_figure_draft_job,
            paper_id,
            figure_id,
            generated_id,
            request,
            actor,
            source_generated_id=source_generated_id,
            extra_image_paths=image_values,
            on_error=fail,
        )
    except Exception as exc:
        return f"Failed to run figure draft business task: {exc}"
    return ""


def _set_figure_generated_item(paper_id: str, figure_id: str, generated_id: str, updates: dict) -> dict:
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    with _figure_update(paper) as figures:
        updated_figure = None
        updated_item = None
        for figure in figures:
            if str(figure.get("figure_id") or "") != figure_id:
                continue
            generated = figure.get("generated") if isinstance(figure.get("generated"), list) else []
            for item in generated:
                if isinstance(item, dict) and str(item.get("generated_id") or "") == generated_id:
                    item.update(updates)
                    item["updated_at"] = int(item.get("updated_at") or _now())
                    updated_item = item
                    break
            figure["generated"] = generated
            figure["updated_at"] = _now()
            updated_figure = figure
            break
        if not updated_figure or not updated_item:
            raise KeyError("figure draft not found")
    return {"figure": _figure_public(updated_figure, paper_id), "generated": updated_item}


def _set_figure_fields(paper_id: str, figure_id: str, updates: dict) -> dict:
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    with _figure_update(paper) as figures:
        updated_figure = None
        for figure in figures:
            if str(figure.get("figure_id") or "") == figure_id:
                figure.update(updates)
                figure["updated_at"] = int(figure.get("updated_at") or _now())
                updated_figure = figure
                break
        if not updated_figure:
            raise KeyError("figure not found")
    return {"figure": _figure_public(updated_figure, paper_id)}


def run_figure_explain_job(paper_id: str, figure_id: str, actor: str) -> dict:
    try:
        _set_figure_fields(paper_id, figure_id, {
            "explanation_status": "running", "explanation_generation_status": "running",
            "explanation_persistence_status": "pending", "explanation_commit": {}, "updated_at": _now(),
        })
        return explain_figure({"paper_id": paper_id, "figure_id": figure_id}, actor)
    except Exception as exc:
        persistence_failure = isinstance(exc, FigureExplanationPersistenceError)
        return _set_figure_fields(paper_id, figure_id, {
            "explanation_status": "error", "explanation_error": str(exc),
            "explanation_generation_status": "generated" if persistence_failure else "error",
            "explanation_persistence_status": "error" if persistence_failure else "not_saved",
            "explanation_commit": {},
            "updated_at": _now(),
        })

def run_figure_draft_job(
    paper_id: str,
    figure_id: str,
    generated_id: str,
    request: str,
    actor: str,
    source_generated_id: str = "",
    extra_image_paths: list[str] | None = None,
) -> dict:
    _set_figure_generated_item(paper_id, figure_id, generated_id, {"status": "running", "updated_at": _now()})
    try:
        return generate_figure_draft(
            {
                "paper_id": paper_id,
                "figure_id": figure_id,
                "generated_id": generated_id,
                "request": request,
                "source_generated_id": source_generated_id,
                "extra_image_paths": extra_image_paths or [],
            },
            actor,
        )
    except Exception as exc:
        return _set_figure_generated_item(
            paper_id,
            figure_id,
            generated_id,
            {
                "status": "error",
                "error": str(exc),
                "usage": {"error": str(exc)},
                "updated_at": _now(),
            },
        )


def _generate_figure_explanation(paper: dict, figure: dict) -> tuple[str, dict]:
    analysis_path = _paper_store_file(paper, "analysis")
    analysis = _read_text_preview(analysis_path, limit=18_000)
    pdf_path = _safe_file_path(str(paper.get("pdf_path") or ""))
    image_path = _figure_image_path(paper, figure)
    if not image_path:
        raise FileNotFoundError("Figure image missing")
    prompt = f"""你是 TFI Portal 的论文 Figure 解读助手。

请查看给定 Figure 图片，并结合论文 metadata、caption、analysis excerpt 和 PDF 路径解释这张图。不要只根据 caption 猜测；优先使用图片中的可见结构、文字、坐标、箭头、模块和图例。必要时可以读取 PDF 文件或 analysis 文件。

Figure image:
[image: {image_path}]

Paper PDF:
[file: {pdf_path or paper.get('pdf_path') or ''}]

Analysis file:
{analysis_path or ''}

本次只负责生成解释，Paper 服务负责校验和正式保存，不要求你直接写入文件或调用提交接口。
生成成功不表示已经正式保存；保存成功与否由 Paper 服务根据真实持久化回执确认。
请只返回一个 JSON 对象，不要代码围栏或前后说明，字段如下：
{{"schema": "{FIGURE_EXPLANATION_RESULT_SCHEMA}", "paper_id": "{paper.get('id') or ''}", "figure_id": "{figure.get('figure_id') or ''}", "status": "generated", "explanation": "中文 Markdown 解读正文", "error": ""}}
只有解释生成成功且有实际内容时才能使用 status=generated；无法生成时使用 status=error，explanation 为空，error 说明真实失败原因。
不要把未要求你执行的正式保存当成生成失败，不要生成或伪造保存回执。
explanation 字段的中文 Markdown 正文包含：

## 图在表达什么
## 读图顺序
## 它支持论文哪个结论
## 需要回看原图或正文确认的点

Paper:
- Title: {paper.get('title') or ''}
- Authors: {paper.get('authors') or ''}
- Venue: {paper.get('venue') or ''} {paper.get('year') or ''}
- Abstract: {paper.get('abstract') or ''}

Figure:
- Label: {figure.get('label') or figure.get('figure_id') or ''}
- Page: {figure.get('page') or ''}
- Size: {figure.get('width') or ''} x {figure.get('height') or ''}
- Caption: {figure.get('caption') or ''}

Analysis excerpt:
{analysis}
"""
    content, usage = _run_figure_resident(prompt, paper, figure, "explain", images=[image_path])
    try:
        generated = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise ValueError("Figure explanation requires a structured generation result, not a submission claim or free text") from exc
    if (not isinstance(generated, dict)
            or not schema_matches(generated.get("schema"), "tfi.paper.figure-explanation.result.v1")
            or generated.get("paper_id") != paper.get("id") or generated.get("figure_id") != figure.get("figure_id")):
        raise ValueError("Figure explanation generation result schema or Paper/Figure identity mismatch")
    if generated.get("status") == "error":
        raise RuntimeError(f"Figure explanation generation failed: {str(generated.get('error') or 'no reason provided')[:2000]}")
    explanation = generated.get("explanation")
    if (generated.get("status") != "generated" or not isinstance(explanation, str) or not explanation.strip()
            or generated.get("error") not in (None, "")):
        raise ValueError("Figure explanation did not return a successful generated artifact")
    return explanation.strip(), usage


def _generated_figure_image_path(paper: dict, figure: dict, generated_id: str) -> Path | None:
    paper_root = _paper_root(paper)
    if not paper_root or not generated_id:
        return None
    for item in figure.get("generated") or []:
        if not isinstance(item, dict) or str(item.get("generated_id") or "") != generated_id:
            continue
        rel_path = str(item.get("image_rel_path") or "").strip()
        if not rel_path:
            return None
        path = paper_root / rel_path
        try:
            resolved = path.resolve(strict=False)
            if not resolved.is_relative_to(paper_root.resolve()):
                return None
            return resolved if resolved.is_file() else None
        except (OSError, RuntimeError, ValueError):
            return None
    return None


def _safe_figure_input_image_paths(paper: dict, raw_paths: object) -> list[Path]:
    paper_root = _paper_root(paper)
    if not paper_root or not isinstance(raw_paths, list):
        return []
    safe_paths: list[Path] = []
    try:
        resolved_root = paper_root.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):  # RuntimeError: a symlink loop; ValueError: a NUL character
        return []
    for raw in raw_paths[:8]:
        path = _safe_file_path(str(raw or ""))
        if not path:
            continue
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(resolved_root)
        except (OSError, RuntimeError, ValueError):  # ValueError: outside the paper's folder
            continue
        if resolved.is_file():
            safe_paths.append(resolved)
    return safe_paths


def _generate_figure_image(
    paper: dict,
    figure: dict,
    request: str,
    generated_id: str,
    source_generated_id: str = "",
    extra_image_paths: list[Path] | None = None,
) -> tuple[str, str, dict]:
    figures_dir = _figures_dir(paper)
    paper_root = _paper_root(paper)
    source_generated_id = str(source_generated_id or "").strip()
    image_path = _generated_figure_image_path(paper, figure, source_generated_id) if source_generated_id else _figure_image_path(paper, figure)
    source_label = f"generated image {source_generated_id}" if source_generated_id else "original Figure"
    if not figures_dir or not paper_root:
        raise ValueError("paper has no figure store")
    if not image_path:
        raise ValueError("source image missing")
    generated_dir = figures_dir / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    output_path = generated_dir / f"{_slugify(generated_id)}.png"
    analysis_path = _paper_store_file(paper, "analysis")
    analysis = _read_text_preview(analysis_path, limit=8_000)
    extra_image_paths = [path for path in (extra_image_paths or []) if path and path.is_file()]
    extra_images_note = "\n".join(f"- {path}" for path in extra_image_paths) if extra_image_paths else "None"
    prompt = f"""你是 TFI Portal 的论文 Figure 生成助手。

请使用 `$imagegen` 基于输入的原 Figure 生成一张新的 PNG 图片。必须把最终图片文件保存到这个路径：

{output_path}

如果 `$imagegen` 把图片保存到了 `$CODEX_HOME/generated_images/...`，请把最终选择的 PNG 复制或转换到上面的路径。最终路径必须存在，而且必须是 PNG 图片。

输入图片中的第一张是待修改的 {source_label}。后续图片是用户从 Paper 输入框粘贴或在放大视图标注后提供的参考图。请优先参考这些图片中的圈画、箭头、文字说明和视觉修改意图。

Additional reference images:
{extra_images_note}

请保持论文含义，不要编造新的实验数据、坐标值、方法名或结论。用户修改需求优先；如果需求含糊，就生成更清晰、更适合论文展示的版本。

Paper title: {paper.get('title') or ''}
Figure label: {figure.get('label') or figure.get('figure_id') or ''}
Figure caption: {figure.get('caption') or ''}
Existing explanation: {figure.get('explanation') or ''}

Analysis excerpt:
{analysis}

User modification request:
{request}

输出要求：
- 生成或编辑一张实际图片，不要只写 prompt。
- 保存 PNG 到指定路径。
- 回复中只简要说明保存路径和主要改动。
"""
    input_images = [image_path, *extra_image_paths]
    content, usage = _run_figure_resident(prompt, paper, figure, "generate-image", images=input_images, generated_id=generated_id)
    if not output_path.is_file():
        raise RuntimeError(f"paper:figure-generation did not write generated image: {output_path}")
    try:
        with Image.open(output_path) as image:
            image.verify()
    except Exception as exc:
        raise RuntimeError(f"generated image is not a readable image: {output_path}") from exc
    rel_path = output_path.relative_to(paper_root).as_posix()
    usage = {
        **usage,
        "output_image_path": str(output_path),
        "source_image_path": str(image_path),
        "source_generated_id": source_generated_id,
        "extra_image_paths": [str(path) for path in extra_image_paths],
    }
    return rel_path, content.strip(), usage
