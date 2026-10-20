"""Explicit conversions at SQL boundaries; database instants are TIMESTAMPTZ.

Numeric wire values require an explicit unit. Empty values mean no instant;
zero remains the epoch unless the owning domain explicitly treats it as absent.
"""
from datetime import datetime, timezone
import math


def instant(value, *, unit="seconds"):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not an absolute time")
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError("Absolute time must be finite")
        if unit not in {"seconds", "milliseconds"}:
            raise ValueError("Unknown timestamp unit")
        result = datetime.fromtimestamp(value / (1000 if unit == "milliseconds" else 1), timezone.utc)
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Absolute time requires an explicit timezone")
    return result.astimezone(timezone.utc)


def epoch_seconds(value):
    return None if value is None else value.timestamp()


def iso_time(value):
    return None if value is None else value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def encode_times(row, fields, *, unit="seconds"):
    return {key: instant(value, unit=unit) if key in fields else value for key, value in row.items()}


def decode_times(row, fields, *, format="iso"):
    def decode(value):
        if value is None:
            return None
        if format == "iso":
            return iso_time(value)
        if format == "seconds":
            return epoch_seconds(value)
        if format == "milliseconds":
            return round(epoch_seconds(value) * 1000)
        raise ValueError("Unknown timestamp wire format")
    return {key: decode(value) if key in fields else value for key, value in dict(row).items()}
