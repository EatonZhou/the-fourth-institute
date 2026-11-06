"""Model wire formats, independent of any Agent loop or Portal identity.

The harness owns history and tools. Conversion preserves their order and call
identities; it never executes a tool, starts an Execution, or selects another API.
"""
from __future__ import annotations

import base64
import hashlib
import json

CHAT = "openai_chat"
RESPONSES = "openai_responses"
MESSAGES = "anthropic_messages"
KINDS = {"openai_compatible_chat": CHAT, RESPONSES: RESPONSES, MESSAGES: MESSAGES}


class WireError(ValueError):
    pass


def upstream(entry, target):
    from .ai_provider_registry import AIProviderRegistry
    offered = AIProviderRegistry.harness_protocols(entry)
    if target in offered:
        return target
    if target not in (MESSAGES, RESPONSES):
        raise WireError(f"No adapter for {target}")
    for protocol in dict.fromkeys((KINDS.get(entry.get("kind")), RESPONSES, CHAT, MESSAGES)):
        if protocol in offered:
            return protocol
    raise WireError(f"No model protocol compatible with {target}")


def _opaque(protocol, value):
    return "tfi-" + protocol + ":" + base64.b64encode(json.dumps(value).encode()).decode()


def _restore(protocol, value):
    prefix = "tfi-" + protocol + ":"
    if not isinstance(value, str) or not value.startswith(prefix):
        return None
    try:
        return json.loads(base64.b64decode(value[len(prefix):], validate=True))
    except (ValueError, UnicodeError):
        raise WireError("Invalid adapter reasoning state") from None


def blocks(value, protocol, *, role="user"):
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    result = []
    if not isinstance(value, list):
        raise WireError("Message content must be text or an array")
    for part in value:
        kind = part.get("type")
        if kind in ("text", "input_text", "output_text"):
            result.append({"type": "text", "text": part["text"]})
        elif kind in ("image", "image_url", "input_image"):
            if kind == "image":
                source = part["source"]
                url = (f"data:{source['media_type']};base64,{source['data']}" if source["type"] == "base64"
                       else source["url"] if source["type"] == "url" else None)
            else:
                source = part.get("image_url")
                url = source.get("url") if isinstance(source, dict) else source
            if not isinstance(url, str):
                raise WireError("Only URL and base64 images can be translated")
            result.append({"type": "image", "url": url})
        elif kind == "tool_use":
            result.append({"type": kind, "id": part["id"], "name": part["name"],
                           "arguments": json.dumps(part["input"], ensure_ascii=False)})
        elif kind == "tool_result":
            content = blocks(part.get("content", ""), protocol)
            if part.get("is_error"):
                content.insert(0, {"type": "text", "text": "Tool execution failed:"})
            result.append({"type": kind, "id": part["tool_use_id"], "content": content})
        elif kind in ("thinking", "redacted_thinking"):
            if kind == "thinking" and not part.get("thinking") and not part.get("signature"):
                continue  # An empty block carries neither text nor upstream state.
            native = _restore("responses", part.get("signature"))
            result.append({"type": "reasoning", "responses": native} if native else {**part})
        elif kind == "refusal":
            result.append({"type": "text", "text": part["refusal"]})
        else:
            raise WireError(f"Unsupported {protocol} content block: {kind}")
    return result


