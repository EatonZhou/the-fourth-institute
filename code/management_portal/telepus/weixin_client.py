"""iLink HTTP and CDN protocol; credentials never appear in exceptions.

Protocol reference: Tencent/openclaw-weixin 3.0.0 (MIT), docs/protocol_zh_CN.md.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .weixin_http import HTTP

API = "https://ilinkai.weixin.qq.com"
CDN = "https://novac2c.cdn.weixin.qq.com/c2c"
PLUGIN_VERSION = "3.0.0"
MAX_MEDIA_BYTES = 50 * 1024 * 1024


class WeixinError(RuntimeError):
    def __init__(self, code, operation):
        self.code = code
        super().__init__(f"iLink {operation}: {code}")


def trusted_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or not host.endswith(".weixin.qq.com")
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise WeixinError("invalid_host", "address")
    return value.rstrip("/")


def crypt(data: bytes, key: bytes, *, decrypt=False) -> bytes:
    if len(key) != 16:
        raise WeixinError("invalid_key", "media")
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    if decrypt:
        worker = cipher.decryptor()
        padded = worker.update(data) + worker.finalize()
        unpad = padding.PKCS7(128).unpadder()
        return unpad.update(padded) + unpad.finalize()
    pad = padding.PKCS7(128).padder()
    worker = cipher.encryptor()
    return worker.update(pad.update(data) + pad.finalize()) + worker.finalize()


class WeixinClient:
    def __init__(self, account=None, *, transport=None):
        self.account = account or {}
        self.base = trusted_url(self.account.get("baseurl") or API)
        self.transport = transport or self._http
        self._default_transport = transport is None
        self.last_http_timing = {}

    @staticmethod
    def _http(url, method, headers, body, timeout):
        def fallback(url, method, headers, body, timeout):
            with urlopen(Request(url, data=body, headers=headers, method=method), timeout=timeout) as response:
                return response.read(MAX_MEDIA_BYTES + 1), dict(response.headers)
        try:
            data, response_headers = HTTP.request(url, method, headers, body, timeout,
                                                 limit=MAX_MEDIA_BYTES, fallback=fallback)
            if len(data) > MAX_MEDIA_BYTES:
                raise WeixinError('too_large', 'http')
            return data, response_headers
        except WeixinError:
            raise
        except Exception:
            # urllib exceptions may contain the URL, QR value or auth headers.
            raise WeixinError("network", "http") from None

    def request(self, operation, payload=None, *, query=None, timeout=40, auth=True, base=None):
        headers = {"iLink-App-Id": "bot", "iLink-App-ClientVersion": str(3 << 16)}
        method = "GET" if operation == "get_qrcode_status" else "POST"
        body = None
        if method == "POST":
            headers.update({"Content-Type": "application/json", "AuthorizationType": "ilink_bot_token",
                            "X-WECHAT-UIN": base64.b64encode(str(secrets.randbits(32)).encode()).decode()})
            payload = dict(payload or {})
            if auth:
                VERSION = json.loads((Path(__file__).resolve().parents[3] / 'RELEASE.json').read_text())['version']
                payload["base_info"] = {"channel_version": f"{PLUGIN_VERSION}/tfi-telepus-{VERSION}",
                                        "bot_agent": f"TFI-Telepus/{VERSION}"}
                headers["Authorization"] = "Bearer " + self.account["bot_token"]
            body = json.dumps(payload).encode()
        url = trusted_url(base or self.base) + "/ilink/bot/" + operation
        if query:
            url += "?" + urlencode(query)
        raw, _ = self.transport(url, method, headers, body, timeout)
        if self._default_transport:
            self.last_http_timing = dict(getattr(HTTP.local, 'timing', {}))
        try:
            result = json.loads(raw)
            codes = [int(result.get(name) or 0) for name in ("ret", "errcode")]
        except (ValueError, TypeError, AttributeError):
            raise WeixinError("invalid_response", operation) from None
        if any(codes):
            raise WeixinError(-14 if -14 in codes else next(c for c in codes if c), operation)
        return result

    def send(self, items, context_token, *, client_id):
        return self.request("sendmessage", {"msg": {"from_user_id": "", "to_user_id": self.account["ilink_user_id"],
                            "client_id": client_id, "message_type": 2, "message_state": 2,
                            "item_list": items, "context_token": context_token}})

    def download(self, item):
        media = item.get("media") or {}
        url = media.get("full_url") or CDN + "/download?" + urlencode({"encrypted_query_param": media.get("encrypt_query_param", "")})
        raw, _ = self.transport(trusted_url(url), "GET", {}, None, 60)
        if item.get("aeskey"):
            key = bytes.fromhex(item["aeskey"])
        else:
            key = base64.b64decode(media.get("aes_key") or "")
            if len(key) == 32:
                key = bytes.fromhex(key.decode("ascii"))
        return crypt(raw, key, decrypt=True) if key else raw

    def upload(self, data, filename, content_type):
        if len(data) > MAX_MEDIA_BYTES:
            raise WeixinError("too_large", "upload")
        key, filekey = secrets.token_bytes(16), secrets.token_hex(16)
        encrypted = crypt(data, key)
        kind = 1 if content_type.startswith("image/") else 2 if content_type.startswith("video/") else 3
        result = self.request("getuploadurl", {"filekey": filekey, "media_type": kind,
            "to_user_id": self.account["ilink_user_id"], "rawsize": len(data),
            "rawfilemd5": hashlib.md5(data).hexdigest(), "filesize": len(encrypted),
            "no_need_thumb": True, "aeskey": key.hex()})
        url = result.get("upload_full_url") or CDN + "/upload?" + urlencode({"encrypted_query_param": result.get("upload_param", ""), "filekey": filekey})
        _, headers = self.transport(trusted_url(url), "POST", {"Content-Type": "application/octet-stream"}, encrypted, 60)
        parameter = {k.lower(): v for k, v in headers.items()}.get("x-encrypted-param")
        if not parameter:
            raise WeixinError("missing_upload_reference", "upload")
        media = {"encrypt_query_param": parameter, "aes_key": base64.b64encode(key.hex().encode()).decode(), "encrypt_type": 1}
        if kind == 1:
            return {"type": 2, "image_item": {"media": media, "mid_size": len(encrypted)}}
        if kind == 2:
            return {"type": 5, "video_item": {"media": media, "video_size": len(encrypted)}}
        return {"type": 4, "file_item": {"media": media, "file_name": filename, "len": str(len(data))}}
