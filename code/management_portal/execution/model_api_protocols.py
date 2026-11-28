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


def _content_blocks(content, *, anthropic=False):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValueError("Model API message content must be text or content blocks")
    parts = []
    for part in content:
        kind = part.get("type")
        if kind in {"text", "input_text", "output_text"} and set(part) == {"type", "text"}:
            parts.append({"type": "text", "text": part["text"]})
        elif kind == "input_image" and set(part) <= {"type", "image_url", "detail"}:
            image = {"url": part["image_url"]}
            if "detail" in part:
                image["detail"] = part["detail"]
            if anthropic:
                if "detail" in image:
                    raise ValueError("Anthropic Messages cannot preserve image detail")
                url = image["url"]
                if url.startswith("data:"):
                    mime, separator, data = url[5:].partition(";base64,")
                    if not separator:
                        raise ValueError("Anthropic Messages requires a base64 image data URL")
                    source = {"type": "base64", "media_type": mime, "data": data}
                else:
                    source = {"type": "url", "url": url}
                parts.append({"type": "image", "source": source})
            else:
                parts.append({"type": "image_url", "image_url": image})
        else:
            raise ValueError(f"Model API cannot preserve content block {kind} with fields {sorted(part)}")
    return parts


def _chat_message(item):
    kind = item.get("type")
    if kind is None:
        return item
    if kind == "message" and set(item) <= {"type", "role", "content"}:
        return {"role": item["role"], "content": _content_blocks(item["content"])}
    if kind == "function_call" and set(item) == {"type", "call_id", "name", "arguments"}:
        return {"role": "assistant", "content": None, "tool_calls": [
            {"type": "function", "id": item["call_id"],
             "function": {"name": item["name"], "arguments": item["arguments"]}}]}
    if kind == "function_call_output" and set(item) == {"type", "call_id", "output"}:
        return {"role": "tool", "tool_call_id": item["call_id"], "content": _content_blocks(item["output"])}
    raise ValueError(f"Chat Completions cannot preserve historical {kind} with fields {sorted(item)}")


def _responses_message(msg):
    # Native Responses items (including encrypted reasoning and compaction)
    # are already valid input. Untyped chat messages may carry Chat image parts.
    if "type" in msg or isinstance(msg["content"], str):
        return msg
    parts = []
    for part in msg["content"]:
        if part.get("type") == "text" and set(part) == {"type", "text"}:
            parts.append({"type": "output_text" if msg["role"] == "assistant" else "input_text",
                          "text": part["text"]})
        elif part.get("type") == "image_url" and set(part) == {"type", "image_url"}:
            image = part["image_url"]
            if set(image) - {"url", "detail"}:
                raise ValueError("Responses cannot preserve Chat image fields")
            parts.append({"type": "input_image", "image_url": image["url"],
                          **({"detail": image["detail"]} if "detail" in image else {})})
        else:
            raise ValueError(f"Responses cannot preserve Chat content block {part.get('type')}")
    return {**msg, "content": parts}


def _anthropic_messages(messages):
    system, conversation = [], []
    for msg in messages:
        if msg.get("type", "message") != "message" or set(msg) - {"role", "content", "type"}:
            raise ValueError("Anthropic Messages cannot preserve this historical item")
        role = msg["role"]
        content = _content_blocks(_responses_message(msg)["content"], anthropic=True)
        if role == "system" and not conversation:
            system.extend([{"type": "text", "text": content}] if isinstance(content, str) else content)
        elif role in {"assistant", "user"}:
            conversation.append({"role": role, "content": content})
        else:
            raise ValueError(f"Anthropic Messages cannot preserve the position or role of {role} input")
    return {"messages": conversation, **({"system": system} if system else {})}


class ChatProtocol:
    name = "openai_compatible_chat"
    settings_view = "chat"
    default_history = True
    timeout_max = 259200
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
        payload = {**parameters, "model": model, "messages": [_chat_message(msg) for msg in messages]}
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
    timeout_max = 360
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


class ResponsesProtocol(ChatProtocol):
    name = "openai_responses"
    wire_protocol = "openai_responses"
    endpoint = "responses"
    parameters = frozenset({"temperature", "top_p", "max_tokens", "max_completion_tokens",
                            "response_format", "reasoning_effort"})

    def validate(self, parameters, *, require_questions=False):
        super().validate(parameters, require_questions=require_questions)
        if set(parameters) - self.parameters:
            raise ValueError(f"Unsupported {self.name} parameters: {sorted(set(parameters) - self.parameters)}")

    def execute(self, registry, item, model, messages, parameters, timeout, retries, cancelled, observe, run):
        from management_portal.model_api_wire import encode
        canonical = {**parameters, "model": model, "tools": [], "messages": []}
        canonical["max_tokens"] = parameters.get("max_completion_tokens", parameters.get("max_tokens"))
        fmt = parameters.get("response_format")
        if fmt:
            canonical["format"] = ({"type": "json_schema", **fmt["json_schema"]}
                                   if fmt.get("type") == "json_schema" else fmt)
        payload = {**encode(canonical, self.wire_protocol), "stream": False}
        if self.wire_protocol == "anthropic_messages":
            payload.update(_anthropic_messages(messages))
        else:
            payload["input"] = [_responses_message(msg) for msg in messages]
        for attempt in range(retries + 1):
            if cancelled():
                raise InterruptedError("execution was cancelled before provider retry")
            try:
                response = run(lambda: registry.request(item["id"], self.endpoint, payload,
                    timeout_seconds=timeout, protocol=self.wire_protocol,
                    observe_transport=lambda report: observe({**report, "attempt_number": attempt + 1}) if not cancelled() else None))
                break
            except Exception as exc:
                if attempt >= retries or not AIProviderRegistry.retryable_error(exc):
                    raise
                time.sleep(min(2.0, 0.25 * (attempt + 1)))
        if self.wire_protocol == "anthropic_messages":
            parts = response.get("content") or []
        else:
            parts = [part for message in response.get("output") or []
                     if message.get("type") == "message" for part in message.get("content") or []]
        content = "\n".join(part.get("text", "") for part in parts if part.get("type") in ("text", "output_text")).strip()
        if not content:
            raise RuntimeError(f"{item['id']} returned an empty completion")
        return ApiResponse(response, content)


class MessagesProtocol(ResponsesProtocol):
    name = "anthropic_messages"
    wire_protocol = "anthropic_messages"
    endpoint = "v1/messages"


PROTOCOLS = {adapter.name: adapter for adapter in (ChatProtocol(), ResponsesProtocol(), MessagesProtocol(), JevProtocol())}


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
