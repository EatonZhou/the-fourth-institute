"""Direct Model API wire protocols, including their configurable capabilities.

Execution owns scheduling, cancellation and result persistence. These adapters
own parameter compatibility, wire requests and decoding; neither runs an agent loop.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time

from management_portal.ai_provider_registry import AIProviderRegistry


@dataclass(frozen=True)
class ApiResponse:
    response: dict
    content: str
    structured: object = None


class ChatProtocol:
    name = "openai_compatible_chat"
    settings_view = "chat"
    default_history = True
    timeout_max = 86400
    parameters = frozenset({"temperature", "top_p", "max_tokens", "max_completion_tokens",
        "stop", "frequency_penalty", "presence_penalty", "seed", "response_format", "thinking", "reasoning_effort"})
    # Only protocol-common controls are inferred. Provider extensions require
    # explicit model metadata in the registry.
    default_controls = ("temperature", "top_p", "max_tokens")

    def validate(self, parameters, *, require_questions=False):
        if "questions" in parameters:
            raise ValueError("questions requires the typesafe_system_one protocol")
        # Resident settings validate their supported keys separately. Other
        # Execution callers may use provider-specific generation parameters.

    def execute(self, registry, item, model, messages, parameters, timeout, retries, cancelled, observe, run):
        payload = {**parameters, "model": model, "messages": messages}
        for attempt in range(retries + 1):
            if cancelled():
                raise InterruptedError("execution was cancelled before provider retry")
            def observed(report):
                if not cancelled():
                    observe({**report, "attempt_number": attempt + 1})
            try:
                response = run(lambda: registry.request(item["id"], "chat/completions", payload,
                    timeout_seconds=timeout, observe_transport=observed))
                break
            except Exception as exc:
                if attempt >= retries or not AIProviderRegistry.retryable_error(exc):
                    raise
                time.sleep(min(2.0, 0.25 * (attempt + 1)))
        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message") if isinstance(choice, dict) else {}
        content = message.get("content") if isinstance(message, dict) else ""
        if isinstance(content, list):
            content = "\n".join(str(part.get("text") or "").strip() for part in content if isinstance(part, dict))
        content = content.strip() if isinstance(content, str) else ""
        if not content:
            raise RuntimeError(f"{item['id']} returned an empty completion")
        return ApiResponse(response, content)


class JevProtocol:
    name = "typesafe_system_one"
    settings_view = "jev"
    default_history = False
    timeout_max = 120
    parameters = frozenset({"questions"})
    default_controls = ()

    def validate(self, parameters, *, require_questions=False):
        from management_portal.jev_provider import validate_questions
        if set(parameters) - self.parameters:
            raise ValueError("Jev accepts questions, not chat generation parameters")
        if "questions" in parameters:
            validate_questions(parameters["questions"])
        elif require_questions:
            raise ValueError("Jev requires backend_options.parameters.questions (choice, score or noul)")

    def execute(self, registry, item, model, messages, parameters, timeout, retries, cancelled, observe, run):
        from management_portal.jev_provider import JevProvider
        response = run(lambda: JevProvider(registry, item["id"]).decide(
            state={"messages": messages}, questions=parameters["questions"], model=model,
            timeout_seconds=timeout, max_retries=retries, configuration=item,
            is_cancelled=cancelled, observe_transport=observe))
        answers = response["answers"]
        return ApiResponse(response, "```json\n" + json.dumps(answers, ensure_ascii=False, indent=2) + "\n```", answers)


PROTOCOLS = {adapter.name: adapter for adapter in (ChatProtocol(), JevProtocol())}


def protocol_adapter(name):
    try:
        return PROTOCOLS[name]
    except KeyError:
        raise ValueError(f"Unsupported Model API protocol: {name}") from None


def protocol_settings(entry):
    adapter = protocol_adapter(entry["kind"])
    controls, efforts = {}, {}
    for model in entry.get("models") or []:
        if not isinstance(model, dict) or not AIProviderRegistry.is_enabled(model):
            continue
        model_id = str(model.get("id") or "")
        controls[model_id] = [key for key in model.get("supported_parameters", adapter.default_controls)
                              if key in adapter.parameters and key != "questions"]
        declared = model.get("reasoning_efforts") or []
        efforts[model_id] = [str(value) for value in declared]
        if efforts[model_id] and "reasoning_effort" in adapter.parameters:
            controls[model_id].append("reasoning_effort")
    return {"view": adapter.settings_view, "default_history": adapter.default_history,
            "history_max_tokens": 8000, "timeout_max": adapter.timeout_max,
            "parameters_by_model": controls, "reasoning_efforts_by_model": efforts}