def request(payload, protocol):
    """Decode a harness request to ordered messages and common generation controls."""
    result = {"model": payload["model"], "messages": [], "tools": [], "tool_map": {}}
    messages = result["messages"]
    if protocol == MESSAGES:
        if payload.get("system"):
            messages.append({"role": "system", "content": blocks(payload["system"], protocol)})
        for message in payload["messages"]:
            messages.append({"role": message["role"], "content": blocks(message["content"], protocol)})
        for tool in payload.get("tools") or []:
            if tool.get("type", "custom") not in ("custom",):
                raise WireError(f"Server tool {tool.get('type')} cannot be translated")
            result["tools"].append({"name": tool["name"], "description": tool.get("description", ""),
                                    "input_schema": tool["input_schema"]})
        choice = payload.get("tool_choice") or {}
        result["tool_choice"] = {"any": "required", "auto": "auto", "none": "none"}.get(choice.get("type"))
        if choice.get("type") == "tool":
            result["tool_choice"] = {"name": choice["name"]}
        if choice.get("disable_parallel_tool_use") is not None:
            result["parallel_tool_calls"] = not choice["disable_parallel_tool_use"]
        result["max_tokens"] = payload.get("max_tokens")
        result["stop"] = payload.get("stop_sequences")
        # Effort is the model's declared effort, not an invented token budget.
        effort = (payload.get("output_config") or {}).get("effort")
        if effort:
            result["reasoning_effort"] = effort
        result["format"] = (payload.get("output_config") or {}).get("format")
        if (payload.get("thinking") or {}).get("type") == "enabled":
            result["thinking"] = payload["thinking"]
            if not effort:
                budget = int(payload["thinking"].get("budget_tokens") or 0)
                result["reasoning_effort"] = "low" if budget < 4096 else "medium" if budget < 16384 else "high"
    elif protocol == RESPONSES:
        if payload.get("previous_response_id") or payload.get("conversation"):
            raise WireError("Protocol conversion requires complete harness history, not server-side response IDs")
        if payload.get("instructions"):
            messages.append({"role": "system", "content": blocks(payload["instructions"], protocol)})
        items = payload.get("input", [])
        if isinstance(items, str):
            items = [{"role": "user", "content": items}]
        for item in items:
            kind = item.get("type", "message")
            if kind == "message":
                messages.append({"role": item["role"], "content": blocks(item["content"], protocol, role=item["role"])})
            elif kind in ("function_call", "custom_tool_call"):
                name = _tool_name(item["name"], item.get("namespace", ""))
                arguments = item.get("arguments", "") if kind == "function_call" else json.dumps({"input": item["input"]})
                _append(messages, "assistant", {"type": "tool_use", "id": item["call_id"], "name": name, "arguments": arguments})
            elif kind in ("function_call_output", "custom_tool_call_output"):
                _append(messages, "user", {"type": "tool_result", "id": item["call_id"], "content": blocks(item["output"], protocol)})
            elif kind == "reasoning":
                chat_state = _restore(CHAT, item.get("encrypted_content"))
                if chat_state is not None:
                    _append(messages, "assistant", {"type": "chat_reasoning", **chat_state})
                    continue
                native = _restore("anthropic", item.get("encrypted_content"))
                _append(messages, "assistant", native or {"type": "reasoning", "responses": item})
            elif kind == "compaction":
                raise WireError("Native Responses compaction state cannot be used with an Anthropic upstream")
            else:
                raise WireError(f"Unsupported Responses input item: {kind}")
        _tools(result, payload.get("tools") or [])
        choice = payload.get("tool_choice")
        result["tool_choice"] = ({"name": _tool_name(choice["name"], choice.get("namespace", ""))}
                                 if isinstance(choice, dict) and choice.get("type") in ("function", "custom") else choice)
        result["parallel_tool_calls"] = payload.get("parallel_tool_calls")
        result["max_tokens"] = payload.get("max_output_tokens")
        result["reasoning_effort"] = (payload.get("reasoning") or {}).get("effort")
        result["format"] = (payload.get("text") or {}).get("format")
    else:
        raise WireError(f"Unsupported harness request protocol: {protocol}")
    for name in ("temperature", "top_p"):
        if name in payload:
            result[name] = payload[name]
    return result


def _append(messages, role, block):
    if messages and messages[-1]["role"] == role:
        messages[-1]["content"].append(block)
    else:
        messages.append({"role": role, "content": [block]})


def _tool_name(name, namespace=""):
    name = namespace + "__" + name if namespace else name
    return name if len(name) <= 64 else name[:47] + "_" + hashlib.sha256(name.encode()).hexdigest()[:16]


