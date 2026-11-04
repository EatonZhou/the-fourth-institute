"""Execution-owned native speech attempts and immutable, redacted receipts.

An uncertain upstream attempt is never resubmitted on worker recovery. Consumers
verify against this store, not against provenance JSON written by an author.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json

from management_common.configuration_files import atomic_json
from management_common.file_lock import lock_stream
from management_portal.ai_provider_registry import shared_ai_provider_registry
from .backend_contracts import BackendResult
from .config import execution_state_root
from .contracts import EXECUTION_ID_RE
from . import speech_protocol


def _path(execution_id):
    if not isinstance(execution_id, str) or not EXECUTION_ID_RE.fullmatch(execution_id):
        raise ValueError("Invalid speech execution ID")
    return execution_state_root() / "speech" / execution_id / "receipt.json"


@contextmanager
def _locked(execution_id):
    path = _path(execution_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / "write.lock").open("a+b") as stream, lock_stream(stream):
        yield path


def read_record(execution_id):
    try:
        return json.loads(_path(execution_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("Speech generation not found") from None


def prepare(request):
    # Worker/session bookkeeping can enrich the accepted ExecutionRequest. Only
    # the synthesis intent defines identity; transport metadata is not input.
    intent = {"actor": request.actor, "provider": request.provider, "text": request.prompt,
              "options": {k: request.runtime_options[k] for k in
                          ("model", "voice", "style", "rate", "volume", "pitch") if k in request.runtime_options}}
    digest = hashlib.sha256(json.dumps(intent, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with _locked(request.execution_id) as path:
        if path.exists():
            record = read_record(request.execution_id)
            if record["request_sha256"] != digest or record["actor"] != request.actor:
                raise ValueError("Speech execution identity conflict")
        else:
            atomic_json(path, {"actor": request.actor, "status": "queued", "request_sha256": digest,
                               "execution_id": request.execution_id})


def verified_receipt(execution_id, *, actor):
    """Read authoritative evidence and recheck ACL and bytes through ResourceService."""
    from management_portal.resources.service import ResourceService
    record = read_record(execution_id)
    if record["status"] != "completed":
        raise ValueError("Native speech has no completed receipt")
    receipt = record["receipt"]
    data, _mime, _detail = ResourceService().read_content(receipt["audio"]["address"], actor=actor,
                                                       max_bytes=speech_protocol.MAX_AUDIO)
    if hashlib.sha256(data).hexdigest() != receipt["audio"]["sha256"]:
        raise ValueError("Native audio does not match its generation receipt")
    return receipt


class SpeechGenerationService:
    service_type = "speech_generation"

    def __init__(self, provider_registry=None):
        self.registry = provider_registry or shared_ai_provider_registry

    def execute(self, invocation, context):
        request = invocation.request
        prepare(request)
        with _locked(request.execution_id) as path:
            record = read_record(request.execution_id)
            if record["status"] == "completed":
                return BackendResult(output=record["receipt"], usage=record["receipt"]["usage"])
            if record["status"] != "queued":
                raise RuntimeError(record.get("error") or "Speech delivery unknown; upstream request will not be replayed")
            if context.is_cancelled():
                raise InterruptedError("Speech cancelled before provider call")
            options = request.runtime_options
            entry = speech_protocol.resolve_entry(self.registry, request.provider)
            body = speech_protocol.payload(entry, request.prompt, options.get("model"), options.get("voice"),
                                           options.get("style", ""),
                                           **{k: options[k] for k in ("rate", "volume", "pitch") if k in options})
            stamp = datetime.now(timezone.utc).isoformat()
            record.update(status="dispatched", started_at=stamp)
            atomic_json(path, record)  # Durably forbid duplicate charge before crossing the network.
            stage = "provider_call"
            try:
                from . import microsoft_speech
                if request.provider == microsoft_speech.API_ID:
                    data, observed = microsoft_speech.generate(request.prompt, **options)
                else:
                    response = self.registry.request_speech(request.provider, body)
                    data, observed = speech_protocol.decode_response(response, options["model"])
                stage = "publish_audio"
                from management_portal.resources.isa import publish_generated_file
                media_type = observed["media_type"]
                audio = publish_generated_file(owner_id=request.execution_id, actor=request.actor,
                                               file_name="speech.mp3" if media_type == "audio/mpeg" else "speech.wav",
                                               media_type=media_type, data=data)
                receipt = {"schema": "tfi.native-speech-receipt.v1", "execution_id": request.execution_id,
                           "api_id": request.provider, "provider": entry["provider"], "model": options["model"],
                           "protocol": entry["speech"]["protocol"], "voice": options["voice"], "audio": audio,
                           "text_sha256": hashlib.sha256(request.prompt.encode()).hexdigest(),
                           "started_at": stamp, "completed_at": datetime.now(timezone.utc).isoformat(), **observed}
                record.update(status="completed", receipt=receipt)
                atomic_json(path, record)
            except Exception as exc:
                from management_portal.ai_provider_registry import AIProviderHTTPError
                # No upstream body, key, transcript or exception repr in public evidence.
                kind = type(exc).__name__
                safe_kind = kind if kind in {"TimeoutError", "TypeError", "ValueError", "RuntimeError",
                    "ModuleNotFoundError", "ImportError", "PermissionError", "ClientConnectorError",
                    "ClientConnectorCertificateError", "WSServerHandshakeError", "NoAudioReceived",
                    "UnexpectedResponse", "UnknownResponse", "MP3StreamInfoError"} else "provider_failure"
                error = str(exc) if isinstance(exc, AIProviderHTTPError) else f"Speech {stage}: {safe_kind}; no automatic retry"
                record.update(status="failed", error=error)
                atomic_json(path, record)
                raise RuntimeError(error) from None
            context.emit("provider_output", {"text": "Native speech audio generated", "final": True})
            return BackendResult(output=receipt, usage=receipt["usage"])

    def interrupt(self, **kwargs):
        # The bounded HTTP request may still finish/bill; never claim upstream cancellation.
        return False
