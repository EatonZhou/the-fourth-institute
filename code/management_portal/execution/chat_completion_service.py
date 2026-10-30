"""The ``chat_completion`` execution service: one stateless Model API request.

The request's messages, with Portal's and the business system prompt, go to
the Model API the request names, through its wire protocol's adapter
(``model_api_protocols``); a stop ends the wait at once.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from management_portal.ai_provider_registry import AIProviderRegistry, shared_ai_provider_registry

from .backend_contracts import BackendContext, BackendInvocation, BackendResult


def _until_cancelled(call: Callable[[], Any], cancelled: Callable[[], bool]) -> Any:
    """Wait for one blocking Provider request; a stop ends the wait at once.

    The request runs on in its own daemon thread until its response or its own
    timeout, and what it returns after the stop is dropped: the Provider may
    still bill that response, but the Resident stops now.
    """

    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["response"] = call()
        except BaseException as exc:  # noqa: BLE001 - handed to the waiting caller
            outcome["error"] = exc

    worker = threading.Thread(target=run, name="chat-completion-request", daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(0.25)
        if worker.is_alive() and cancelled():
            raise InterruptedError("execution was cancelled during provider call")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["response"]


class ChatCompletionService:
    service_type = "chat_completion"

    def __init__(
        self,
        provider_registry: AIProviderRegistry | None = None,
    ) -> None:
        self.provider_registry = (
            provider_registry or shared_ai_provider_registry
        )

    def execute(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
    ) -> BackendResult:
        from .resident_capability_assembly import prepare
        invocation = prepare(invocation, context)
        transport: dict[str, Any] = {}
        try:
            return self._execute(invocation, context, transport)
        except Exception as exc:
            if transport.get("schema"):
                existing = getattr(exc, "error_details", {})
                exc.error_details = {**(existing if isinstance(existing, dict) else {}), "provider_transport": transport}
            raise

    def _execute(
        self, invocation: BackendInvocation, context: BackendContext, transport: dict[str, Any],
    ) -> BackendResult:
        request = invocation.request
        if context.is_cancelled():
            raise InterruptedError("execution was cancelled before provider call")
        options = request.runtime_options
        provider_id = request.provider
        item = self.provider_registry.get(provider_id)
        raw_messages = options.get("messages")
        messages = [
            dict(message)
            for message in raw_messages
            if isinstance(message, dict)
        ] if isinstance(raw_messages, list) else []
        if invocation.attach_system_context:
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": invocation.system_context,
                },
            )
        business_system_prompt = str(options.get("system_prompt") or "").strip()
        if business_system_prompt:
            insert_at = 1 if invocation.attach_system_context else 0
            messages.insert(
                insert_at,
                {"role": "system", "content": business_system_prompt},
            )
        if messages and messages[-1].get("role") == "user":
            messages[-1]["content"] = invocation.user_prompt
        else:
            messages.append({"role": "user", "content": invocation.user_prompt})
        parameters = (
            dict(options.get("parameters"))
            if isinstance(options.get("parameters"), dict)
            else {}
        )
        from .model_api_protocols import protocol_adapter
        adapter = protocol_adapter(str(item.get("kind") or "openai_compatible_chat"))
        adapter.validate(parameters, require_questions=True)
        model = str(options.get("model") or item.get("default_model") or "").strip()
        if not model:
            raise ValueError(f"LLM provider model is missing: {provider_id}")
        if options.get("context_history"):
            context.emit("context_history", options["context_history"])
        result = adapter.execute(
            self.provider_registry, {**item, "id": provider_id}, model, messages, parameters,
            float(options.get("timeout_seconds") or 360), max(0, int(options.get("max_retries") or 0)),
            context.is_cancelled, transport.update, lambda call: _until_cancelled(call, context.is_cancelled),
        )
        return self._result(context, provider_id, model, result.response, result.content, transport,
                            structured=result.structured)

    def _result(self, context, provider_id, model, response, content, transport, *, structured=None):
        usage = (
            dict(response.get("usage"))
            if isinstance(response.get("usage"), dict)
            else {}
        )
        usage.update(
            {
                "api_id": provider_id,
                "model": str(model),
            }
        )
        context.emit(
            "provider_output",
            {"text": content, "final": True,
             "request_timing": {key: transport.get(key) for key in ("started_at", "finished_at")}},
        )
        return BackendResult(
            output={
                "message": content,
                "service_type": self.service_type,
                "provider": provider_id,
                "response": response,
                **({"structured": structured} if structured is not None else {}),
                **({"provider_transport": transport} if transport.get("schema") else {}),
            },
            usage=usage,
        )

    def interrupt(
        self,
        *,
        provider: str,
        backend_session_id: str,
        turn_id: str,
        backend_route_id: str,
        runtime_options: dict[str, Any],
    ) -> bool:
        return False
