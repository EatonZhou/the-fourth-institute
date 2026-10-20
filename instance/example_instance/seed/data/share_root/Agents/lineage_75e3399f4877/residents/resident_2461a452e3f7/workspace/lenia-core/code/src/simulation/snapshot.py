"""Atomic snapshots of named research state arrays, without model dependencies.

The caller owns state semantics and must include every persistent field, rule,
protocol cursor and RNG state needed to continue its particular experiment.
This module preserves numeric fields; it does not serialize an autograd graph.
"""
from dataclasses import dataclass
import json
import numbers
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping

import numpy as np

SNAPSHOT_SCHEMA = "lenia.state-snapshot.v1"
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
_MANIFEST = "manifest_json"


@dataclass(frozen=True)
class StateSnapshot:
    fields: dict[str, np.ndarray]
    step: int
    metadata: dict


def _step(value):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, numbers.Integral) or value < 0:
        raise ValueError("step must be a nonnegative integer")
    return int(value)


def _field(name, value):
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("field names must match [A-Za-z][A-Za-z0-9_]*")
    if not isinstance(value, np.ndarray):
        raise TypeError(f"field {name!r} must be a NumPy array; convert tensors explicitly")
    if value.dtype.kind not in "buifc":
        raise ValueError(f"field {name!r} must have a numeric or boolean dtype")
    if not np.isfinite(value).all():
        raise ValueError(f"field {name!r} contains nonfinite values")
    return value


def save_snapshot(path, fields: Mapping[str, np.ndarray], *, step, metadata):
    """Save a complete, self-contained state bundle by same-directory replace.

    Shapes, dtypes and values are preserved without coercion. Fields may have
    different shapes/ranges, e.g. body[B,Y,X], memory[B,C,Y,X], trace[Y,X].
    Metadata must be JSON-compatible. Pause state updates before calling.
    Existing single-body simulation checkpoints remain a separate format.
    """
    step = _step(step)
    if not isinstance(fields, Mapping) or not fields:
        raise ValueError("fields must be a nonempty mapping")
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")
    # Validate before touching an existing checkpoint. Copying also detaches
    # returned values from mutable views; the caller must prevent concurrent
    # state updates while this sequential snapshot is being captured.
    arrays = {name: _field(name, value).copy() for name, value in fields.items()}
    manifest = {
        "schema": SNAPSHOT_SCHEMA, "step": step, "metadata": metadata,
        "fields": {name: {"shape": list(value.shape), "dtype": value.dtype.str}
                   for name, value in arrays.items()},
    }
    serialized = json.dumps(manifest, ensure_ascii=False, allow_nan=False)
    payload = {f"field__{name}": value for name, value in arrays.items()}
    payload[_MANIFEST] = np.asarray(serialized)
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{path.name}.", suffix=".tmp",
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            np.savez(handle, **payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_snapshot(path, *, required_fields=()):
    """Load numeric state, requiring fields that the caller's model depends on.

    This validates the file format and array descriptors, not domain-specific
    constraints. The caller must validate rule/protocol metadata before resume.
    Returned arrays are independent, writable copies, with no pickle loading.
    """
    if isinstance(required_fields, str):
        raise TypeError("required_fields must be a sequence of field names")
    required = set(required_fields)
    if any(not isinstance(name, str) or not _NAME.fullmatch(name) for name in required):
        raise ValueError("required_fields contains an invalid field name")
    archive = np.load(path, allow_pickle=False)
    if not isinstance(archive, np.lib.npyio.NpzFile):
        raise ValueError("not a state snapshot archive")
    with archive:
        if _MANIFEST not in archive.files:
            raise ValueError("not a state snapshot")
        raw = archive[_MANIFEST]
        if raw.shape != () or raw.dtype.kind != "U":
            raise ValueError("snapshot manifest must be a Unicode scalar")
        def invalid_constant(value):
            raise ValueError(f"nonfinite JSON constant: {value}")
        manifest = json.loads(str(raw.item()), parse_constant=invalid_constant)
        if not isinstance(manifest, dict) or manifest.get("schema") != SNAPSHOT_SCHEMA:
            raise ValueError("unsupported snapshot schema")
        step = _step(manifest.get("step"))
        metadata, descriptors = manifest.get("metadata"), manifest.get("fields")
        if not isinstance(metadata, dict) or not isinstance(descriptors, dict) or not descriptors:
            raise ValueError("snapshot needs metadata and a nonempty field manifest")
        # JSON exponent overflow (e.g. 1e999) does not go through parse_constant.
        json.dumps(metadata, allow_nan=False)
        missing = required - descriptors.keys()
        if missing:
            raise ValueError(f"required state fields missing: {sorted(missing)}")
        expected = {_MANIFEST, *(f"field__{name}" for name in descriptors)}
        if set(archive.files) != expected or len(archive.files) != len(expected):
            raise ValueError("archive fields do not match the snapshot manifest")
        fields = {}
        for name, descriptor in descriptors.items():
            value = _field(name, archive[f"field__{name}"])
            if (not isinstance(descriptor, dict)
                    or descriptor.get("shape") != list(value.shape)
                    or descriptor.get("dtype") != value.dtype.str):
                raise ValueError(f"field {name!r} shape/dtype does not match its manifest")
            fields[name] = value.copy()
    return StateSnapshot(fields, step, metadata)
