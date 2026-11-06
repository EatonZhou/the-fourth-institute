"""Incremental model streams. A premature EOF is an error, never a successful turn."""
from __future__ import annotations

import json
import time
import uuid

from .model_api_wire import CHAT, RESPONSES, MESSAGES, WireError, _opaque


def sse(lines):
    data = []
    for raw in lines:
        if len(raw) > 32 * 1024 * 1024:
            raise WireError("Model stream frame exceeds 32 MiB")
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if not line:
            if data:
                text = "\n".join(data)
                data.clear()
                if text == "[DONE]":
                    return
                yield json.loads(text)
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        raise WireError("Model stream ended inside an SSE frame")


def usage(value, protocol):
    if protocol == CHAT:
        return {"input_tokens": value.get("prompt_tokens", 0), "output_tokens": value.get("completion_tokens", 0),
                "cached_tokens": (value.get("prompt_tokens_details") or {}).get("cached_tokens", 0)}
    if protocol == MESSAGES:
        cached = value.get("cache_read_input_tokens", 0)
        return {"input_tokens": value.get("input_tokens", 0) + cached + value.get("cache_creation_input_tokens", 0),
                "output_tokens": value.get("output_tokens", 0), "cached_tokens": cached}
    return {"input_tokens": value.get("input_tokens", 0), "output_tokens": value.get("output_tokens", 0),
            "cached_tokens": (value.get("input_tokens_details") or {}).get("cached_tokens", 0)}


