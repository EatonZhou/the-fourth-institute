"""Authorized, bounded resource reads for the projection bridge."""
from __future__ import annotations

import hashlib
import logging

from management_portal.resources.content_payload import content_payload
from management_portal.resources.errors import resource_error_http_response
from .store import ProjectionError


LOGGER = logging.getLogger(__name__)
RESOURCE_READ_MAX_BYTES = 16 * 1024 * 1024
RESOURCE_BATCH_MAX_ITEMS = 128
RESOURCE_BATCH_MAX_BYTES = 16 * 1024 * 1024
UNAVAILABLE = {"readable": False, "resource_type": "", "title": "", "portal_url": ""}


def _resolve(resources, address, actor):
    resolved = resources.resolve(address, actor=actor)
    if not resolved.get("exists"):
        raise FileNotFoundError(address)
    return {
        "readable": True,
        "resource_type": resolved.get("resource_type", ""),
        "title": resolved.get("title") or (resolved.get("record") or {}).get("title", ""),
        "portal_url": resolved.get("portal_url", ""),
    }


def _read(resources, address, actor, limit):
    # read_content itself resolves and authorizes the complete address. A prior
    # root/resolve result is never an authorization token for this call.
    content, content_type, metadata = resources.read_content(address, actor=actor, max_bytes=limit)
    if len(content) > limit:
        raise ProjectionError("projection.resource_size", "资源正文超过投影读取预算", 413)
    return content, content_type, {
        **metadata, "content_size": len(content),
        "content_sha256": hashlib.sha256(content).hexdigest(),
    }


def _content(address, content, content_type, metadata, format):
    if format == "yaml":
        import yaml
        return {"address": address, "data": yaml.safe_load(content), "metadata": metadata}
    return content_payload(address, content, content_type, metadata)


def _batch(resources, operation, arguments, actor):
    allowed = {"addresses", "format"} if operation == "read_many" else {"addresses"}
    addresses = arguments.get("addresses")
    if set(arguments) - allowed or not isinstance(addresses, list) or not 1 <= len(addresses) <= RESOURCE_BATCH_MAX_ITEMS:
        raise ProjectionError("projection.resource_batch", "批量资源操作需提供 1–128 个地址，且不能覆盖预算或身份")
    if any(not isinstance(address, str) or not address.strip() or len(address) > 4096 for address in addresses):
        raise ProjectionError("projection.resource_batch", "每个资源地址必须是非空字符串，最多 4096 字符")
    if arguments.get("format") not in (None, "text", "yaml"):
        raise ProjectionError("projection.resource_batch", "正文格式仅支持 text/yaml")
    results, total_bytes = [], 0
    for address in addresses:
        try:
            if operation == "resolve_many":
                value = _resolve(resources, address, actor)
            else:
                remaining = RESOURCE_BATCH_MAX_BYTES - total_bytes
                if remaining <= 0:
                    raise ProjectionError("projection.resource_budget", "本批正文预算已用完；请在后续批次读取此地址", 413)
                content, content_type, metadata = _read(resources, address, actor, min(RESOURCE_READ_MAX_BYTES, remaining))
                # Charge complete bytes even if subsequent parsing fails.
                total_bytes += len(content)
                value = _content(address, content, content_type, metadata, arguments.get("format"))
            results.append({"address": address, "ok": True, "result": value})
        except Exception as error:
            status, failure = resource_error_http_response(error)
            if status >= 500:
                LOGGER.exception("Projection resource batch item failed")
            results.append({"address": address, "ok": False, "status": status, "error": failure})
    return {"results": results, "total_bytes": total_bytes, "max_total_bytes": RESOURCE_BATCH_MAX_BYTES}


def dispatch(resources, operation, arguments, actor, *, check_workbench):
    """The HTTP actor and Workbench check are supplied only by the host route."""
    if not isinstance(arguments, dict):
        raise ProjectionError("projection.resource_arguments", "资源参数必须是对象")
    if {"actor", "registry_root", "automatic", "principal_id"}.intersection(arguments):
        raise PermissionError("页面不能指定调用身份或内部配置")
    if operation == "configuration":
        if arguments:
            raise ProjectionError("projection.resource_arguments", "公开读取配置不接受参数")
        check_workbench()
        # No deployment value may enable a historical format or identifier.
        return {"format_namespaces": []}
    if operation in {"resolve_many", "read_many"}:
        result = _batch(resources, operation, arguments, actor)
        # Discard all collected data if Workbench access was revoked in-flight.
        check_workbench()
        return result
    if operation == "resolve":
        try:
            return _resolve(resources, str(arguments["address"]), actor)
        except Exception as error:
            status, failure = resource_error_http_response(error)
            if status not in {403, 404}:
                raise
            return {**UNAVAILABLE, "error": failure, "status": status}
    if operation == "read":
        address = str(arguments["address"])
        return _content(address, *_read(resources, address, actor, RESOURCE_READ_MAX_BYTES), arguments.get("format"))
    if operation == "query":
        return resources.query(actor=actor, **arguments)
    raise ProjectionError("projection.resource_operation", "页面资源入口仅支持 query/read/resolve/read_many/resolve_many/configuration")