def _tools(result, tools, namespace=""):
    for tool in tools:
        kind = tool.get("type")
        if kind == "namespace":
            _tools(result, tool["tools"], tool["name"])
            continue
        if kind not in ("function", "custom"):
            raise WireError(f"Server tool {kind} cannot be translated; use harness-owned tools")
        name = _tool_name(tool["name"], namespace)
        if name in result["tool_map"]:
            raise WireError(f"Duplicate translated tool name: {name}")
        result["tool_map"][name] = {"name": tool["name"], "type": kind, **({"namespace": namespace} if namespace else {})}
        description = tool.get("description", "")
        schema = tool.get("parameters", {"type": "object", "properties": {}})
        if kind == "custom":
            description += "\nReturn the raw tool input in the input string."
            if tool.get("format"):
                description += "\nInput format: " + json.dumps(tool["format"], ensure_ascii=False)
            schema = {"type": "object", "properties": {"input": {"type": "string"}}, "required": ["input"], "additionalProperties": False}
        result["tools"].append({"name": name, "description": description, "input_schema": schema})


def _content(parts, protocol, role):
    result = []
    for part in parts:
        kind = part["type"]
        if kind == "text":
            result.append({"type": "text" if protocol != RESPONSES else "output_text" if role == "assistant" else "input_text", "text": part["text"]})
        elif kind == "image":
            url = part["url"]
            if protocol == MESSAGES:
                if url.startswith("data:"):
                    media, separator, data = url[5:].partition(";base64,")
                    if not separator:
                        raise WireError("Image data URL must be base64")
                    source = {"type": "base64", "media_type": media, "data": data}
                else:
                    source = {"type": "url", "url": url}
                result.append({"type": "image", "source": source})
            else:
                result.append({"type": "input_image", "image_url": url} if protocol == RESPONSES
                              else {"type": "image_url", "image_url": {"url": url}})
        else:
            raise WireError(f"Unsupported {kind} in {protocol} message content")
    return result