def decode(events, protocol):
    """Yield (start, block, delta, close, finish) events common to the wire protocols."""
    started, finished = False, False
    opened, closed = {}, set()
    totals, reason = {}, "end_turn"
    chat_tools = {}
    def block(key, value):
        if key in opened:
            return []
        index = len(opened)
        opened[key] = index
        return [("block", {"index": index, "block": value})]
    def delta(key, **value):
        return "delta", {"index": opened[key], **value}
    for event in events:
        kind = event.get("type", "")
        if event.get("error") or kind in ("error", "response.failed"):
            raise WireError("Upstream model stream reported an error")
        if protocol == RESPONSES:
            if kind in ("response.created", "response.in_progress") and not started:
                response = event.get("response") or {}
                yield "start", {"id": response.get("id"), "model": response.get("model")}
                started = True
            key = (event.get("output_index", 0), event.get("content_index", 0))
            if kind == "response.output_item.added":
                item = event["item"]
                if item["type"] == "function_call":
                    yield from block(key, {"type": "tool_use", "id": item["call_id"], "name": item["name"], "arguments": ""})
                    if item.get("arguments"):
                        yield delta(key, arguments=item["arguments"])
                elif item["type"] not in ("message", "reasoning"):
                    raise WireError(f"Unsupported upstream output item: {item['type']}")
            elif kind in ("response.output_text.delta", "response.refusal.delta"):
                yield from block(key, {"type": "text", "text": ""})
                yield delta(key, text=event["delta"])
            elif kind == "response.function_call_arguments.delta":
                if key not in opened:
                    raise WireError("Tool argument delta arrived before its call")
                yield delta(key, arguments=event["delta"])
            elif kind == "response.output_item.done":
                item = event["item"]
                if item["type"] == "reasoning":
                    text = "".join(p.get("text", "") for p in item.get("summary") or [])
                    # Keep opaque state byte for byte, without forging an Anthropic signature.
                    yield from block(key, {"type": "thinking", "thinking": text, "signature": _opaque("responses", item)})
                elif item["type"] == "message":
                    for i, part in enumerate(item.get("content") or []):
                        item_key = (event.get("output_index", 0), i)
                        if item_key not in opened:
                            yield from block(item_key, {"type": "text", "text": part.get("text", part.get("refusal", ""))})
                elif item["type"] == "function_call" and key not in opened:
                    yield from block(key, {"type": "tool_use", "id": item["call_id"], "name": item["name"], "arguments": item["arguments"]})
                for item_key in list(opened):
                    if item_key[0] == key[0] and item_key not in closed:
                        yield "close", {"index": opened[item_key]}
                        closed.add(item_key)
            elif kind in ("response.completed", "response.incomplete"):
                response = event["response"]
                totals = usage(response.get("usage") or {}, protocol)
                reason = "max_tokens" if kind == "response.incomplete" else "tool_use" if any(i.get("type") == "function_call" for i in response.get("output") or []) else "end_turn"
                finished = True
                break
        elif protocol == MESSAGES:
            key = event.get("index", 0)
            if kind == "message_start":
                message = event["message"]
                yield "start", {"id": message.get("id"), "model": message.get("model")}
                totals = usage(message.get("usage") or {}, protocol)
                started = True
            elif kind == "content_block_start":
                part = event["content_block"]
                if part["type"] == "tool_use":
                    part = {"type": "tool_use", "id": part["id"], "name": part["name"],
                            "arguments": json.dumps(part["input"]) if part.get("input") else ""}
                elif part["type"] not in ("text", "thinking", "redacted_thinking"):
                    raise WireError(f"Unsupported upstream content: {part['type']}")
                yield from block(key, dict(part))
            elif kind == "content_block_delta":
                part = event["delta"]
                fields = {"text_delta": "text", "input_json_delta": "arguments", "thinking_delta": "thinking", "signature_delta": "signature"}
                if part["type"] not in fields:
                    raise WireError(f"Unsupported upstream delta: {part['type']}")
                field = fields[part["type"]]
                yield delta(key, **{field: part.get("partial_json" if field == "arguments" else field, "")})
            elif kind == "content_block_stop":
                yield "close", {"index": opened[key]}
                closed.add(key)
            elif kind == "message_delta":
                reason = event.get("delta", {}).get("stop_reason") or reason
                # Anthropic's message_delta usage is cumulative but may only contain output.
                if "output_tokens" in (event.get("usage") or {}):
                    totals["output_tokens"] = event["usage"]["output_tokens"]
            elif kind == "message_stop":
                finished = True
                break
        elif protocol == CHAT:
            if not started:
                yield "start", {"id": event.get("id"), "model": event.get("model")}
                started = True
            if event.get("usage"):
                totals = usage(event["usage"], protocol)
            choices = event.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            part = choice.get("delta") or {}
            if part.get("reasoning_content"):
                key = ("chat_reasoning", 0)
                yield from block(key, {"type": "thinking", "thinking": "", "source_protocol": CHAT})
                yield delta(key, thinking=part["reasoning_content"])
            if part.get("content"):
                key = ("text", 0)
                yield from block(key, {"type": "text", "text": ""})
                yield delta(key, text=part["content"])
            for tool in part.get("tool_calls") or []:
                key = ("tool", tool["index"])
                state = chat_tools.setdefault(key, {"id": "", "name": "", "arguments": ""})
                state["id"] += tool.get("id", "")
                state["name"] += (tool.get("function") or {}).get("name", "")
                arguments = (tool.get("function") or {}).get("arguments", "")
                if key not in opened:
                    state["arguments"] += arguments
                    if not state["id"] or not state["name"]:
                        continue
                    yield from block(key, {"type": "tool_use", **state})
                else:
                    yield delta(key, arguments=arguments)
            if choice.get("finish_reason"):
                reason = {"tool_calls": "tool_use", "length": "max_tokens", "stop": "end_turn", "content_filter": "refusal"}.get(choice["finish_reason"], "end_turn")
                finished = True
            # The usage-only chunk follows finish_reason, before [DONE].
        else:
            raise WireError(f"Unsupported stream protocol: {protocol}")
    if not started or not finished:
        raise WireError("Upstream model stream ended without a completion event")
    for key, index in opened.items():
        if key not in closed:
            yield "close", {"index": index}
    yield "finish", {"stop_reason": reason, "usage": totals}


def json_events(response, protocol):
    """Gateways may return JSON despite stream=true; feed the same decoder."""
    if protocol == RESPONSES:
        yield {"type": "response.created", "response": response}
        for index, item in enumerate(response.get("output") or []):
            yield {"type": "response.output_item.done", "output_index": index, "item": item}
        status = response.get("status")
        if status not in ("completed", "incomplete", None):
            raise WireError("Upstream Responses request did not complete")
        yield {"type": "response.incomplete" if status == "incomplete" else "response.completed", "response": response}
    elif protocol == MESSAGES:
        yield {"type": "message_start", "message": response}
        for index, part in enumerate(response.get("content") or []):
            yield {"type": "content_block_start", "index": index, "content_block": part}
            yield {"type": "content_block_stop", "index": index}
        yield {"type": "message_delta", "delta": {"stop_reason": response.get("stop_reason")}, "usage": response.get("usage", {})}
        yield {"type": "message_stop"}
    else:
        choices = response.get("choices") or []
        yield {**response, "choices": [{**c, "delta": {**c.get("message", {}), "tool_calls": [{**t, "index": i} for i, t in enumerate(c.get("message", {}).get("tool_calls") or [])]}} for c in choices]}


