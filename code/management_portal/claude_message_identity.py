"""Stable native prompt identity, shared by delivery and offline reconciliation."""
import uuid

TURN_PROMPT_NAMESPACE = uuid.UUID('5d0d8c43-6f1e-4b8e-9a51-0b7f3a1c2d64')


def turn_prompt_uuid(session_key: str, turn_id: str) -> str:
    return str(uuid.uuid5(TURN_PROMPT_NAMESPACE, f'{session_key}\n{turn_id}'))
