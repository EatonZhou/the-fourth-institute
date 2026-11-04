"""Native Provider speech: explicit entitlement, durable attempts, authenticated audio.

:module: execution.providers
:importance: P1
"""
import base64
import hashlib
import io
import json
from types import SimpleNamespace
import urllib.error
import wave

import pytest

from management_portal.ai_provider_registry import AIProviderRegistry, AIProviderHTTPError
from management_portal.execution import speech_protocol as protocol, speech_generation_service as speech, speech_isa
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.hatg.contracts import InstructionError


MODEL = "gemini-3.8-flash-tts"
ENTRY = {"id": "gemini", "provider": "google", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
         "api_key_file": "/unused/secret", "speech": {"protocol": protocol.PROTOCOL, "models": [MODEL], "voices": ["Kore"]}}


def wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b"\x01\x00" * 2400)
    return output.getvalue()


def answer():
    return {"id": "provider-request-1", "status": "completed", "model": MODEL,
            "steps": [{"type": "model_output", "content": [{"type": "audio", "data": base64.b64encode(wav()).decode()}]}],
            "usage": {"total_tokens": 9, "debug": "do not publish"}}


def request():
    return ExecutionRequest.new(actor="alice", prompt="你好，世界。", service_type="speech_generation", provider="gemini",
                                operation="generate", session=SessionOptions(mode="stateless"),
                                runtime_options={"model": MODEL, "voice": "Kore", "style": "calm"})


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from management_portal.resources import isa
    from management_common import upload_retention
    monkeypatch.setattr(speech, "execution_state_root", lambda: tmp_path / "execution")
    monkeypatch.setattr(isa, "upload_root", lambda: tmp_path / "uploads")
    monkeypatch.setattr(upload_retention, "created", lambda directory, **kw: directory.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(upload_retention, "consumed", lambda *a, **kw: None)
    return tmp_path


def test_explicit_native_capability_is_separate_from_chat_models():
    with pytest.raises(ValueError, match="no enabled"):
        protocol.configuration({**ENTRY, "speech": {}})
    with pytest.raises(ValueError, match="official"):
        protocol.configuration({**ENTRY, "base_url": "https://evil.invalid/v1beta"})
    with pytest.raises(ValueError, match="explicitly registered"):
        protocol.payload(ENTRY, "hello", "chat-only-model", "Kore")
    assert AIProviderRegistry.models(ENTRY) == []
    assert protocol.payload(ENTRY, "你好", MODEL, "Kore", "calm")["input"][0]["content"] == [
        {"type": "text", "text": "你好", "annotations": [{"type": "speech_metadata", "style": "calm"}]}]


@pytest.mark.parametrize("text,style", [("", ""), ("x" * 8001, ""), ("ok", "x" * 501)])
def test_bounds_before_provider_call(text, style):
    with pytest.raises(ValueError):
        protocol.payload(ENTRY, text, MODEL, "Kore", style)


def test_real_audio_container_required_and_model_mismatch_rejected():
    data, evidence = protocol.decode_response(answer(), MODEL)
    assert data == wav() and evidence["duration_seconds"] == .1
    assert evidence["usage"] == {"total_tokens": 9}
    response = answer()
    response["steps"][0]["content"][0]["data"] = base64.b64encode(b"a text response is not audio").decode()
    with pytest.raises(ValueError, match="WAV"):
        protocol.decode_response(response, MODEL)
    with pytest.raises(ValueError, match="differs"):
        protocol.decode_response({**answer(), "model": "some-other-model"}, MODEL)


def test_native_transport_owns_secret_and_never_echoes_http_body(monkeypatch):
    import urllib.request
    registry = AIProviderRegistry()
    monkeypatch.setattr(registry, "get", lambda _: ENTRY)
    monkeypatch.setattr(registry, "read_secret", lambda _: "private-test-token")
    seen = []
    def open_request(req, **kwargs):
        seen.append(req)
        raise urllib.error.HTTPError(req.full_url, 403, "private-test-token", {}, io.BytesIO(b"private-test-token"))
    monkeypatch.setattr(urllib.request, "build_opener", lambda handler: SimpleNamespace(open=open_request))
    with pytest.raises(AIProviderHTTPError) as caught:
        registry.request_speech("gemini", protocol.payload(ENTRY, "hi", MODEL, "Kore"))
    assert "private-test-token" not in str(caught.value) and caught.value.status_code == 403
    assert len(seen) == 1 and seen[0].full_url.endswith("/v1beta/interactions")
    assert seen[0].get_header("X-goog-api-key") == "private-test-token"


def test_generation_survives_reobservation_without_second_call(isolated, monkeypatch):
    from management_portal.resources.service import ResourceService
    calls = []
    registry = SimpleNamespace(get=lambda _: ENTRY, request_speech=lambda *a: calls.append(a) or answer())
    service = speech.SpeechGenerationService(registry)
    req = request()
    inv = SimpleNamespace(request=req)
    ctx = SimpleNamespace(is_cancelled=lambda: False, emit=lambda *a: None)
    result = service.execute(inv, ctx)
    again = service.execute(inv, ctx)
    assert result.output == again.output and len(calls) == 1
    assert result.output["provider_request_id"] == "provider-request-1"
    assert result.output["provider"] == "google" and result.output["model"] == MODEL
    assert "你好" not in json.dumps(result.output, ensure_ascii=False)
    received = []
    monkeypatch.setattr(ResourceService, "read_content", lambda self, address, **kw: received.append(kw) or (wav(), "audio/wav", {}))
    assert speech.verified_receipt(req.execution_id, actor="bob")["audio"] == result.output["audio"]
    assert received[0]["actor"] == "bob"  # Actual consumer ACL, not submitter impersonation.
    monkeypatch.setattr(ResourceService, "read_content", lambda *a, **kw: (b"changed", "audio/wav", {}))
    with pytest.raises(ValueError, match="does not match"):
        speech.verified_receipt(req.execution_id, actor="bob")


def test_uncertain_delivery_is_not_replayed(isolated):
    calls = []
    def failed(*args):
        calls.append(args)
        raise RuntimeError("private provider debug/token")
    service = speech.SpeechGenerationService(SimpleNamespace(get=lambda _: ENTRY, request_speech=failed))
    req = request()
    inv, ctx = SimpleNamespace(request=req), SimpleNamespace(is_cancelled=lambda: False, emit=lambda *a: None)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="no automatic retry"):
            service.execute(inv, ctx)
    record = speech.read_record(req.execution_id)
    assert len(calls) == 1 and record["status"] == "failed" and "receipt" not in record
    assert "private" not in json.dumps(record)