def encode(value, protocol):
    """Encode one stateless model request; history stays with the harness."""
    payload = {"model": value["model"], "stream": True}
    for name in ("temperature", "top_p"):
        if value.get(name) is not None:
            payload[name] = value[name]
    tools = value["tools"]
    choice = value.get("tool_choice")
    if protocol == MESSAGES:
        payload.update(messages=[], max_tokens=value.get("max_tokens") or 16384)
        system = []
        for message in value["messages"]:
            role = message["role"]
            if role in ("system", "developer"):
                system.extend(_content(message["content"], protocol, role))
                continue
            content = []
            for part in message["content"]:
                kind = part["type"]
                if kind == "tool_use":
                    try:
                        arguments = json.loads(part["arguments"])
                    except ValueError:
                        raise WireError("Tool arguments must contain valid JSON") from None
                    content.append({"type": "tool_use", "id": part["id"], "name": part["name"], "input": arguments})
                elif kind == "tool_result":
                    content.append({"type": "tool_result", "tool_use_id": part["id"], "content": _content(part["content"], protocol, "user")})
                elif kind in ("thinking", "redacted_thinking"):
                    content.append(part)
                elif kind == "reasoning":
                    raise WireError("Responses reasoning state belongs to a different upstream")
                else:
                    content.extend(_content([part], protocol, role))
            if payload["messages"] and payload["messages"][-1]["role"] == role:
                payload["messages"][-1]["content"].extend(content)
            else:
                payload["messages"].append({"role": role, "content": content})
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools
        if isinstance(choice, dict):
            payload["tool_choice"] = {"type": "tool", "name": choice["name"]}
        elif choice:
            payload["tool_choice"] = {"type": "any" if choice == "required" else choice}
        if value.get("parallel_tool_calls") is False and tools:
            payload.setdefault("tool_choice", {"type": "auto"})["disable_parallel_tool_use"] = True
        if value.get("reasoning_effort") and value["reasoning_effort"] not in ("none", "minimal"):
            payload["thinking"] = {"type": "adaptive"}
            payload["output_config"] = {"effort": value["reasoning_effort"]}
        if value.get("format") and value["format"].get("type") not in (None, "text"):
            fmt = value["format"]
            if fmt.get("type") != "json_schema":
                raise WireError("Anthropic structured output requires a JSON schema")
            payload.setdefault("output_config", {})["format"] = {"type": "json_schema", "schema": fmt["schema"]}
        if value.get("stop"):
            payload["stop_sequences"] = value["stop"]
        return payload
    items = []
    for message in value["messages"]:
        role = message["role"]
        pending = []
        def flush():
            if pending:
                items.append({"role": role, "content": _content(pending, protocol, role)})
                pending.clear()
        for part in message["content"]:
            kind = part["type"]
            if kind in ("text", "image"):
                pending.append(part)
                continue
            flush()
            if kind == "tool_use":
                if protocol == RESPONSES:
                    items.append({"type": "function_call", "call_id": part["id"], "name": part["name"], "arguments": part["arguments"]})
                else:
                    items.append({"role": "assistant", "content": None, "tool_calls": [{"id": part["id"], "type": "function", "function": {"name": part["name"], "arguments": part["arguments"]}}]})
            elif kind == "tool_result":
                content = _content(part["content"], protocol, "user")
                if protocol == RESPONSES:
                    items.append({"type": "function_call_output", "call_id": part["id"], "output": content})
                else:
                    # Modern Chat endpoints accept multimodal tool results.
                    items.append({"role": "tool", "tool_call_id": part["id"], "content": content})
            elif kind == "reasoning" and protocol == RESPONSES:
                items.append(part["responses"])
            elif kind == "chat_reasoning" and protocol == CHAT:
                items.append({"role": "assistant", "content": None,
                              "reasoning_content": part["reasoning_content"]})
            elif kind in ("thinking", "redacted_thinking"):
                raise WireError("Anthropic thinking state belongs to a different upstream")
            elif kind != "reasoning":
                raise WireError(f"Unsupported {kind} for {protocol}")
        flush()
    if protocol == RESPONSES:
        payload.update(input=items, store=False)
        # Responses function schemas default to strict; harness schemas do not.
        if tools:
            payload["tools"] = [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["input_schema"], "strict": False} for t in tools]
        if choice:
            payload["tool_choice"] = {"type": "function", **choice} if isinstance(choice, dict) else choice
        if value.get("max_tokens"):
            payload["max_output_tokens"] = value["max_tokens"]
        if value.get("reasoning_effort"):
            payload["reasoning"] = {"effort": value["reasoning_effort"]}
            payload["include"] = ["reasoning.encrypted_content"]
        elif tools:
            payload["include"] = ["reasoning.encrypted_content"]
        if value.get("format"):
            payload["text"] = {"format": value["format"]}
        if value.get("stop"):
            raise WireError("Responses does not support stop sequences")
    elif protocol == CHAT:
        # Keep parallel calls in a single assistant message for Chat endpoints.
        merged = []
        for item in items:
            if (merged and merged[-1].get("role") == item.get("role") == "assistant"
                    and "reasoning_content" in merged[-1]):
                previous = merged[-1]
                if item.get("tool_calls"):
                    previous.setdefault("tool_calls", []).extend(item["tool_calls"])
                if item.get("content") is not None:
                    previous["content"] = (previous.get("content") or []) + item["content"]
                if "reasoning_content" in item:
                    previous["reasoning_content"] += item["reasoning_content"]
            elif item.get("tool_calls") and merged and merged[-1].get("tool_calls"):
                merged[-1]["tool_calls"].extend(item["tool_calls"])
            else:
                merged.append(item)
        payload.update(messages=merged, stream_options={"include_usage": True})
        if tools:
            payload["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
        if choice:
            payload["tool_choice"] = {"type": "function", "function": choice} if isinstance(choice, dict) else choice
        for src, dst in (("max_tokens", "max_completion_tokens"), ("reasoning_effort", "reasoning_effort"), ("stop", "stop"), ("format", "response_format")):
            if value.get(src) is not None:
                payload[dst] = value[src]
    else:
        raise WireError(f"Unsupported upstream: {protocol}")
    if value.get("parallel_tool_calls") is not None and tools:
        payload["parallel_tool_calls"] = value["parallel_tool_calls"]
    return payload