class Writer:
    """Build terminal JSON while emitting real text and tool argument deltas."""
    def __init__(self, protocol, model, tool_map=None):
        self.protocol, self.model = protocol, model
        self.id = ("msg_" if protocol == MESSAGES else "resp_") + uuid.uuid4().hex
        self.blocks, self.tool_map, self.sequence = [], tool_map or {}, 0
        self.result = None

    def _event(self, kind, **values):
        value = {"type": kind, **values}
        if self.protocol == RESPONSES:
            value["sequence_number"] = self.sequence
            self.sequence += 1
        return value

    def _message_block_delta(self, index, field, value):
        kind = {"text": "text_delta", "arguments": "input_json_delta",
                "thinking": "thinking_delta", "signature": "signature_delta"}[field]
        return self._event("content_block_delta", index=index,
                           delta={"type": kind, "partial_json" if field == "arguments" else field: value})

    def _response(self, status="in_progress", totals=None):
        totals = totals or {}
        result = {"id": self.id, "object": "response", "created_at": int(time.time()), "model": self.model,
                  "status": status, "output": [self._item(i, b, status) for i, b in enumerate(self.blocks)],
                  "error": None, "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None}
        if status != "in_progress":
            result["usage"] = {"input_tokens": totals.get("input_tokens", 0), "output_tokens": totals.get("output_tokens", 0),
                               "input_tokens_details": {"cached_tokens": totals.get("cached_tokens", 0)},
                               "output_tokens_details": {"reasoning_tokens": 0},
                               "total_tokens": totals.get("input_tokens", 0) + totals.get("output_tokens", 0)}
        return result

    def _item(self, index, block, status="completed"):
        identity = f"{self.id}_{index}"
        kind = block["type"]
        if kind == "text":
            return {"id": identity, "type": "message", "role": "assistant", "status": status,
                    "phase": "commentary" if any(b["type"] == "tool_use" for b in self.blocks) else "final_answer",
                    "content": [{"type": "output_text", "text": block["text"], "annotations": [], "logprobs": []}]}
        if kind == "tool_use":
            tool = self.tool_map.get(block["name"], {"type": "function", "name": block["name"]})
            item = {"id": identity, "type": "custom_tool_call" if tool["type"] == "custom" else "function_call",
                    "call_id": block["id"], "name": tool["name"], "status": status}
            if tool.get("namespace"):
                item["namespace"] = tool["namespace"]
            if tool["type"] == "custom":
                arguments = block.get("arguments") or "{}"
                item["input"] = json.loads(arguments).get("input", "") if status != "in_progress" else ""
            else:
                item["arguments"] = block.get("arguments", "")
            return item
        state = (_opaque(CHAT, {"reasoning_content": block.get("thinking", "")})
                 if block.get("source_protocol") == CHAT else _opaque("anthropic", block))
        return {"id": identity, "type": "reasoning", "summary": [{"type": "summary_text", "text": block.get("thinking", "")}],
                "encrypted_content": state}

    def take(self, kind, value):
        if kind == "start":
            if self.protocol == MESSAGES:
                yield self._event("message_start", message={"id": self.id, "type": "message", "role": "assistant", "content": [],
                    "model": self.model, "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}})
            else:
                yield self._event("response.created", response=self._response())
                yield self._event("response.in_progress", response=self._response())
        elif kind == "block":
            index, block = value["index"], dict(value["block"])
            if index != len(self.blocks):
                raise WireError("Model content blocks arrived out of order")
            self.blocks.append(block)
            if self.protocol == MESSAGES:
                part = {**block}
                initial = {}
                if part["type"] == "tool_use":
                    initial["arguments"] = part.pop("arguments", "")
                    part["input"] = {}
                elif part["type"] in ("text", "thinking"):
                    field = part["type"]
                    initial[field], part[field] = part.get(field, ""), ""
                    if field == "thinking":
                        initial["signature"] = part.pop("signature", "")
                # Native clients accumulate text and reasoning signatures from
                # deltas, even when the upstream delivered a complete JSON block.
                yield self._event("content_block_start", index=index, content_block=part)
                for field, text in initial.items():
                    if text:
                        yield self._message_block_delta(index, field, text)
            else:
                yield self._event("response.output_item.added", output_index=index, item=self._item(index, block, "in_progress"))
                if block["type"] == "text":
                    yield self._event("response.content_part.added", output_index=index, content_index=0, item_id=f"{self.id}_{index}", part={"type": "output_text", "text": "", "annotations": []})
                    if block["text"]:
                        yield self._event("response.output_text.delta", output_index=index, content_index=0, item_id=f"{self.id}_{index}", delta=block["text"])
        elif kind == "delta":
            index = value["index"]
            block = self.blocks[index]
            for field in ("text", "arguments", "thinking", "signature"):
                if field not in value:
                    continue
                block[field] = block.get(field, "") + value[field]
                if self.protocol == MESSAGES:
                    yield self._message_block_delta(index, field, value[field])
                elif field in ("text", "arguments"):
                    # A custom tool's JSON envelope must be decoded before its raw input is streamed.
                    custom = field == "arguments" and self.tool_map.get(block.get("name"), {}).get("type") == "custom"
                    if not custom:
                        event = "response.function_call_arguments.delta" if field == "arguments" else "response.output_text.delta"
                        yield self._event(event, output_index=index, item_id=f"{self.id}_{index}", **({"content_index": 0} if field == "text" else {}), delta=value[field])
        elif kind == "close":
            index = value["index"]
            block = self.blocks[index]
            if block["type"] == "tool_use":
                block["arguments"] = block.get("arguments") or "{}"
                try:
                    if not isinstance(json.loads(block["arguments"]), dict):
                        raise ValueError("Expected an object")
                except ValueError:
                    raise WireError("Upstream returned invalid tool arguments") from None
            if self.protocol == MESSAGES:
                if block["type"] == "tool_use":
                    try:
                        json.loads(block["arguments"])
                    except (ValueError, KeyError):
                        raise WireError("Upstream returned invalid tool arguments") from None
                yield self._event("content_block_stop", index=index)
            else:
                item = self._item(index, block)
                common = {"output_index": index, "item_id": item["id"]}
                if item["type"] == "function_call":
                    yield self._event("response.function_call_arguments.done", **common, arguments=item["arguments"])
                elif item["type"] == "custom_tool_call":
                    yield self._event("response.custom_tool_call_input.delta", **common, delta=item["input"])
                    yield self._event("response.custom_tool_call_input.done", **common, input=item["input"])
                elif item["type"] == "message":
                    yield self._event("response.output_text.done", **common, content_index=0, text=block["text"])
                    yield self._event("response.content_part.done", **common, content_index=0, part=item["content"][0])
                yield self._event("response.output_item.done", output_index=index, item=item)
        elif kind == "finish":
            totals, reason = value["usage"], value["stop_reason"]
            if self.protocol == MESSAGES:
                content = []
                for block in self.blocks:
                    if block["type"] == "tool_use":
                        content.append({"type": "tool_use", "id": block["id"], "name": block["name"], "input": json.loads(block["arguments"])})
                    else:
                        content.append(block)
                wire_usage = {"input_tokens": max(0, totals.get("input_tokens", 0) - totals.get("cached_tokens", 0)),
                              "output_tokens": totals.get("output_tokens", 0), "cache_read_input_tokens": totals.get("cached_tokens", 0), "cache_creation_input_tokens": 0}
                self.result = {"id": self.id, "type": "message", "role": "assistant", "model": self.model, "content": content,
                               "stop_reason": reason, "stop_sequence": None, "usage": wire_usage}
                yield self._event("message_delta", delta={"stop_reason": reason, "stop_sequence": None}, usage=wire_usage)
                yield self._event("message_stop")
            else:
                status = "incomplete" if reason == "max_tokens" else "completed"
                self.result = self._response(status, totals)
                yield self._event("response." + status, response=self.result)


def frame(value):
    return ("event: " + value["type"] + "\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode()
