from __future__ import annotations

import json
from io import BytesIO


from management_portal import serve_management_portal as portal
from management_portal import http_request_body


class _ResponseHarness:

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.path = ""
        self.wfile = BytesIO()
        self.status = 0
        self.response_headers: dict[str, str] = {}

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.response_headers[name] = value

    def end_headers(self) -> None:
        return


def test_default_json_body_limit_accepts_payload_larger_than_64_kib() -> None:
    """JSON 请求体的默认上限可接受超过 64 KiB 的负载"""
    body = json.dumps({"content": "x" * 70_000}).encode("utf-8")
    handler = _ResponseHarness()
    handler.headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    handler.rfile = BytesIO(body)

    payload = http_request_body._read_bounded_json_object(handler)

    assert payload == {"content": "x" * 70_000}