def test_crashed_dispatch_does_not_generate_again(isolated):
    from management_common.configuration_files import atomic_json
    req = request()
    speech.prepare(req)
    record = speech.read_record(req.execution_id)
    atomic_json(speech._path(req.execution_id), {**record, "status": "dispatched"})
    service = speech.SpeechGenerationService(SimpleNamespace())
    with pytest.raises(RuntimeError, match="will not be replayed"):
        service.execute(SimpleNamespace(request=req), SimpleNamespace(is_cancelled=lambda: False))


def test_instruction_submission_keeps_identity_and_pending_acl(isolated, monkeypatch):
    from management_portal.hatg.catalog import declaration
    calls = []
    monkeypatch.setattr(speech_isa.shared_ai_provider_registry, "get", lambda _: ENTRY)
    monkeypatch.setattr(speech_isa, "_client", lambda: SimpleNamespace(submit=lambda request: calls.append(request)))
    args = {"api_id": "gemini", "model": MODEL, "text": "你好", "voice": "Kore"}
    declaration("GenerateSpeech").validate_arguments(args)
    inv = SimpleNamespace(arguments=args, caller="alice", instruction_id="inst_exe_1234")
    first, second = speech_isa.generate_speech(inv), speech_isa.generate_speech(inv)
    assert first == second and calls[0].to_payload() == calls[1].to_payload()
    assert calls[0].service_type == "speech_generation" and not calls[0].system_instructions.parts
    with pytest.raises(InstructionError) as caught:
        speech_isa.get_speech_generation(SimpleNamespace(argument=lambda _: first["execution_id"], caller="bob"))
    assert caught.value.code == "instruction.not_found"


