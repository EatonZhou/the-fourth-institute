"""Where exported files live in an engineering bundle, and how records name them.

A bundle stores each instance file under ``data/<area>/<relative path>``, one
directory per area of the instance protocol. Records that point at those files
(database rows, generated text) use the area form ``<area>:<relative path>``;
the installer copies ``data/<area>/`` into the same area of the new instance, so
the two forms name the same file before and after installation.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from management_common.instance_manifest import AREAS

DATA = "data"
# Area folders an exporter may place content in. provider-state is host-bound
# state of the source instance and is never exported.
EXPORT_AREAS = tuple(area for area in AREAS if area != "provider-state")


def check_relative(relative):
    """Return a canonical relative POSIX path, or fail without interpreting it."""
    if not isinstance(relative, str) or any(c in relative for c in "\0\r\n\\"):
        raise ValueError("Area path must be canonical relative text")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or relative == "." or ".." in parts.parts or (relative and parts.as_posix() != relative):
        raise ValueError("Area path must be a canonical relative path")
    return relative


def reference(area, relative=""):
    """The record form of a file in an area: ``area:relative``."""
    if area not in EXPORT_AREAS:
        raise ValueError("Unknown export area: " + str(area))
    return area + ":" + check_relative(relative)


def split_reference(value):
    """Split ``area:relative`` into its parts; reject anything else."""
    area, separator, relative = value.partition(":") if isinstance(value, str) else ("", "", "")
    if not separator or area not in EXPORT_AREAS:
        raise ValueError("Expected an export area reference such as projects:<project>/<path>")
    return area, check_relative(relative)


def target(area, relative=""):
    """The bundle path (``data/<area>/<relative>``) of a file in an area."""
    reference(area, relative)
    return "/".join(part for part in (DATA, area, relative) if part)


def split_target(path):
    """Split a bundle path into (area, relative); the relative part is empty for an area itself."""
    parts = PurePosixPath(str(path)).parts
    if len(parts) < 2 or parts[0] != DATA or parts[1] not in EXPORT_AREAS or ".." in parts:
        raise ValueError("Resolved resource files must target a declared data area")
    return parts[1], PurePosixPath(*parts[2:]).as_posix() if parts[2:] else ""


def target_reference(path):
    """The record form of a bundle path."""
    return reference(*split_target(path))


def reference_target(value):
    """The bundle path of a record-form reference."""
    return target(*split_reference(value))


def physical(value):
    """Host path of a stored location: an area reference resolves in this instance."""
    if isinstance(value, str) and value.partition(":")[0] in AREAS and ":" in value:
        from management_common import instance_manifest
        return instance_manifest.load().resolve(value)
    return Path(value)


def aliases(value):
    """Every spelling of a stored location that text in the export may use."""
    result = [str(value)]
    path = str(physical(value))
    if path not in result:
        result.append(path)
    return result
