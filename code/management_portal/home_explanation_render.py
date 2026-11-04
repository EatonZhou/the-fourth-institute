"""Trusted continuous node/edge animation renderer for declarative plans."""
from __future__ import annotations

import math
import subprocess
import threading
import time
import wave

from management_portal.home_report_media import capabilities, _run, _stamp, speech_units


def frame(plan, scene, progress, at, fonts):
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (960, 540), '#f5f8ff')
    draw = ImageDraw.Draw(image)
    draw.text((30, 22), plan['title'], font=fonts[24], fill='#172440')
    draw.text((30, 62), scene['title'], font=fonts[20], fill='#52617c')
    nodes = {n['id']: (90 + n['x'] * 780, 140 + n['y'] * 250) for n in plan['nodes']}
    for edge in scene['edges']:
        a, b = nodes[edge['from']], nodes[edge['to']]
        width = max(1, round(1 + edge['weight'] * 8))
        draw.line((a, b), fill='#bac8dc', width=width)
        phase = max(0, min(1, progress * 1.4))
        end = (a[0] + (b[0] - a[0]) * phase, a[1] + (b[1] - a[1]) * phase)
        draw.line((a, end), fill='#527be8', width=width)
        # Moving information parcels continue through the full narrated stage.
        fraction = (at * .35) % 1
        x, y = a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction
        draw.ellipse((x-5, y-5, x+5, y+5), fill='#2ca386')
        if edge['label']:
            draw.text(((a[0]+b[0])/2, (a[1]+b[1])/2-20), edge['label'], font=fonts[16], fill='#29446b', anchor='mm')
    values = {v['node']: v['value'] for v in scene['values']}
    for node in plan['nodes']:
        x, y = nodes[node['id']]
        draw.rounded_rectangle((x-65, y-26, x+65, y+26), radius=12, fill='#ffffff', outline='#6989d0', width=2)
        label = node['label']
        draw.text((x, y), label[:10], font=fonts[20], fill='#243554', anchor='mm')
        if node['id'] in values:
            value = values[node['id']] * min(1, progress * 1.5)
            draw.text((x, y+45), f'{value:.3f}', font=fonts[20], fill='#168465', anchor='mm')
    draw.rounded_rectangle((30, 485, 930, 490), radius=2, fill='#dce5f5')
    draw.rounded_rectangle((30, 485, 30+max(1, 900*progress), 490), radius=2, fill='#527be8')
    return image


def render(plan, directory, audio=None):
    from PIL import ImageFont
    started = time.monotonic()
    tools = capabilities()
    needed = [key for key in ('ffmpeg', 'font') if not tools.get(key)]
    if needed or (audio is None and not tools.get('speech')):
        raise RuntimeError('视频生成依赖未就绪')
    fonts = {size: ImageFont.truetype(tools['font'], size) for size in (16, 20, 24)}
    scenes = [dict(s) for s in plan['scenes']]
    wav_path = directory / 'narration.wav'
    if audio is not None:
        wav_path.write_bytes(audio)
        with wave.open(str(wav_path)) as wav:
            duration = wav.getnframes() / wav.getframerate()
            if wav.getnchannels() not in (1, 2) or wav.getsampwidth() != 2 or wav.getframerate() > 48000 or not 0 < duration <= 300:
                raise ValueError('音轨须为不超过五分钟的PCM16 WAV')
        cues = plan['cues']
        if scenes[-1]['end'] > duration+.05 or cues[-1]['end'] > duration+.05:
            raise ValueError('字幕或阶段时间超出实际音轨')
        voice = '内容作者提供的固定音轨'
    else:
        pcm, cues, offset, rate = [], [], 0.0, None
        for scene in scenes:
            scene['start'] = offset
            for sentence in speech_units(scene['speech']):
                if time.monotonic()-started > 180:
                    raise TimeoutError('配音超过生成时限')
                part = directory / 'part.wav'
                _run([tools['speech'], '-v', 'cmn', '-s', '155', '-w', str(part), '--stdin'], input=sentence.encode())
                with wave.open(str(part)) as wav:
                    if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or (rate and rate != wav.getframerate()):
                        raise ValueError('不支持的配音输出')
                    rate = wav.getframerate()
                    seconds = wav.getnframes()/rate
                    pcm.append(wav.readframes(wav.getnframes()))
                cues.append({'start': offset, 'end': offset+seconds, 'text': sentence})
                offset += seconds+.25
                if offset > 300:
                    raise ValueError('配音超过五分钟限制')
                pcm.append(b'\0'*(round(rate*.25)*2))
            scene['end'] = offset
        duration = offset
        with wave.open(str(wav_path), 'wb') as wav:
            wav.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
            wav.writeframes(b''.join(pcm))
        voice = '本地eSpeak机械配音'
    captions = 'WEBVTT\n\n' + '\n\n'.join(f"{_stamp(c['start'])} --> {_stamp(c['end'])}\n{c['text'].replace('-->', '→')}" for c in cues)
    (directory/'captions.vtt').write_text(captions, encoding='utf-8')
    (directory/'transcript.txt').write_text('\n\n'.join(s['speech'] for s in scenes), encoding='utf-8')
    args = [tools['ffmpeg'], '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '960x540',
            '-r', '12', '-i', 'pipe:0', '-i', str(wav_path), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '24',
            '-pix_fmt', 'yuv420p', '-threads', '2', '-c:a', 'aac', '-shortest', '-movflags', '+faststart',
            '-fs', str(128*1024*1024), str(directory/'video.mp4')]
    with (directory/'encoding.log').open('wb') as log:
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        timer = threading.Timer(300, process.kill)
        timer.start()
        try:
            for index in range(math.ceil(duration*12)):
                at = index/12
                scene = next((s for s in scenes if at < s['end']), scenes[-1])
                progress = min(1, max(0, (at-scene['start'])/max(.01, scene['end']-scene['start'])))
                image = frame(plan, scene, progress, at, fonts)
                if index == 12:
                    image.save(directory/'poster.png')
                process.stdin.write(image.tobytes())
            process.stdin.close()
            if process.wait(timeout=30):
                raise RuntimeError('视频编码失败或超时')
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()
    return {'duration': duration, 'chapters': [{'time': s['start'], 'title': s['title']} for s in scenes], 'voice_note': voice}
