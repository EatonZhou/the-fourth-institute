"""Local media and attachment navigation share bounded byte-range responses."""
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from management_portal import http_files


@pytest.mark.parametrize("download", [False, True])
@pytest.mark.parametrize("range_value,status,expected", [
    ("", 200, b"0123456789abcdef"),
    ("bytes=2-5", 206, b"2345"),
    ("bytes=-3", 206, b"def"),
    ("bytes=99-", 416, b""),
    ("bytes=0-1,4-5", 416, b""),
])
def test_local_file_ranges(tmp_path, download, range_value, status, expected):
    file = tmp_path / "原生 附件.bin"
    file.write_bytes(b"0123456789abcdef")
    headers = {}
    handler = SimpleNamespace(
        path="/file?download=1" if download else "/file",
        headers={"Range": range_value}, command="GET", wfile=BytesIO(),
        send_response=lambda value: headers.update(status=value),
        send_header=lambda name, value: headers.update({name: value}),
        end_headers=lambda: None,
    )
    if download:
        http_files._serve_download_file(handler, file)
    else:
        http_files._serve_media_file(handler, file, "application/octet-stream")
    assert headers["status"] == status
    assert headers["Accept-Ranges"] == "bytes"
    assert int(headers["Content-Length"]) == len(expected)
    assert handler.wfile.getvalue() == expected
    if status == 206:
        assert headers["Content-Range"].endswith("/16")
    if download:
        assert headers["Cache-Control"] == "no-store"
        if status in {200, 206}:
            assert quote(file.name, safe="") in headers["Content-Disposition"]
