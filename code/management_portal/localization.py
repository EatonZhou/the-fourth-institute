"""Presentation-only localization shared by Portal HTTP and browser catalogs."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Mapping

LANGUAGES = ("original", "auto", "zh-CN", "en")
CATALOG_PATH = Path(__file__).with_name("static") / "portal_translations.json"
# A feature's own text lives in translations/<feature key>.json; a package that
# leaves the feature out leaves the file out (portal_features.py).
FRAGMENTS_DIRECTORY = Path(__file__).with_name("translations")


def normalize_language(value: object) -> str:
    return value if isinstance(value, str) and value in LANGUAGES else "auto"


def preferred_locale(accept_language: str, *, default: str = "zh-CN") -> str:
    choices = []
    for index, item in enumerate(str(accept_language or "").split(",")):
        language, *parameters = item.strip().lower().split(";")
        quality = 1.0
        for parameter in parameters:
            if parameter.strip().startswith("q="):
                try:
                    quality = float(parameter.strip()[2:])
                except ValueError:
                    quality = 0.0
        if not 0 < quality <= 1:
            continue
        if language == "zh" or language.startswith("zh-"):
            choices.append((-quality, index, "zh-CN"))
        elif language == "en" or language.startswith("en-"):
            choices.append((-quality, index, "en"))
    return min(choices)[2] if choices else default


def request_locale(headers: Mapping, *, language: object = None, default: str = "en") -> str:
    explicit = headers.get("X-Portal-Language", "")
    if explicit in {"original", "zh-CN", "en"}:
        return explicit
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("Cookie", ""))
    except Exception:
        pass
    selected = normalize_language(language if language is not None else (
        cookie["portal_language"].value if "portal_language" in cookie else "auto"))
    if selected != "auto":
        return selected
    # Machine clients without a locale retain English protocol diagnostics.
    return preferred_locale(headers.get("Accept-Language", ""), default=default)


def catalog_paths() -> list[Path]:
    """The core catalog and the catalogs of the features in this package."""
    from .portal_features import FEATURES
    fragments = (FRAGMENTS_DIRECTORY / f"{feature.key}.json" for feature in FEATURES)
    return [CATALOG_PATH, *(path for path in fragments if path.is_file())]


def merge_catalogs(paths) -> tuple[dict[str, dict[str, str]], list[tuple[Path, str]]]:
    """The merged catalog, and the ``(path, source)`` a later catalog translates differently."""
    merged, conflicts = {}, []
    for path in paths:
        for source, translations in json.loads(path.read_text(encoding="utf-8")).items():
            if merged.setdefault(source, translations) != translations:
                conflicts.append((path, source))
    return merged, conflicts


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict[str, str]]:
    # The browser catalog build rejects conflicts; here the first catalog wins.
    return merge_catalogs(catalog_paths())[0]


@lru_cache(maxsize=1)
def message_patterns():
    patterns = []
    for source in catalog():
        if not re.search(r"\{p\d+\}", source):
            continue
        parts = re.split(r"(\{p\d+\})", source)
        fields = [part[1:-1] for part in parts if re.fullmatch(r"\{p\d+\}", part)]
        if len(set(fields)) != len(fields):
            continue
        pattern = "".join(f"(?P<{part[1:-1]}>.*?)" if re.fullmatch(r"\{p\d+\}", part) else re.escape(part) for part in parts)
        literal_length = sum(len(part) for part in parts if not re.fullmatch(r"\{p\d+\}", part))
        if literal_length >= 8:
            patterns.append((literal_length, source, re.compile(pattern, re.DOTALL)))
    return sorted(patterns, key=lambda item: item[0], reverse=True)


def translate_message(message: str, locale: str) -> str:
    if locale == "original":
        return message
    entry = catalog().get(message)
    if entry:
        return entry.get(locale, message)
    for _length, source, pattern in message_patterns():
        match = pattern.fullmatch(message)
        if match:
            translated = catalog()[source].get(locale, source)
            return re.sub(r"\{(p\d+)\}", lambda field: match.groupdict().get(field[1], field[0]), translated)
    return message


def localize_error(payload: object, locale: str) -> object:
    if not isinstance(payload, dict):
        return payload
    result = dict(payload)
    if isinstance(result.get("error"), dict):
        result["error"] = localize_error(result["error"], locale)
    for name in ("error", "message"):
        if isinstance(result.get(name), str):
            result[name] = translate_message(result[name], locale)
    return result


class PortalLocaleMiddleware:
    """Localize error display fields, preserving structured instruction receipts."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {key.decode("latin-1").title(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        # Header names are case insensitive, including X-Portal-Language.
        from email.message import Message
        request_headers = Message()
        for key, value in headers.items():
            request_headers[key] = value
        locale = request_locale(request_headers)
        if locale == "original":
            return await self.app(scope, receive, send)
        start = None
        chunks = []

        async def localized_send(event):
            nonlocal start
            if event["type"] == "http.response.start":
                response_headers = dict(event.get("headers", []))
                if event["status"] >= 400 and b"application/json" in response_headers.get(b"content-type", b""):
                    start = event
                    return
            if event["type"] == "http.response.body" and start is not None:
                chunks.append(event.get("body", b""))
                if event.get("more_body"):
                    return
                raw = b"".join(chunks)
                try:
                    payload = json.loads(raw)
                    raw = json.dumps(localize_error(payload, locale), ensure_ascii=False).encode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    pass
                output = [(key, value) for key, value in start.get("headers", []) if key not in {b"content-length", b"content-language", b"vary"}]
                vary = dict(start.get("headers", [])).get(b"vary", b"")
                output.extend([(b"content-length", str(len(raw)).encode()), (b"content-language", locale.encode()), (b"vary", b", ".join(filter(None, [vary, b"Accept-Language, Cookie, X-Portal-Language"])) )])
                await send({**start, "headers": output})
                await send({**event, "body": raw})
                start = None
                return
            await send(event)
        await self.app(scope, receive, localized_send)