def test_discovery_is_explicit_and_redacted(monkeypatch):
    monkeypatch.setattr(speech_isa.shared_ai_provider_registry, "entries", lambda: [ENTRY, {**ENTRY, "speech": {}}])
    providers = speech_isa.list_speech_providers(None)["providers"]
    assert len(providers) == 2 and providers[1]["entitlement"] == "not_probed"
    assert providers[0]["provider"] == "microsoft" and providers[0]["model_identity_kind"] == "service_engine_alias"
    assert "api_key" not in json.dumps(providers) and "base_url" not in json.dumps(providers)


def test_speech_execution_runtime_dispatches_and_keeps_transcript(isolated, monkeypatch):
    from management_portal.execution.runtime import ExecutionRuntime
    sent = []
    monkeypatch.setattr(speech.shared_ai_provider_registry, "get", lambda _: ENTRY)
    monkeypatch.setattr(speech.shared_ai_provider_registry, "request_speech", lambda *args: sent.append(args) or answer())
    runtime = ExecutionRuntime(state_root=isolated / "runtime")
    req = request()
    result = runtime.execute(req)
    assert result.output["schema"] == "tfi.native-speech-receipt.v1"
    assert result.output["audio"]["sha256"]
    assert sent[0][1]["input"][0]["content"][0]["text"] == req.prompt
    assert "speech_generation" in runtime.capabilities()


def test_registry_speech_configuration_cannot_point_at_unrelated_service():
    from management_portal.execution.isa import _stored_fields, _check_api_kind
    fields = _stored_fields({"speech": ENTRY["speech"]})
    assert fields["speech"] == ENTRY["speech"]
    _check_api_kind({**ENTRY, "kind": "openai_compatible_chat", "enabled": False})
    with pytest.raises(InstructionError, match="Google"):
        _check_api_kind({**ENTRY, "kind": "openai_compatible_chat", "provider": "moonshot"})


def test_cloud_speech_rejects_unknown_voice_style_and_extreme_controls():
    from management_portal.execution import microsoft_speech as cloud
    assert cloud.validate("Hello", cloud.MODEL, "en-US-GuyNeural", rate="+9%")["rate"] == "+9%"
    for options in ({"voice": "fake"}, {"style": "dramatic"}, {"rate": "+999%"}, {"pitch": "loud"}, {"boundary": "guessed"}):
        args = dict(text="Hello", model=cloud.MODEL, voice="en-US-GuyNeural", **{})
        args.update(options)
        with pytest.raises(ValueError):
            cloud.validate(**args)


def test_worker_bookkeeping_does_not_change_speech_intent_identity(isolated):
    from dataclasses import replace
    req = request()
    speech.prepare(req)
    speech.prepare(replace(req, metadata={**req.metadata, "worker_attempt": 2},
                           runtime_options={**req.runtime_options, "resume_context": "worker-owned"}))
    with pytest.raises(ValueError, match="identity conflict"):
        speech.prepare(replace(req, prompt="different synthesis text"))


