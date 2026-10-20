from __future__ import annotations

from types import SimpleNamespace

import pytest

from management_portal import isa_client, resident_handoff


class _ResidentService:
    def __init__(self) -> None:
        self.registrations: list[tuple[dict, str]] = []
        self.deliveries: list[tuple[dict, str]] = []
        self.states: list[dict] = []
        self.events: list[dict] = []
        self.cancelled: list[tuple[str, str, str]] = []

    def ensure_resident_invocation(self, payload: dict, actor: str) -> dict:
        self.registrations.append((payload, actor))
        return {
            "resident_id": "resident_aaaaaaaaaaaa",
            "lineage_id": "lineage_aaaaaaaaaaaa",
        }

    def deliver_resident_input(self, payload: dict, actor: str) -> dict:
        self.deliveries.append((payload, actor))
        return {
            "resident_id": payload["resident_id"],
            "message_id": payload["message"]["message_id"],
        }

    def get_resident_input_state(self, *_args) -> dict:
        return self.states.pop(0)

    def get_resident_conversation_snapshot(self, *_args, **_kwargs) -> dict:
        return {"resident_id": "resident_aaaaaaaaaaaa", "cursor": 0}

    def get_resident_events(self, _resident_id, _actor, after_cursor, *, limit, input_message_id="") -> dict:
        selected = [
            event
            for event in self.events
            if int(event["cursor"]) > int(after_cursor)
        ][:limit]
        return {
            "cursor": int(self.events[-1]["cursor"]) if self.events else int(after_cursor),
            "events": selected,
        }

    def get_resident_message(self, *_args) -> dict:
        return {
            "message_id": "message_reply",
            "role": "agent",
            "content_parts": [{"type": "text", "text": "done"}],
            "created_at": "2026-08-28T00:00:00Z",
        }

    def get_resident_artifact(self, *_args) -> dict:
        raise AssertionError("no artifact expected")

    def cancel_resident_input(self, *args) -> dict:
        self.cancelled.append(tuple(str(item) for item in args))
        return {
            "input_state": {
                "message_id": str(args[2]),
                "state": "cancelled",
            }
        }


class _Hub:
    sequence = 7

    def cursor_has_gap(self, _cursor):
        return False

    def wait(self, cursor, **_kwargs):
        return [SimpleNamespace(
            subject="portal.resident.changed.resident_aaaaaaaaaaaa.conversation",
            payload={
                "event": {
                    "event_type": "resident.input.state.changed",
                    "payload": {
                        "input_state": {
                            "message_id": "message_input",
                            "state": "completed",
                            "reply_message_ids": ["message_reply"],
                        }
                    },
                }
            },
        )], cursor + 1


def _service(monkeypatch):
    service = _ResidentService()
    monkeypatch.setattr(resident_handoff, "get_society_service", lambda: SimpleNamespace(resident=service))

    def call(actor, instruction, arguments, key=""):
        # Business delivery is the DeliverMessage instruction.
        assert instruction == "DeliverMessage" and key
        return service.deliver_resident_input(arguments, actor)

    monkeypatch.setattr(isa_client, "call", call)
    monkeypatch.setattr(resident_handoff, "ensure_notification_listener", lambda: None)
    monkeypatch.setattr(resident_handoff, "get_notification_hub", lambda: _Hub())
    monkeypatch.setattr(
        resident_handoff,
        "ensure_invocation_resident",
        lambda **payload: service.ensure_resident_invocation(
            {**payload, "backend": resident_handoff.provider_name(payload["backend"])},
            payload["actor"],
        ),
    )
    return service


