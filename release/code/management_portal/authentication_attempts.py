"""Bounded authentication attempt windows shared by Portal instances."""
from __future__ import annotations

import hashlib
import time

from .identity_store import IdentityState


class AuthenticationAttempts:
    def __init__(self, *, limit: int = 10, window: int = 900, capacity: int = 4096):
        self.limit = limit
        self.window = window
        self.capacity = capacity
        self.state = IdentityState("authentication_attempts")

    @staticmethod
    def _key(stage: str, username: str) -> str:
        return hashlib.sha256(f'{stage}:{username.strip().casefold()}'.encode()).hexdigest()

    def allow(self, stage: str, username: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        key = self._key(stage, username)
        with self.state.transaction():
            records = {k: v for k, v in self.state.read({}).items() if v['expires_at'] > now}
            record = records.get(key)
            if record is None:
                if len(records) >= self.capacity:
                    self.state.replace(records)
                    return False
                record = {'attempts': 0, 'expires_at': now + self.window}
                records[key] = record
            allowed = record['attempts'] < self.limit
            if allowed:
                record['attempts'] += 1
            self.state.replace(records)
            return allowed

    def succeeded(self, stage: str, username: str) -> None:
        with self.state.transaction():
            records = self.state.read({})
            if records.pop(self._key(stage, username), None) is not None:
                self.state.replace(records)