def test_cloud_receipt_uses_service_identity_and_never_reads_a_private_registry(isolated, monkeypatch):
    from management_portal.execution import microsoft_speech as cloud
    from management_portal.execution.accounts import ExecutionAccounts
    from management_portal.execution.runtime import ExecutionRuntime
    calls = []
    def render(text, **options):
        calls.append((text, options))
        return b"unit-mp3-fixture", {"media_type": "audio/mpeg", "usage": {}, "client": "edge-tts",
                    "client_version": "7.3.0", "language": "en-US", "reported_model": None,
                    "provider_request_id": "", "duration_seconds": 1.5}
    monkeypatch.setattr(cloud, "generate", render)
    monkeypatch.setattr(speech.shared_ai_provider_registry, "get", lambda _: pytest.fail("private registry read"))
    req = ExecutionRequest.new(actor="alice", prompt="Original transcript", service_type="speech_generation",
            provider=cloud.API_ID, operation="generate", session=SessionOptions(mode="stateless"),
            runtime_options={"model": cloud.MODEL, "voice": "en-US-GuyNeural", "rate": "+5%"})
    result = ExecutionRuntime(state_root=isolated / "runtime").execute(req)
    assert calls == [("Original transcript", req.runtime_options)]
    assert result.output["provider"] == "microsoft" and result.output["protocol"] == cloud.PROTOCOL
    assert result.output["audio"]["media_type"] == "audio/mpeg"
    assert result.output["reported_model"] is None and result.output["provider_request_id"] == ""
    accounts = ExecutionAccounts(())
    account = accounts.for_request(req.to_payload())
    assert account.max_concurrency == 2 and account.kind == "service" and not account.quota_enabled
    assert accounts.usage_identity(req.to_payload()) == ("microsoft", cloud.API_ID)


def test_cloud_transport_validates_container_and_records_actual_version(monkeypatch):
    from management_portal.execution import microsoft_speech as cloud
    import edge_tts
    import mutagen.mp3
    seen = []
    class Communicate:
        def __init__(self, *args, **kwargs):
            seen.append((args, kwargs))
        async def stream(self):
            yield {"type": "audio", "data": b"test fixture bytes"}
            yield {"type": "WordBoundary", "offset": 1_000_000, "duration": 2_000_000, "text": "hello"}
    monkeypatch.setattr(edge_tts, "Communicate", Communicate)
    monkeypatch.setattr(mutagen.mp3, "MP3", lambda stream: SimpleNamespace(info=SimpleNamespace(length=1.2, sample_rate=24000, channels=1)))
    data, observed = cloud.generate("hello", cloud.MODEL, "en-US-GuyNeural")
    assert data == b"test fixture bytes" and observed["client_version"] == cloud.version("edge-tts")
    event = observed["boundaries"][0]
    assert event == {"type": "WordBoundary", "offset_ticks": 1_000_000, "duration_ticks": 2_000_000,
                     "offset_seconds": .1, "duration_seconds": .2, "text": "hello", "input_start": 0,
                     "input_end": 5, "input_mapping": "exact"}
    assert len(seen) == 1 and seen[0][1]["boundary"] == "SentenceBoundary"
    monkeypatch.setattr(mutagen.mp3, "MP3", lambda stream: (_ for _ in ()).throw(ValueError("invalid MP3")))
    with pytest.raises(ValueError, match="invalid MP3"):
        cloud.generate("hello", cloud.MODEL, "en-US-GuyNeural")


def test_word_boundary_parameter_flows_through_formal_submission(isolated, monkeypatch):
    from management_portal.execution import microsoft_speech as cloud
    from management_portal.hatg.catalog import declaration
    calls = []
    monkeypatch.setattr(speech_isa, "_client", lambda: SimpleNamespace(submit=calls.append))
    args = dict(api_id=cloud.API_ID, model=cloud.MODEL, voice="zh-CN-YunxiNeural", text="先相乘，再相加。", boundary="WordBoundary")
    declaration("GenerateSpeech").validate_arguments(args)
    speech_isa.generate_speech(SimpleNamespace(arguments=args, caller="alice", instruction_id="inst_words"))
    assert calls[0].runtime_options["boundary"] == "WordBoundary"
    from dataclasses import replace
    with pytest.raises(ValueError, match="identity conflict"):
        speech.prepare(replace(calls[0], runtime_options={**calls[0].runtime_options, "boundary": "SentenceBoundary"}))
    with pytest.raises(ValueError, match="only supported"):
        protocol.payload(ENTRY, "hi", MODEL, "Kore", boundary="WordBoundary")