def test_business_invocation_returns_resident_and_message_identity_contracts(monkeypatch, subtests, isolated_portal_case):
    """业务调用保留 Resident 与消息身份并支持附件和等待"""
    with subtests.test(contract='test_business_invocation_returns_resident_and_message_identity'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            service = _service(monkeypatch)
            handle = resident_handoff.submit_invocation(
                actor="CASE-A",
                message="inspect",
                resident_scope="review:42",
                lineage_scope="reviews",
                backend="claude",
                business_ref="review-42",
            )

            assert handle["resident_id"] == "resident_aaaaaaaaaaaa"
            assert handle["lineage_id"] == "lineage_aaaaaaaaaaaa"
            assert handle["submission_state"] == "submitted"
            assert handle["message_id"] == handle["request_id"]
            registration, actor = service.registrations[0]
            assert actor == "CASE-A"
            assert registration["backend"] == "claude"
            delivery, _actor = service.deliveries[0]
            assert delivery["message"]["content_parts"] == [
                {"type": "text", "text": "inspect"}
            ]
            assert "job_id" not in handle
    with subtests.test(contract='test_business_invocation_preserves_image_and_file_content_parts'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            service = _service(monkeypatch)

            resident_handoff.submit_invocation(
                actor="CASE-A",
                message="inspect attachments",
                resident_scope="review:attachments",
                input_media=[
                    {
                        "media_type": "image",
                        "source_type": "local_path",
                        "source": "/tmp/probe.png",
                    },
                    {
                        "type": "file",
                        "media_type": "application/pdf",
                        "source_type": "local_path",
                        "source": "/tmp/probe.pdf",
                    },
                ],
            )

            parts = service.deliveries[0][0]["message"]["content_parts"]
            assert [part["type"] for part in parts] == ["text", "image", "file"]
    with subtests.test(contract='test_business_invocation_rejects_unknown_media_type'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            _service(monkeypatch)

            with pytest.raises(ValueError, match="must be image or file"):
                resident_handoff.submit_invocation(
                    actor="CASE-A",
                    message="inspect attachment",
                    resident_scope="review:invalid-attachment",
                    input_media=[{"media_type": "audio", "source": "/tmp/probe.wav"}],
                )
    with subtests.test(contract='test_business_invocation_waits_on_resident_events'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            service = _service(monkeypatch)
            service.states[:] = [
                {"message_id": "message_input", "state": "running"},
            ]
            service.events[:] = [{
                "cursor": 1,
                "event_type": "resident.input.state.changed",
                "payload": {
                    "input_state": {
                        "message_id": "message_input",
                        "state": "completed",
                        "reply_message_ids": ["message_reply"],
                    }
                },
            }]

            result = resident_handoff.resume_and_wait(
                actor="CASE-A",
                resident_id="resident_aaaaaaaaaaaa",
                message_id="message_input",
            )

            assert result["message"] == "done"
            assert result["message_id"] == "message_input"
            assert "job_id" not in result


def test_business_assignment_receipt_precedes_delivery(monkeypatch, subtests, isolated_portal_case):
    """业务分配回执先于投递且可在投递前取消"""
    for entrypoint in ["submit_invocation", "invoke_and_wait"]:
        for cancel_before_delivery in [False, True]:
            with subtests.test(entrypoint=entrypoint, cancel_before_delivery=cancel_before_delivery), isolated_portal_case() as case_path:
                with monkeypatch.context() as monkeypatch:
                    service = _service(monkeypatch)
                    assigned = []
                    deliveries = []

                    def assignment(identity):
                        assigned.append(identity)
                        if cancel_before_delivery:
                            raise RuntimeError("cancelled before delivery")

                    def deliver(payload, actor):
                        assert assigned
                        deliveries.append(payload)
                        raise ValueError("当前 Resident 正在等待 Workspace Handoff ready")

                    monkeypatch.setattr(service, "deliver_resident_input", deliver)
                    with pytest.raises((ValueError, RuntimeError)):
                        getattr(resident_handoff, entrypoint)(
                            actor="CASE-A", message="inspect", resident_scope="review:42",
                            business_ref="review-42", on_assigned=assignment,
                        )
                    assert [{key: item[key] for key in ("resident_id", "lineage_id")} for item in assigned] == [
                        {"resident_id": "resident_aaaaaaaaaaaa", "lineage_id": "lineage_aaaaaaaaaaaa"}]
                    assert len(deliveries) == (0 if cancel_before_delivery else 1)
