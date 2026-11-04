"""Microsoft's cloud speech via edge-tts. No local synthesis or private credentials."""
import asyncio
import io
import re
from importlib.metadata import version

API_ID = "microsoft-edge-tts"
PROTOCOL = "microsoft_edge_tts"
MODEL = "edge-neural-tts"  # Service engine alias; Microsoft does not report a model version.
VOICES = {"en-US-GuyNeural": "en-US", "en-US-JennyNeural": "en-US",
          "zh-CN-XiaoxiaoNeural": "zh-CN", "zh-CN-YunxiNeural": "zh-CN"}
ENTRY = {"id": API_ID, "provider": "microsoft", "speech": {
    "protocol": PROTOCOL, "models": [MODEL], "voices": list(VOICES)}}


def validate(text, model, voice, style="", **options):
    from .speech_protocol import MAX_TEXT
    if model != MODEL or voice not in VOICES:
        raise ValueError("Select a registered Microsoft cloud speech engine and voice")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError(f"Speech text requires 1–{MAX_TEXT} characters")
    if style:
        raise ValueError("Microsoft cloud speech supports rate/volume/pitch, not free-form style or SSML")
    values = {}
    for name, unit, low, high in (("rate", "%", -50, 100), ("volume", "%", -100, 100), ("pitch", "Hz", -100, 100)):
        value = options.get(name, "+0" + unit)
        if not isinstance(value, str) or not re.fullmatch(r"[+-]\d{1,3}" + unit, value):
            raise ValueError(f"Invalid speech {name}")
        if not low <= int(value[:-len(unit)]) <= high:
            raise ValueError(f"Speech {name} exceeds supported limits")
        values[name] = value
    return values


def descriptor():
    from .speech_protocol import MAX_TEXT
    return {"api_id": API_ID, "provider": "microsoft", **ENTRY["speech"],
            "source": "Microsoft Edge online speech / edge-tts", "client_version": version("edge-tts"),
            "voice_languages": VOICES, "model_identity_kind": "service_engine_alias",
            "entitlement": "public_cloud_service_no_private_credentials", "availability": "not_probed",
            "max_text_characters": MAX_TEXT, "output_media_type": "audio/mpeg",
            "options": {"rate": "-50%..+100%", "volume": "-100%..+100%", "pitch": "-100Hz..+100Hz"}}


async def _stream(text, voice, options):
    import edge_tts
    from .speech_protocol import MAX_AUDIO
    audio = bytearray()
    boundaries = []
    async for chunk in edge_tts.Communicate(text, voice, **options).stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
            if len(audio) > MAX_AUDIO:
                raise ValueError("Cloud speech exceeds the output limit")
        elif chunk["type"] in {"WordBoundary", "SentenceBoundary"}:
            boundaries.append({"type": chunk["type"], "offset_seconds": chunk["offset"] / 10_000_000,
                               "duration_seconds": chunk["duration"] / 10_000_000})
    return bytes(audio), boundaries


def generate(text, model, voice, style="", **options):
    from mutagen.mp3 import MP3
    values = validate(text, model, voice, style, **options)
    data, boundaries = asyncio.run(asyncio.wait_for(_stream(text, voice, values), timeout=180))
    info = MP3(io.BytesIO(data)).info
    if not data or info.length <= 0 or info.sample_rate <= 0:
        raise ValueError("Cloud speech did not return readable MP3 audio")
    return data, {"reported_model": None, "provider_request_id": "", "model_identity_kind": "service_engine_alias",
                  "source": "Microsoft Edge online speech / edge-tts", "client": "edge-tts",
                  "client_version": version("edge-tts"), "language": VOICES[voice], "speech_options": values,
                  "duration_seconds": info.length, "sample_rate": info.sample_rate, "channels": info.channels,
                  "media_type": "audio/mpeg", "boundaries": boundaries, "usage": {}}