def test_mapping_uses_original_codepoints_and_no_double_xml_unescape():
    from management_portal.execution.microsoft_speech import _boundary_evidence
    original = " 你好，你好。\x0bA &amp; <B> 𝄞"
    words = ["你好", "你好", "A", "&amp;", "<B>", "𝄞"]
    chunks = [dict(type="WordBoundary", text=w, offset=i*1234567, duration=12345) for i,w in enumerate(words)]
    events = _boundary_evidence(original, chunks)
    cursor = 0
    for event, word, chunk in zip(events, words, chunks):
        assert event["input_mapping"] == "exact"
        assert event["input_start"] >= cursor
        assert original[event["input_start"]:event["input_end"]] == word
        assert event["duration_ticks"] == chunk["duration"]
        assert event["offset_seconds"] == chunk["offset"]/10_000_000
        cursor = event["input_end"]
    assert events[-1]["input_end"]-events[-1]["input_start"] == 1  # Not UTF-16 units.


def test_ambiguous_normalization_never_skips_words_or_fabricates_positions():
    from management_portal.execution.microsoft_speech import _boundary_evidence
    events = _boundary_evidence("2相乘，二相乘", [
        dict(type="WordBoundary", text="二", offset=100, duration=200),
        dict(type="WordBoundary", text="相乘", offset=300, duration=400)])
    assert [e["input_mapping"] for e in events] == ["unmatched", "unresolved_after_mismatch"]
    assert all(e["input_start"] is None and e["input_end"] is None for e in events)
    assert events[0]["text"] == "二" and events[0]["offset_ticks"] == 100


def test_word_events_survive_execution_and_are_bound_to_fixed_audio(isolated, monkeypatch):
    from management_portal.execution import microsoft_speech as cloud
    from management_portal.execution.runtime import ExecutionRuntime
    from management_portal.resources.service import ResourceService
    import edge_tts
    import mutagen.mp3
    sent = []
    class Communicate:
        def __init__(self, text, voice, **options):
            sent.append((text, voice, options))
        async def stream(self):
            yield dict(type="audio", data=b"mp3-test-container")
            yield dict(type="WordBoundary", text="相乘", offset=1_000_000, duration=2_000_000)
            yield dict(type="WordBoundary", text="相加", offset=8_000_000, duration=3_000_000)
    monkeypatch.setattr(edge_tts, "Communicate", Communicate)
    monkeypatch.setattr(mutagen.mp3, "MP3", lambda _: SimpleNamespace(info=SimpleNamespace(length=1.2, sample_rate=24000, channels=1)))
    req=ExecutionRequest.new(actor="alice", prompt="相乘，相加", service_type="speech_generation", provider=cloud.API_ID,
        operation="generate", session=SessionOptions(mode="stateless"),
        runtime_options=dict(model=cloud.MODEL, voice="zh-CN-YunxiNeural", boundary="WordBoundary"))
    receipt=ExecutionRuntime(state_root=isolated/"runtime").execute(req).output
    assert sent[0][2]["boundary"] == "WordBoundary"
    assert receipt["alignment"]["source_audio_sha256"] == hashlib.sha256(b"mp3-test-container").hexdigest()
    assert receipt["alignment"]["input_sha256"] == receipt["text_sha256"]
    assert receipt["alignment"]["mapped_event_count"] == 2
    assert receipt["boundaries"][1]["input_start"] == 3
    assert receipt["boundaries"][1]["offset_seconds"] == .8  # Never interpolated from characters.
    def deny(self, address, *, actor, **kw):
        assert actor == "bob"
        raise PermissionError("denied")
    monkeypatch.setattr(ResourceService, "read_content", deny)
    with pytest.raises(PermissionError):
        speech.verified_receipt(req.execution_id, actor="bob")
