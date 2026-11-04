"""Local media toolchain shared by explanation pages and optional reports.

The bundle location and TFI_HOME_REPORT_* settings are existing deployment
contracts. Resolving or running tools never imports a presentation feature.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def directory(code=ROOT):
    return Path(code).parent / 'dependencies/native/home-report-media-v2'


def executable(name, setting):
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
    managed_font = directory() / 'share/font.ttc'
    font = os.environ.get("TFI_HOME_REPORT_FONT") or (str(managed_font) if managed_font.is_file() else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    if not Path(font).is_file():
        missing.append("CJK font / TFI_HOME_REPORT_FONT")
    return {"available": not missing, "missing": missing, "font": font, **result}


def run(args, **kwargs):
    if Path(args[0]).parent == directory() / 'bin':
        env = dict(os.environ)
        env['LD_LIBRARY_PATH'] = str(directory() / 'lib')
        env['ESPEAK_DATA_PATH'] = str(directory() / 'share')
        kwargs['env'] = env
    result = subprocess.run(args, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, timeout=180, **kwargs)
    if result.returncode:
        raise RuntimeError(f"媒体工具退出 {result.returncode}: {result.stderr.decode(errors='replace')[-600:]}")


def speech_units(value):
    # Punctuation is a natural speech/caption boundary. Keep words intact.
    return re.findall(r"[^。！？，,；;：:]+[。！？，,；;：:]?", value)


def stamp(seconds):
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"
