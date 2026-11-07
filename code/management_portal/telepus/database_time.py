"""Telepus SQL instants are TIMESTAMPTZ; service payloads use UTC ISO text."""
from datetime import timezone

FIELDS = frozenset({"created_at", "updated_at", "expires_at", "acquired_at",
                    "renewed_at", "rotated_at", "started_at", "finished_at",
                    "delivered_at", "processed_at"})


def iso_time(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') if value is not None else ''


def decode(row):
    return {key: iso_time(value) if key in FIELDS else value for key, value in dict(row).items()}
