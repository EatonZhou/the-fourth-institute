"""Derived, principal-scoped report media; local speech and animated diagrams.

Requires ffmpeg, espeak-ng and a CJK font. Explicit executable paths can be
configured with TFI_HOME_REPORT_FFMPEG / TFI_HOME_REPORT_ESPEAK. No model/API
call, external upload, task mutation or persistent fact storage is involved.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

from management_common.portal_paths import path as portal_path
from management_portal import business_tasks
from management_portal.home_report import digest, reauthorize

_SLOTS = threading.BoundedSemaphore(2)
_LOCK = threading.Lock()
FILES = {"video.mp4": "video/mp4", "captions.vtt": "text/vtt; charset=utf-8",
         "poster.png": "image/png", "transcript.txt": "text/plain; charset=utf-8"}


def executable(name, setting):
    from management_portal.home_report_dependencies import directory
    managed = directory() / 'bin' / name
    candidate = os.environ.get(setting) or (str(managed) if managed.is_file() else shutil.which(name))
    if not candidate or not Path(candidate).is_file():
        raise RuntimeError(f"视频依赖未安装：{name}（可配置 {setting}）")
    return str(candidate)


def capabilities():
    missing = []
    result = {}
    for key, name, setting in (("ffmpeg", "ffmpeg", "TFI_HOME_REPORT_FFMPEG"), ("speech", "espeak-ng", "TFI_HOME_REPORT_ESPEAK")):
        try:
            result[key] = executable(name, setting)
        except RuntimeError:
            missing.append(name)
    from management_portal.home_report_dependencies import directory
    managed_font = directory() / 'share/font.ttc'
    font = os.environ.get("TFI_HOME_REPORT_FONT") or (str(managed_font) if managed_font.is_file() else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    if not Path(font).is_file():
        missing.append("CJK font / TFI_HOME_REPORT_FONT")
    return {"available": not missing, "missing": missing, "font": font, **result}


def _run(args, **kwargs):
    from management_portal.home_report_dependencies import directory
    if Path(args[0]).parent == directory() / 'bin':
        env = dict(os.environ)
        env['LD_LIBRARY_PATH'] = str(directory() / 'lib')
        env['ESPEAK_DATA_PATH'] = str(directory() / 'share')
        kwargs['env'] = env
    result = subprocess.run(args, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, timeout=180, **kwargs)
    if result.returncode:
        raise RuntimeError(f"媒体工具退出 {result.returncode}: {result.stderr.decode(errors='replace')[-600:]}")


def spoken_text(value, budget=180):
    """Prepare speech only; keep source IDs, addresses and wording in reports.

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


def speech_units(value):
    # Punctuation is a natural speech/caption boundary. Keep words intact.
    return re.findall(r"[^。！？，,；;：:]+[。！？，,；;：:]?", value)


def chapters(report):
    rows = report["rows"]
    if not rows:
        raise ValueError("当前内容不足以形成视觉讲解，请阅读文字报告。")
    scenes = []
    names = {row["id"]: row["title"] for row in rows}
    relation_labels = {"builds_on": "建立在这一项的基础上", "depends_on": "依赖这一项", "converges": "与这一项汇合", "alternative": "与这一项互为替代"}
    for relation in report.get("relations", [])[:2]:
        if relation["kind"] in relation_labels:
            left, right = names[relation["from"]], names[relation["to"]]
            label = relation_labels[relation["kind"]]
            scenes.append({"title": "已登记的工作关系", "speech": spoken_text(f"{left}，{label}，{right}。"),
                           "mode": "relation", "left": left, "right": right, "label": label})
    # Free-form prose is not a mechanism model. When only the registered
    # relation is demonstrable, keep the video focused on that relation.
    if scenes and report['target']['kind'] != 'task':
        return scenes
    if report['target']['kind'] != 'task' and len({r['status'] for r in rows}) < 2:
        raise ValueError('当前报告没有可视化的登记关系或状态差异，请阅读文字内容。')
    for row in rows[:5]:
        reply_text = (row.get('replies') or [{}])[-1].get('text', '')
        speech = spoken_text(row['title'], 90) + spoken_text(f"当前状态，{row['status']}。")
        if report['target']['kind'] == 'task':
            speech += spoken_text(row['body'], 160)
            speech += "已读取的原始回复说：" + spoken_text(reply_text, 180) + "回复内容仍需逐项核实，不能据此判定任务完成。" if reply_text else "当前没有可读的原始回复，不能推断已有交付产物。"
        scenes.append({"title": row["title"], "speech": speech, "mode": "domain", "row": row})
    return scenes


