"""Native speech contract. Chat models never implicitly acquire audio capability."""
import base64
import binascii
import io
import re
import wave
from urllib.parse import urlsplit

PROTOCOL = "google_interactions_tts"
MAX_TEXT = 8000
MAX_AUDIO = 24 * 1024 * 1024
MAX_RESPONSE = 34 * 1024 * 1024


def resolve_entry(registry, api_id):
    from . import microsoft_speech
    return microsoft_speech.ENTRY if api_id == microsoft_speech.API_ID else registry.get(api_id)


def validate_configuration(value):
    if value is None or value == {}:
        return
    if not isinstance(value, dict) or set(value) != {"protocol", "models", "voices"}:
        raise ValueError("speech requires protocol, models and voices")
    if value["protocol"] != PROTOCOL:
        raise ValueError("Unsupported native speech protocol")
    for name in ("models", "voices"):
        values = value[name]
        if not isinstance(values, list) or not values or len(values) > 100:
            raise ValueError("speech models/voices must be nonempty arrays")
        if any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", v) for v in values):
            raise ValueError("Invalid speech model or voice")
        if len(values) != len(set(values)):
            raise ValueError("Duplicate speech model or voice")


def configuration(entry, *, require_enabled=True):
    config = entry.get("speech")
    validate_configuration(config)
    if not config or (require_enabled and entry.get("enabled") is False):
        raise ValueError("This API has no enabled native speech capability")
    url = urlsplit(str(entry.get("base_url") or ""))
    if (entry.get("provider") != "google" or url.scheme != "https"
            or url.netloc != "generativelanguage.googleapis.com"
            or url.path.rstrip("/") not in {"/v1beta", "/v1beta/openai"}
            or url.query or url.fragment):
        raise ValueError("Native Google speech requires the registered official Google API endpoint")
    return config


def payload(entry, text, model, voice, style="", **options):
    from . import microsoft_speech
    if entry.get("id") == microsoft_speech.API_ID:
        return microsoft_speech.validate(text, model, voice, style, **options)
    if options:
        raise ValueError("rate/volume/pitch are only supported by Microsoft cloud speech")
    config = configuration(entry)
    if model not in config["models"] or voice not in config["voices"]:
        raise ValueError("Speech model and voice must be explicitly registered")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError(f"Speech text requires 1–{MAX_TEXT} characters")
    if not isinstance(style, str) or len(style) > 500:
        raise ValueError("Speech style must be at most 500 characters")
    content = {"type": "text", "text": text}
    if style:
        content["annotations"] = [{"type": "speech_metadata", "style": style}]
    return {"model": model, "input": [{"type": "user_input", "content": [content]}],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": voice}]}}


def decode_response(response, requested_model):
    if not isinstance(response, dict) or response.get("status") not in {None, "completed"}:
        raise ValueError("Native speech response did not complete")
    reported = response.get("model")
    if reported and reported != requested_model:
        raise ValueError("Native speech response model differs from the requested model")
    blocks = [content for step in response.get("steps", []) if step.get("type") == "model_output"
              for content in step.get("content", []) if content.get("type") == "audio"]
    if len(blocks) != 1:
        raise ValueError("Expected exactly one complete native audio block")
    try:
        data = base64.b64decode(blocks[0].get("data", ""), validate=True)
    except (ValueError, binascii.Error, TypeError):
        raise ValueError("Invalid native audio encoding") from None
    if not data or len(data) > MAX_AUDIO:
        raise ValueError("Native audio is empty or exceeds the output limit")
    # The Interactions TTS contract returns a WAV container, not raw PCM.
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            frames, rate = source.getnframes(), source.getframerate()
            channels, width = source.getnchannels(), source.getsampwidth()
            if not frames or rate <= 0 or source.getcomptype() != "NONE":
                raise ValueError("Invalid native WAV format")
            if len(source.readframes(frames)) != frames * channels * width:
                raise ValueError("Truncated native WAV")
    except (wave.Error, EOFError):
        raise ValueError("Native speech did not return a readable WAV file") from None
    usage = response.get("usage") or {}
    usage = {k: v for k, v in usage.items() if re.fullmatch(r"[a-z_]+", k)
             and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0}
    return data, {"reported_model": reported or None, "provider_request_id": str(response.get("id") or "")[:200],
                  "duration_seconds": frames / rate, "sample_rate": rate,
                  "channels": channels, "media_type": "audio/wav", "usage": usage}
