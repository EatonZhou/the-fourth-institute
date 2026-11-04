"""Public native speech instructions using the ordinary Execution lifecycle."""
import hashlib
from management_portal.ai_provider_registry import shared_ai_provider_registry
from management_portal.hatg.contracts import invalid, not_found
from .contracts import ExecutionRequest, SessionOptions
from . import speech_protocol, speech_generation_service as speech


def _client():
    from .service import get_execution_service
    return get_execution_service()


def list_speech_providers(inv):
    from . import microsoft_speech
    providers = [microsoft_speech.descriptor()]
    for entry in shared_ai_provider_registry.entries():
        try:
            config = speech_protocol.configuration(entry)
        except ValueError:
            continue
        providers.append({"api_id": entry["id"], "provider": entry["provider"], **config,
                          "entitlement": "not_probed", "max_text_characters": speech_protocol.MAX_TEXT,
                          "output_media_type": "audio/wav"})
    return {"providers": providers}


def generate_speech(inv):
    args = inv.arguments
    try:
        entry = speech_protocol.resolve_entry(shared_ai_provider_registry, args["api_id"])
        speech_protocol.payload(entry, args["text"], args["model"], args["voice"], args.get("style", ""),
                                **{k: args[k] for k in ("rate", "volume", "pitch", "boundary") if k in args})
        execution_id = "execution-" + hashlib.sha256(inv.instruction_id.encode()).hexdigest()[:24]
        request = ExecutionRequest(
            execution_id=execution_id, actor=inv.caller, agent_id=execution_id,
            prompt=args["text"], provider=args["api_id"], service_type="speech_generation", operation="generate",
            session=SessionOptions(mode="stateless"), permission_profile="provider_api",
            runtime_options={k: args[k] for k in ("model", "voice", "style", "rate", "volume", "pitch", "boundary") if k in args},
            metadata={"business_type": "native_speech", "instruction_id": inv.instruction_id},
        ).validate()
        speech.prepare(request)
    except (ValueError, KeyError) as exc:
        raise invalid(str(exc)) from None
    record = speech.read_record(execution_id)
    if record["status"] == "queued":
        _client().submit(request)
    return {"execution_id": execution_id, "status": record["status"]}


def get_speech_generation(inv):
    execution_id = inv.argument("execution_id")
    try:
        record = speech.read_record(execution_id)
    except ValueError:
        raise not_found("Speech generation not found") from None
    if record["status"] == "completed":
        return {"execution_id": execution_id, "status": "completed",
                "receipt": speech.verified_receipt(execution_id, actor=inv.caller)}
    if record["actor"] != inv.caller:
        raise not_found("Speech generation not found")
    if record["status"] == "failed":
        return {"execution_id": execution_id, "status": "failed", "error": record["error"]}
    snapshot = _client().status(execution_id, actor=inv.caller, include_events=False, include_result=False)
    return {"execution_id": execution_id, "status": snapshot.get("status", record["status"]),
            **({"error": "Speech execution did not complete; no audio receipt"}
               if snapshot.get("status") in {"failed", "cancelled"} else {})}


HANDLERS = {"ListSpeechProviders": list_speech_providers, "GenerateSpeech": generate_speech,
            "GetSpeechGeneration": get_speech_generation}
