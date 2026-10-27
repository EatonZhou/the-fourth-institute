from __future__ import annotations

import json
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from management_portal import model_api_store
from management_portal.provider_transport_diagnostics import ProviderTransportObservation

# Wire protocols an agent harness speaks to a Model API; each AgentProvider
# names the one its API-key accounts need.
HARNESS_PROTOCOLS = ("openai_chat", "openai_responses", "anthropic_messages")
# Options a registration may declare per protocol; chat follows ``kind``.
DECLARED_PROTOCOL_OPTIONS = {"openai_responses": frozenset(), "anthropic_messages": frozenset({"base_url", "auth"})}
ANTHROPIC_AUTH = ("bearer", "x-api-key")


class AIProviderRegistryError(RuntimeError):
    """A provider configuration cannot be used."""


class AIProviderHTTPError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class AIProviderRegistry:
    """Resolve shared AI provider configuration and perform authenticated calls.

    Registrations live in PostgreSQL (``model_api_store``); the secrets they
    reference stay in their files.
    """

    def _entry(self, item: object) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise AIProviderRegistryError(
                "AI provider registry entries must be mappings"
            )
        provider_id = str(item.get("id") or "").strip()
        if not provider_id:
            raise AIProviderRegistryError(
                "AI provider registry entry requires id"
            )
        return dict(item)

    def entries(self) -> list[dict[str, Any]]:
        """Every registered model API, in registration order."""
        return [entry for entry, _version in model_api_store.apis()]

    def get(self, provider_id: str) -> dict[str, Any]:
        selected = str(provider_id or "").strip()
        matches = [
            item for item in self.entries()
            if str(item.get("id") or "").strip() == selected
        ]
        if matches:
            item = matches[0]
            if self.is_enabled(item):
                return item
        raise ValueError(f"AI provider is not available: {selected}")

    @staticmethod
    def is_enabled(item: dict[str, Any]) -> bool:
        return item.get("enabled") is not False

    @staticmethod
    def supports_model_api(item: dict[str, Any]) -> bool:
        from .execution.model_api_protocols import PROTOCOLS
        return AIProviderRegistry.is_enabled(item) and item.get("kind") in PROTOCOLS

    @staticmethod
    def models(item: dict[str, Any]) -> list[str]:
        return list(dict.fromkeys(
            str(model["id"]).strip() for model in item.get("models") or []
            if isinstance(model, dict) and AIProviderRegistry.is_enabled(model) and str(model.get("id") or "").strip()
        ))

    @staticmethod
    def base_url(item: dict[str, Any], *, protocol: str = "openai") -> str:
        url = str(item.get("base_url") or "").strip().rstrip("/")
        if not url:
            raise ValueError("AI provider base URL is missing")
        if protocol == "anthropic" and not url.endswith("/anthropic"):
            url = url.removesuffix("/v1") + "/anthropic"
        return url

    @staticmethod
    def declares_protocols(item: dict[str, Any]) -> bool:
        return isinstance(item.get("protocols"), dict)

    @staticmethod
    def harness_protocols(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Wire protocols an agent harness can use with this registration.

        Chat follows the direct-call kind; the others are declared. A DeepSeek
        registration that declares nothing keeps its historical Anthropic route.
        """
        chat = item.get("kind") == "openai_compatible_chat"
        protocols: dict[str, dict[str, Any]] = {"openai_chat": {}} if chat else {}
        if AIProviderRegistry.declares_protocols(item):
            protocols.update({str(name): dict(options or {}) for name, options in item["protocols"].items()
                              if name in DECLARED_PROTOCOL_OPTIONS})
        elif chat and item.get("provider") == "deepseek":
            protocols["anthropic_messages"] = {}
        return protocols

    @staticmethod
    def harness_endpoint(item: dict[str, Any], protocol: str) -> tuple[str, str]:
        """The base URL and authentication style a harness uses for ``protocol``."""
        options = AIProviderRegistry.harness_protocols(item).get(protocol)
        if options is None:
            raise ValueError(f"Model API {item.get('id')} does not offer {protocol}")
        if protocol != "anthropic_messages":
            return AIProviderRegistry.base_url(item), "bearer"
        if not AIProviderRegistry.declares_protocols(item):
            return AIProviderRegistry.base_url(item, protocol="anthropic"), "bearer"
        url = str(options.get("base_url") or "").strip().rstrip("/")
        return url or AIProviderRegistry.base_url(item).removesuffix("/v1"), str(options.get("auth") or "bearer")

    @staticmethod
    def harness_models(item: dict[str, Any]) -> list[str]:
        """Declared models, or the default model when none are declared."""
        default = str(item.get("default_model") or "").strip()
        return AIProviderRegistry.models(item) or ([default] if default else [])

    @staticmethod
    def model_entry(item: dict[str, Any], model: str) -> dict[str, Any]:
        return next((entry for entry in item.get("models") or []
                     if isinstance(entry, dict) and entry.get("id") == model), {})

    @staticmethod
    def retryable_error(error: Exception) -> bool:
        if isinstance(error, AIProviderHTTPError):
            return error.status_code == 429 or error.status_code >= 500
        if isinstance(error, urllib.error.HTTPError):
            return error.code == 429 or error.code >= 500
        return isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError))

    @staticmethod
    def secret_path(item: dict[str, Any]) -> str:
        return str(item.get("api_key_file") or "").strip()

    @staticmethod
    def read_secret(path: str) -> str:
        if not str(path or "").strip():
            raise ValueError("AI provider secret path is missing")
        value = Path(path).expanduser().read_text(
            encoding="utf-8"
        ).strip()
        if not value:
            raise ValueError("empty AI provider secret")
        return value

    @staticmethod
    def headers(
        provider: str,
        key: str,
        item: dict[str, Any],
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "TFIPortal/1.0",
        }
        extra = item.get("default_headers")
        if isinstance(extra, dict):
            for header_key, header_value in extra.items():
                value = str(header_value or "").strip()
                if value:
                    headers[str(header_key)] = value
        if provider == "openrouter":
            headers.setdefault("HTTP-Referer", "https://portal.local")
            headers.setdefault("X-OpenRouter-Title", "TFI Portal")
        return headers

    def request(
        self,
        provider_id: str,
        endpoint: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        observe_transport: Callable[[dict], None] | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = self.get(provider_id) if configuration is None else self._entry(configuration)
        if item["id"] != provider_id or not self.is_enabled(item):
            raise ValueError(f"AI provider is not available: {provider_id}")
        base_url = self.base_url(item)
        secret_path = self.secret_path(item)
        if not base_url or not secret_path:
            raise ValueError(
                f"AI provider metadata is incomplete: {provider_id}"
            )
        provider = str(item.get("provider") or "").strip().lower()
        secret = self.read_secret(secret_path)
        headers = self.headers(provider, secret, item)
        headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{base_url}/{endpoint.lstrip('/')}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        timeout = max(1.0, float(timeout_seconds))
        observation = ProviderTransportObservation(request.data, timeout)
        failure = None
        try:
            try:
                tls = {}
                if sys.platform == "win32":
                    # Fresh Windows installations may not yet have populated
                    # roots that browsers retrieve on demand. Retain OS trust
                    # and add the maintained public CA bundle, never skip TLS.
                    import certifi
                    context = ssl.create_default_context()
                    context.load_verify_locations(cafile=certifi.where())
                    tls["context"] = context
                with urllib.request.urlopen(request, timeout=timeout, **tls) as response:
                    observation.headers_received(getattr(response, "status", None))
                    observation.enter("reading_response_body")
                    raw_body = response.read()
                    observation.body_received(raw_body)
                    observation.enter("decoding_response_json")
                    body = raw_body.decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                failure = exc
                observation.headers_received(exc.code)
                body = exc.read().decode("utf-8", errors="replace")[:1200]
                raise AIProviderHTTPError(exc.code, f"{provider_id} returned HTTP {exc.code}: {body}") from exc
            except urllib.error.URLError as exc:
                failure = exc
                raise
            decoded = json.loads(body or "{}")
            if not isinstance(decoded, dict):
                raise RuntimeError(f"{provider_id} returned a non-object response")
            return decoded
        except Exception as exc:
            failure = failure or exc
            raise
        finally:
            observation.finish(failure, observe_transport)



shared_ai_provider_registry = AIProviderRegistry()