def _stamp(seconds):
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"


def render(report, directory: Path):
    from PIL import Image, ImageDraw, ImageFont
    started = time.monotonic()
    tools = capabilities()
    if not tools["available"]:
        raise RuntimeError("视频生成依赖缺少：" + "、".join(tools["missing"]))
    scenes = chapters(report)
    rows = report["rows"]
    font_path = tools["font"]
    fonts = {size: ImageFont.truetype(font_path, size) for size in (20, 24, 28, 34, 42)}
    cues, pcm, rate, offset = [], [], None, 0.0
    speech_started = time.monotonic()
    transcript = []
    for scene in scenes:
        scene["start"] = offset
        # Per-sentence audio gives exact subtitle times without inferred alignment.
        for sentence_part in speech_units(scene["speech"]):
            wav = directory / "speech-part.wav"
            # eSpeak's output filename buffer truncates long absolute paths.
            # A local filename also works in deeply nested instance caches.
            _run([tools["speech"], "-v", "cmn", "-s", "165", "-w", wav.name, "--stdin"], input=sentence_part.encode(), cwd=directory)
            with wave.open(str(wav)) as audio:
                if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                    raise RuntimeError("语音输出格式不受支持")
                if rate and rate != audio.getframerate():
                    raise RuntimeError("语音采样率变化")
                rate = audio.getframerate()
                duration = audio.getnframes() / rate
                pcm.append(audio.readframes(audio.getnframes()))
            cues.append({"start": offset, "end": offset + duration, "text": sentence_part})
            offset += duration
        scene["end"] = offset
        transcript.append(f"{_stamp(scene['start'])} {scene['title']}\n{scene['speech']}")
    with wave.open(str(directory / "narration.wav"), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"".join(pcm))
    speech_seconds = time.monotonic() - speech_started
    (directory / "captions.vtt").write_text("WEBVTT\n\n" + "\n\n".join(
        f"{_stamp(c['start'])} --> {_stamp(c['end'])}\n{c['text']}" for c in cues), encoding="utf-8")
    (directory / "transcript.txt").write_text("\n\n".join(transcript), encoding="utf-8")

    def draw_text(draw, xy, value, size=24, width=860, fill="#e5eaf4", max_lines=4):
        x, y = xy
        line, lines = "", []
        for char in str(value).replace("\n", " "):
            if draw.textlength(line + char, font=fonts[size]) > width:
                lines.append(line)
                line = ""
            line += char
        lines.append(line)
        for line in lines[:max_lines]:
            draw.text((x, y), line, font=fonts[size], fill=fill)
            y += size + 9
        return y

    fps, width, height = 12, 960, 540
    args = [tools["ffmpeg"], "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
            "-r", str(fps), "-i", "pipe:0", "-i", str(directory / "narration.wav"),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", "-threads", "2",
            "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", str(directory / "video.mp4")]
    encoding_started = time.monotonic()
    with (directory / "encoding.log").open("wb") as error_log:
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=error_log)
        try:
            for frame in range(math.ceil(offset * fps)):
                at = frame / fps
                scene = next((s for s in scenes if at < s["end"]), scenes[-1])
                progress = min(1, (at - scene["start"]) / 2.5)
                img = Image.new("RGB", (width, height), "#111b2d")
                draw = ImageDraw.Draw(img)
                draw_text(draw, (36, 22), scene["title"], 34, max_lines=1)
                draw_text(draw, (36, 72), f"{report['target']['kind']} · 核对 {report['checked_at'][:19]} · {report['version'][:10]}", 20, fill="#98a9c4", max_lines=1)
                if scene["mode"] == "domain":
                    selected = scene["row"]
                    if report['target']['kind'] == 'task':
                        reply = (selected.get('replies') or [{}])[-1].get('text','')
                        nodes = [('请求', selected['body'][:45]), ('处理状态', selected['status']), ('可核实回复', reply[:45] or '尚无可读回复，未推断交付产物')]
                        for n, (label, value) in enumerate(nodes):
                            x = 40 + n * 310
                            draw.rounded_rectangle((x,155,x+285,340),radius=12,outline='#83dfbc' if progress >= n/3 else '#60718b',width=3)
                            draw_text(draw,(x+16,170),label,24,width=255)
                            draw_text(draw,(x+16,225),value,24,width=255,max_lines=3)
                    else:
                        # Compare actual worklines at the same checked time. No
                        # invented timeline or percentage-of-completion scale.
                        for n, item in enumerate(rows[:5]):
                            y = 124+n*57
                            chosen = item['id']==selected['id']
                            draw.rounded_rectangle((35,y,925,y+47),radius=8,fill='#28465f' if chosen else '#19283d')
                            draw_text(draw,(50,y+5),item['title'],20,width=635,max_lines=1)
                            draw_text(draw,(720,y+5),item['status'],20,width=190,max_lines=1,fill='#83dfbc' if chosen else '#b7c6db')
                    draw_text(draw,(40,410),'当前核对时点的对照 · 不表示历史演进',20,max_lines=1)
                elif scene["mode"] == "relation":
                    draw.rounded_rectangle((40, 160, 360, 350), radius=14, outline="#7bb3ff", width=3)
                    draw.rounded_rectangle((600, 160, 920, 350), radius=14, outline="#83dfbc", width=3)
                    draw_text(draw, (62, 190), scene["left"], 28, width=274, max_lines=3)
                    draw_text(draw, (622, 190), scene["right"], 28, width=274, max_lines=3)
                    draw.line((362, 270, 595, 270), fill="#a5b9d4", width=3)
                    x = 366 + int(218 * ((at - scene["start"]) % 3 / 3))
                    draw.ellipse((x, 263, x + 14, 277), fill="#83dfbc")
                    draw.polygon([(598,270),(585,261),(585,279)], fill="#a5b9d4")
                    draw_text(draw, (385, 170), scene["label"], 20, width=190, max_lines=3)
                    draw_text(draw, (70, 388), "连线表示已登记关系；可打开来源核对", 24, max_lines=1)
                caption = next((c["text"] for c in cues if c["start"] <= at < c["end"]), "")
                draw.rectangle((0, 447, width, height), fill="#080e19")
                draw_text(draw, (35, 455), caption, 24, width=890, max_lines=2)
                draw.rectangle((0, 536, int(width * at / offset), 539), fill="#83dfbc")
                if frame == fps:
                    img.save(directory / "poster.png")
                process.stdin.write(img.tobytes())
            process.stdin.close()
            if process.wait(timeout=60):
                raise RuntimeError("视频编码失败，请检查媒体依赖后重试")
        except BaseException:
            process.kill()
            process.wait()
            raise
    for filename in ("narration.wav", "speech-part.wav", "encoding.log"):
        (directory / filename).unlink(missing_ok=True)
    return {"duration": round(offset, 3), "chapters": [{"title": s["title"], "start": s["start"]} for s in scenes],
            "scope_note": "视频只解释已登记关系、当前状态或原始任务处理；未有可验证结构支持的主题解释保留在文字报告。",
            "transcript": "\n\n".join(transcript), "engine": "espeak-ng/cmn + Pillow + ffmpeg/libx264",
            "generation_seconds": round(time.monotonic() - started, 3), "speech_seconds": round(speech_seconds, 3),
            "encoding_seconds": round(time.monotonic() - encoding_started, 3)}


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
        job = digest(["home-report-media-v4", report["version"]])
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
