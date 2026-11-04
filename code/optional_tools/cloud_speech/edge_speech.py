"""Microsoft's cloud speech via edge-tts. No local synthesis or private credentials."""
import asyncio
import io
import re
import unicodedata

MAX_TEXT = 8000
MAX_AUDIO = 24 * 1024 * 1024
from importlib.metadata import version

MODEL = "edge-neural-tts"  # Service engine alias; Microsoft does not report a model version.
VOICES = {"en-US-GuyNeural": "en-US", "en-US-JennyNeural": "en-US",
          "zh-CN-XiaoxiaoNeural": "zh-CN", "zh-CN-YunxiNeural": "zh-CN"}


def validate(text, model, voice, style="", **options):
    if model != MODEL or voice not in VOICES:
        raise ValueError("Select a supported Microsoft cloud speech engine and voice")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError(f"Speech text requires 1–{MAX_TEXT} characters")
    if style:
        raise ValueError("Microsoft cloud speech supports rate/volume/pitch, not free-form style or SSML")
    boundary = options.get("boundary", "SentenceBoundary")
    if boundary not in {"WordBoundary", "SentenceBoundary"}:
        raise ValueError("Unsupported Microsoft speech boundary")
    values = {"boundary": boundary}
    for name, unit, low, high in (("rate", "%", -50, 100), ("volume", "%", -100, 100), ("pitch", "Hz", -100, 100)):
        value = options.get(name, "+0" + unit)
        if not isinstance(value, str) or not re.fullmatch(r"[+-]\d{1,3}" + unit, value):
            raise ValueError(f"Invalid speech {name}")
        if not low <= int(value[:-len(unit)]) <= high:
            raise ValueError(f"Speech {name} exceeds supported limits")
        values[name] = value
    return values


def _boundary_evidence(text, chunks):
    """Preserve provider timing; locate text conservatively, never infer timing.

    edge-tts replaces incompatible controls one-for-one before XML escaping.
    SDK event text is already XML-unescaped once. Do not unescape it again.
    After a mismatch, later repeated words cannot safely re-anchor the stream.
    """
    from edge_tts.communicate import remove_incompatible_characters
    normalized = remove_incompatible_characters(text)
    cursor, mapping_lost = 0, False
    result = []
    for chunk in chunks:
        offset, duration, word = chunk["offset"], chunk["duration"], chunk["text"]
        if (type(offset) is not int or type(duration) is not int or offset < 0 or duration < 0
                or not isinstance(word, str)):
            raise ValueError("Invalid cloud speech boundary")
        begin = normalized.find(word, cursor) if word and not mapping_lost else -1
        # Only punctuation/spacing omitted by the provider may precede a match.
        exact = begin >= 0 and all(c.isspace() or unicodedata.category(c).startswith("P")
                                   for c in normalized[cursor:begin])
        status = "exact" if exact else ("unresolved_after_mismatch" if mapping_lost else "unmatched")
        end = begin + len(word) if exact else None
        result.append({"type": chunk["type"], "offset_ticks": offset, "duration_ticks": duration,
                       "offset_seconds": offset / 10_000_000, "duration_seconds": duration / 10_000_000,
                       "text": word, "input_start": begin if exact else None, "input_end": end,
                       "input_mapping": status})
        if exact:
            cursor = end
        else:
            mapping_lost = True
    return result


async def _stream(text, voice, options):
    import edge_tts
    audio = bytearray()
    boundaries = []
    async for chunk in edge_tts.Communicate(text, voice, **options).stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
            if len(audio) > MAX_AUDIO:
                raise ValueError("Cloud speech exceeds the output limit")
        elif chunk["type"] in {"WordBoundary", "SentenceBoundary"}:
            boundaries.append(chunk)
    return bytes(audio), _boundary_evidence(text, boundaries)


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
                  "media_type": "audio/mpeg", "boundaries": boundaries,
                  "alignment": {"schema": "tfi.speech-alignment.v1", "requested_boundary": values["boundary"],
                      "source": "edge-tts stream metadata", "ticks_per_second": 10_000_000,
                      "time_origin": "start_of_source_audio; SDK chunk offset compensation included",
                      "input_offset_unit": "unicode_code_point", "input_span": "zero_based_half_open",
                      "input_basis": "original tool input text before XML escaping",
                      "normalization": "edge-tts incompatible controls replaced by one space; event text XML-unescaped once",
                      "mapping_method": "ordered_exact_text; skip only punctuation/whitespace; stop mapping after mismatch",
                      "event_count": len(boundaries),
                      "mapped_event_count": sum(b["input_mapping"] == "exact" for b in boundaries)}, "usage": {}}


def main():
    """Optional Resident tool. No Portal imports, credentials or registration."""
    import argparse
    import hashlib
    import importlib.util
    import json
    from pathlib import Path
    from datetime import datetime, timezone
    from uuid import uuid4
    parser = argparse.ArgumentParser(description="Optional Microsoft cloud speech tool; not a Portal capability")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--text-file")
    parser.add_argument("--output-dir")
    parser.add_argument("--voice", default="zh-CN-YunxiNeural", choices=VOICES)
    parser.add_argument("--boundary", default="WordBoundary", choices=["WordBoundary", "SentenceBoundary"])
    parser.add_argument("--rate", default="+0%")
    args = parser.parse_args()
    missing = [name for name in ("edge_tts", "mutagen") if importlib.util.find_spec(name) is None]
    if args.check:
        print(json.dumps({"available": not missing, "missing_packages": missing, "cloud_probed": False}))
        return 0
    if missing:
        print(json.dumps({"status": "unavailable", "missing_packages": missing}))
        return 2
    if not args.text_file or not args.output_dir:
        parser.error("--text-file and --output-dir are required")
    directory = Path(args.output_dir)
    directory.mkdir(parents=True, exist_ok=False)  # Never overwrite a prior run/audio.
    text = Path(args.text_file).read_text(encoding="utf-8")
    run_id = "edge-speech-" + uuid4().hex
    started = datetime.now(timezone.utc).isoformat()
    try:
        data, evidence = generate(text, MODEL, args.voice, boundary=args.boundary, rate=args.rate)
    except Exception as exc:
        failure = {"status": "failed", "run_id": run_id, "error_type": type(exc).__name__, "automatic_retry": False}
        (directory / "failure.json").write_text(json.dumps(failure))
        print(json.dumps(failure))
        return 1
    sha = hashlib.sha256(data).hexdigest()
    input_sha = hashlib.sha256(text.encode()).hexdigest()
    evidence["alignment"].update(source_audio_sha256=sha, input_sha256=input_sha)
    record = {"schema": "resident-tool-audio/v1", "run_id": run_id, "provider": "microsoft",
              "model": MODEL, "voice": args.voice, "started_at": started,
              "completed_at": datetime.now(timezone.utc).isoformat(), "text_sha256": input_sha,
              "audio": {"file": "speech.mp3", "sha256": sha, "size": len(data), "media_type": "audio/mpeg"},
              "evidence_kind": "tool_report; not a Portal or vendor attestation", **evidence}
    (directory / "speech.mp3").write_bytes(data)
    (directory / "receipt.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({"status": "completed", "run_id": run_id, "audio_sha256": sha,
                      "receipt": str(directory / "receipt.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
