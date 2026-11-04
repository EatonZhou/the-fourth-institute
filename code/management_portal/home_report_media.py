"""Derived report videos with animated diagrams and readable captions.

This renderer produces silent video. Audio creation belongs to external tools;
existing media files remain readable through the ordinary resource boundary.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from management_common.portal_paths import path as portal_path
from management_portal import business_tasks
from management_portal.home_report import digest, reauthorize
from management_portal.media_tools import capabilities, stamp

_SLOTS = threading.BoundedSemaphore(2)
_LOCK = threading.Lock()
FILES = {"video.mp4": "video/mp4", "captions.vtt": "text/vtt; charset=utf-8",
         "poster.png": "image/png", "transcript.txt": "text/plain; charset=utf-8", "narration.wav": "audio/wav",
         "reasoning.json": "application/json"}


def caption_text(value, budget=180):
    """Prepare display captions; keep source IDs, addresses and wording in reports.

    Select complete sentences/clauses. Never cut through a word to meet a
    character budget; a single unbroken overlong fragment stays in the text.
    """
    value = re.sub(r"```[\s\S]*?```", "", str(value or ""))
    value = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"(?:https?://|file://|/)[^\s，。！？；、<>）)\]]+", "", value)
    value = re.sub(r"\b[a-z][a-z0-9_]*_[0-9a-f]{12,64}\b", "", value)
    value = re.sub(r"[`#*_]", "", value)
    value = re.sub(r"[（(][\s、，,]*[）)]", "", value)
    value = re.sub(r"[ \t]+", " ", value).strip(" \n，,：:；;")
    value = re.sub(r"\.{2,}|(?<!\d)\.(?!\d)", "。", value)
    value = re.sub(r"[。!?！？]+", lambda m: "？" if any(c in m[0] for c in '?？') else "！" if any(c in m[0] for c in '!！') else "。", value)
    value = re.sub(r"[，,；;：:]+(?=[。！？])", "", value)
    value = re.sub(r"(?<=[。！？])[，,；;：:]+", "", value)
    units = re.findall(r"[^。！？\n]+[。！？]?", value)
    selected = []
    for unit in units:
        if sum(map(len, selected)) + len(unit) <= budget:
            selected.append(unit)
            continue
        if not selected:
            for clause in re.findall(r"[^，,；;：:]+[，,；;：:]?", unit):
                if sum(map(len, selected)) + len(clause) > budget:
                    break
                selected.append(clause)
        break
    result = "".join(selected).strip(" \n，,；;：:")
    if not result:
        return "完整内容请阅读文字报告。" if value else ""
    return result if result[-1] in "。！？" else result + "。"


def chapters(report):
    from management_portal.home_report_reasoning import build_reasoning
    plan = report.get('reasoning') or build_reasoning(report)
    if not plan['available']:
        raise ValueError(plan['reason'])
    return [{**step, 'speech': caption_text(step['speech'], 1800)} for step in plan['steps']]


def render(report, directory: Path):
    from PIL import ImageFont
    from management_portal.home_report_reasoning import build_reasoning
    from management_portal.home_report_visual import frame as draw_frame
    started = time.monotonic()
    from management_portal.home_explanation_quality import policy
    quality_policy = policy(report.get('media_quality', 'medium'))
    tools = capabilities()
    if not tools["available"]:
        raise RuntimeError("视频生成依赖缺少：" + "、".join(tools["missing"]))
    plan = report.get('reasoning') or build_reasoning(report)
    scenes = chapters(report)
    fonts = {size: ImageFont.truetype(tools["font"], size) for size in (16,18,20,22,24,32)}
    cues, offset, transcript = [], 0.0, []
    for scene in scenes:
        scene['start'], scene['trace_windows'] = offset, []
        for beat in scene.get('beats', [{'speech': scene['speech']}]):
            beat_start = offset
            # Reading-time pacing for silent captions, never audio alignment.
            for part in re.findall(r"[^。！？，,；;：:]+[。！？，,；;：:]?", caption_text(beat['speech'], 1800)):
                duration = max(2.0, min(12.0, len(part) / 5.0))
                cues.append({'start': offset, 'end': offset + duration, 'text': part})
                offset += duration
            if beat.get('edge'):
                scene['trace_windows'].append({'start': beat_start, 'end': offset, 'edge': beat['edge']})
        offset += .55
        scene['end'] = offset
        transcript.append(f"{stamp(scene['start'])} {scene['title']}\n{scene['speech']}")
    (directory / "captions.vtt").write_text("WEBVTT\n\n" + "\n\n".join(
        f"{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for c in cues), encoding="utf-8")
    (directory / "transcript.txt").write_text("\n\n".join(transcript), encoding="utf-8")
    (directory / "reasoning.json").write_text(json.dumps({'report_version':report['version'],
        'quality_policy':quality_policy,'plan':plan,'scenes':scenes},ensure_ascii=False,indent=2),encoding='utf-8')
    fps, width, height = 12, 1280, 720
    args = [tools["ffmpeg"], "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", "-threads", "2",
            "-an", "-movflags", "+faststart", str(directory / "video.mp4")]
    encoding_started = time.monotonic()
    captured = set()
    with (directory / "encoding.log").open("wb") as error_log:
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=error_log)
        try:
            for number in range(math.ceil(offset * fps)):
                at = number / fps
                index = next((i for i,s in enumerate(scenes) if at < s["end"]), len(scenes)-1)
                scene = scenes[index]
                caption = next((c["text"] for c in cues if c["start"] <= at < c["end"]), "")
                windows = scene['trace_windows']
                fraction = sum(max(0,min(1,(at-w['start'])/max(.01,w['end']-w['start']))) for w in windows) / max(1,len(windows))
                img = draw_frame(plan,index,at-scene['start'],scene['end']-scene['start'],caption,fonts,fraction)
                if number == fps:
                    img.save(directory / "poster.png")
                if index not in captured and at >= scene['start'] + min(3,(scene['end']-scene['start'])/2):
                    img.save(directory / f"chapter-{index+1:02}.png")
                    captured.add(index)
                process.stdin.write(img.tobytes())
            process.stdin.close()
            if process.wait(timeout=60):
                raise RuntimeError("视频编码失败，请检查媒体依赖后重试")
        except BaseException:
            process.kill(); process.wait()
            raise
    (directory / "encoding.log").unlink(missing_ok=True)
    return {"quality_policy":quality_policy,"duration": round(offset,3), "chapters":[{"title":s["title"],"start":s["start"]} for s in scenes],
            "scope_note": plan['assumption'] + ' ' + plan['limit'],
            "voice_note": "无配音；按字幕阅读时间播放。",
            "transcript": "\n\n".join(transcript), "engine": "continuous graph + ffmpeg/libx264",
            "generation_seconds":round(time.monotonic()-started,3),
            "encoding_seconds":round(time.monotonic()-encoding_started,3)}


class MediaStore:
    def __init__(self, root=None, renderer=render):
        self.root = Path(root) if root is not None else portal_path("cache") / "home-reports"
        self.renderer = renderer

    def directory(self, actor, job):
        if not re.fullmatch(r"[a-f0-9]{64}", job):
            raise ValueError("媒体标识无效")
        return self.root / digest(actor) / job

    @staticmethod
    def save(directory, value):
        temporary = directory / "state.tmp"
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(directory / "state.json")

    def start(self, report, actor, *, retry=False):
        job = digest(["home-report-media-continuous-v3", report["version"]])
        directory = self.directory(actor, job)
        task = f"home-report:{digest(actor)}:{job}"
        with _LOCK:
            previous = self.status(job, actor, authorize=False) if (directory / "state.json").exists() else None
            if previous and (previous["status"] == "ready" or business_tasks.is_active(task)):
                return previous
            if previous and not retry:
                return previous
            if not _SLOTS.acquire(blocking=False):
                raise RuntimeError("视频生成繁忙；文字报告已可阅读，请稍后重试")
            try:
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                # Derived files have a bounded lifetime and are always rebuildable.
                old = sorted((p for p in directory.parent.iterdir() if p.is_dir() and not p.is_symlink() and p != directory),
                             key=lambda p: p.stat().st_mtime, reverse=True)
                for index, previous_dir in enumerate(old):
                    if (index >= 9 or time.time() - previous_dir.stat().st_mtime > 7 * 86400) and not business_tasks.is_active(f"home-report:{digest(actor)}:{previous_dir.name}"):
                        shutil.rmtree(previous_dir)
                state = {"job_id": job, "status": "pending", "version": report["version"], "report": report}
                self.save(directory, state)
            except BaseException:
                _SLOTS.release()
                raise

            def generate():
                started = time.monotonic()
                try:
                    data = self.renderer(report, directory)
                    self.save(directory, {**state, **data, "status": "ready"})
                except Exception as exc:
                    self.save(directory, {**state, "status": "failed", "error": str(exc)[:800],
                                          "generation_seconds": round(time.monotonic() - started, 3)})
                finally:
                    _SLOTS.release()
            try:
                if not business_tasks.start(task, generate):
                    _SLOTS.release()
            except BaseException:
                _SLOTS.release()
                raise
        return {key: value for key, value in state.items() if key != "report"}

    def status(self, job, actor, *, authorize=True, read=None):
        directory = self.directory(actor, job)
        state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        if authorize:
            reauthorize(state["report"], actor=actor, read=read)
        if state["status"] == "pending" and not business_tasks.is_active(f"home-report:{digest(actor)}:{job}"):
            # Authorization may outlast generation. Re-read the atomic result
            # before classifying a now-inactive producer as interrupted.
            state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
            if state["status"] == "pending":
                state = {**state, "status": "failed", "error": "上次生成已中断，可以重试。"}
        return {key: value for key, value in state.items() if key != "report"}

    def file(self, job, filename, actor, *, read=None):
        if filename not in FILES:
            raise FileNotFoundError("媒体文件不存在")
        state = self.status(job, actor, read=read)
        if state["status"] != "ready":
            raise FileNotFoundError("媒体尚未生成")
        return self.directory(actor, job) / filename, FILES[filename]
