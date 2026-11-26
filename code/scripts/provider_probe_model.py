"""Loopback model responses for exercising native SDKs without a production account."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def event(self, payload, kind=""):
        prefix = f"event: {kind}\n" if kind else ""
        self.wfile.write((prefix + "data: " + json.dumps(payload) + "\n\n").encode())
        self.wfile.flush()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.server.previous_answer and self.server.previous_answer in json.dumps(body):
            self.server.received_previous_answer = True
        if self.server.stall.is_set():
            self.server.request_started.set()
            self.server.release_request.wait(20)
            return
        answer = self.server.answer
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        model = body.get("model", "probe")
        advertised = [t.get("function", t).get("name", "") for t in body.get("tools", [])]
        name = next((n for n in advertised if n.endswith("receipt")), "")
        serialized = json.dumps(body.get("input", body.get("messages", [])))
        complete = all(value in serialized for value in self.server.receipts)
        if complete:
            # Check the exact call id, including when the SDK delivers results out of order.
            results = {}
            for message in body.get("input", body.get("messages", [])):
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "function_call_output":
                    results[message["call_id"]] = message.get("output")
                if message.get("role") == "tool":
                    results[message["tool_call_id"]] = message.get("content")
                content = message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            results[block["tool_use_id"]] = block.get("content")
            self.server.results_matched = all(value in json.dumps(results.get(self.server.call_id + str(i)))
                                              for i, value in enumerate(self.server.receipts))
        calls = [] if not name or complete else [(self.server.call_id + str(idx), {"index": idx, "path": str(self.server.paths[idx])})
                                                  for idx in range(2)]
        if self.path.split("?")[0].endswith("/messages"):
            def emit(kind, **params):
                self.event({"type": kind, **params}, kind)
            emit("message_start", message={"id": "msg_probe", "type": "message", "role": "assistant",
                 "model": model, "content": [], "usage": {"input_tokens": 12, "output_tokens": 0}})
            for i, (identity, arguments) in enumerate(calls):
                emit("content_block_start", index=i, content_block={"type": "tool_use", "id": identity,
                     "name": name, "input": {}})
                emit("content_block_delta", index=i, delta={"type": "input_json_delta",
                     "partial_json": json.dumps(arguments)})
                emit("content_block_stop", index=i)
            if not calls:
                emit("content_block_start", index=0, content_block={"type": "text", "text": ""})
                emit("content_block_delta", index=0, delta={"type": "text_delta", "text": answer})
                emit("content_block_stop", index=0)
            emit("message_delta", delta={"stop_reason": "tool_use" if calls else "end_turn"},
                 usage={"output_tokens": 4})
            emit("message_stop")
        elif self.path.endswith("/responses"):
            def emit(kind, **params):
                self.event({"type": kind, **params}, kind)
            emit("response.created", response={"id": "resp_probe"})
            items = [{"id": "fc_" + identity, "type": "function_call", "call_id": identity, "name": name,
                      "arguments": json.dumps(arguments)} for identity, arguments in calls]
            if not items:
                items.append({"id": "msg_probe", "type": "message", "role": "assistant",
                              "content": [{"type": "output_text", "text": answer}]})
            for item in items:
                emit("response.output_item.done", item=item)
            emit("response.completed", response={"id": "resp_probe", "status": "completed",
                 "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16}})
        else:
            delta = {"role": "assistant", "content": answer}
            if calls:
                delta = {"role": "assistant", "tool_calls": [
                    {"index": i, "id": identity, "type": "function",
                     "function": {"name": name, "arguments": json.dumps(arguments)}}
                    for i, (identity, arguments) in enumerate(calls)]}
            for chunk, finish in [(delta, None), ({}, "tool_calls" if calls else "stop")]:
                self.event({"id": "chat_probe", "model": model, "object": "chat.completion.chunk", "created": 1,
                            "choices": [{"index": 0, "delta": chunk, "finish_reason": finish}],
                            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}})
            self.wfile.write(b"data: [DONE]\n\n")


@contextmanager
def loopback_model():
    with ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler) as server:
        server.stall = threading.Event()
        server.request_started = threading.Event()
        server.release_request = threading.Event()
        server.answer = "probe-" + secrets.token_hex(8)
        server.results_matched = False
        server.previous_answer = ""
        server.received_previous_answer = False
        server.call_id = "call_" + secrets.token_hex(8)
        server.receipts = ["receipt-" + secrets.token_hex(8) for _ in range(2)]
        peer = Path.home() / "authorized-workspace"
        peer.mkdir(exist_ok=True)
        server.paths = [peer / "receipt.txt", Path.home() / "other-workspace.txt"]
        server.paths[0].write_text(server.receipts[0])
        server.paths[1].write_text("unchanged")
        def dispatch(arguments):
            index = arguments["index"]
            path = Path(arguments["path"])
            if not path.is_absolute() or path != server.paths[index]:
                raise ValueError("SDK tool arguments changed")
            if path.parent != peer:
                return {"content": [{"type": "text", "text": server.receipts[1]}], "isError": True}
            value = path.read_text()
            path.write_text("received:" + value)
            return {"content": [{"type": "text", "text": value}], "isError": False}
        server.dispatch = dispatch
        server.url = f"http://127.0.0.1:{server.server_port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()


@contextmanager
def stalled_request(model):
    model.request_started.clear()
    model.release_request.clear()
    model.stall.set()
    try:
        yield
    finally:
        model.stall.clear()
        model.release_request.set()
