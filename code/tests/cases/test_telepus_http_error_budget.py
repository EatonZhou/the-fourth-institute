"""Telegram HTTP failures retain bounded diagnostics and release the response.

:design: decision_692a484e9bea
:importance: P1
"""
import io
import urllib.error

import pytest

from management_portal.telepus import telegram_client


class ObservedBody(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


@pytest.mark.parametrize('operation', ['download', 'api'])
@pytest.mark.parametrize('text', ['x' * 100000, '🦉' * 2000, '拒绝\n请求', ''])
def test_http_error_body_is_bounded_closed_and_not_replayed(monkeypatch, operation, text):
    body = ObservedBody(text.encode())
    error = urllib.error.HTTPError('https://example.invalid', 403, 'Forbidden', {}, body)
    calls = []

    def denied(request, **kwargs):
        calls.append(request)
        raise error

    monkeypatch.setattr(telegram_client.urllib.request, 'urlopen', denied)
    client = telegram_client.TelegramClient(token='synthetic', upload_interface='')
    with pytest.raises(RuntimeError) as result:
        if operation == 'download':
            client.download_file('test.png')
        else:
            client.get_me()
    assert str(result.value).endswith('HTTP 403: ' + text[:1000])
    assert calls and len(calls) == 1
    assert body.read_sizes and all(0 < size <= 4000 for size in body.read_sizes)
    assert body.closed


def test_successful_download_keeps_its_existing_byte_limit(monkeypatch):
    bodies = [ObservedBody(b'1234'), ObservedBody(b'12345')]
    monkeypatch.setattr(telegram_client.urllib.request, 'urlopen', lambda *a, **kw: bodies.pop(0))
    client = telegram_client.TelegramClient(token='synthetic', upload_interface='')
    assert client.download_file('test.png', max_bytes=4) == b'1234'
    with pytest.raises(RuntimeError, match='exceeds configured'):
        client.download_file('test.png', max_bytes=4)
