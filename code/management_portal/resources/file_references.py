"""Object identity plus a portable relative filename, independent of placement.

ReadResource/ResolveResource accept exactly one of workspace_id, repo_id or
article_id together with relative_path. Empty relative_path addresses the root.
The /file string address remains a protocol representation; host absolute paths
are never fallbacks. Service reads resolve identity and permission before opening
the current registered directory. This module only validates/encodes references;
it neither owns directory placement nor queries Society.

Persist file_reference in context_refs.resources for report/deliverable links.
"""
from collections.abc import Mapping
from pathlib import PureWindowsPath
from urllib.parse import unquote

from management_common.resource_identity import get_resource_type_registry
from management_common.secure_paths import relative_parts

FILE_REFERENCE_KEYS = {"workspace_id": "workspace", "repo_id": "repo", "article_id": "article"}


def file_reference_parts(value):
    if not isinstance(value, Mapping):
        raise ValueError("File reference must be an object")
    keys = set(value) & FILE_REFERENCE_KEYS.keys()
    if len(keys) != 1 or set(value) != keys | {"relative_path"}:
        raise ValueError("File reference requires exactly one object ID and relative_path")
    key = next(iter(keys))
    identifier = value[key]
    if not isinstance(identifier, str) or identifier != identifier.strip():
        raise ValueError("File reference ID does not match its object type")
    get_resource_type_registry().validate(identifier, FILE_REFERENCE_KEYS[key])
    relative = value["relative_path"]
    if not isinstance(relative, str):
        raise ValueError("relative_path must be text")
    portable_parts(relative)
    return identifier, relative


def portable_parts(relative):
    if PureWindowsPath(relative).drive:
        raise ValueError("relative_path requires / separators and no drive")
    # Slash is always the separator. Other characters retain the current
    # filesystem's filename semantics: a POSIX literal backslash/newline is
    # not traversal, while relative_parts rejects Windows reserved names.
    return relative_parts(relative) if relative else ()


def file_reference_address(value):
    identifier, relative = file_reference_parts(value)
    return identifier + "/file" + ("/" + relative.replace("%", "%25") if relative else "")


def file_reference_for(identifier, relative):
    key = identifier.rpartition("_")[0] + "_id"
    if key not in FILE_REFERENCE_KEYS:
        return None
    value = {key: identifier, "relative_path": relative}
    file_reference_parts(value)
    return value


def file_reference_from_address(address):
    """Migrate an explicit protocol address, never guess from an absolute path."""
    if not isinstance(address, str):
        return None
    identifier, separator, suffix = unquote(address).partition("/")
    if not separator or not (suffix == "file" or suffix.startswith("file/")):
        return None
    return file_reference_for(identifier, suffix.partition("/")[2])
