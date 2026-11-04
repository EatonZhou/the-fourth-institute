"""Trusted continuous node/edge animation renderer for declarative plans."""
from __future__ import annotations

import math
import subprocess
import threading
import wave

from management_portal.media_tools import capabilities, stamp


def _fit(draw, text, font, width):
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + '…', font=font) > width:
        text = text[:-1]
    return text + '…'


def _endpoint(a, b, box):
    """Intersect a centre-to-centre line with the source card's boundary."""
    dx, dy = b[0]-a[0], b[1]-a[1]
    scale = min(box[0]/abs(dx) if dx else math.inf,
                box[1]/abs(dy) if dy else math.inf)
    if not math.isfinite(scale):
        return a
    return a[0]+dx*scale, a[1]+dy*scale


def prepare_frame(plan, scene, fonts):
    """Lay out the static stage once; animation adds only its active connection."""
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (960, 540), '#f5f8ff')
    draw = ImageDraw.Draw(image)
    draw.text((30, 22), _fit(draw, plan['title'], fonts[24], 900), font=fonts[24], fill='#172440')
    draw.text((30, 62), scene['title'], font=fonts[20], fill='#52617c')
    nodes = {n['id']: (120 + n['x'] * 720, 112 + n['y'] * 332) for n in plan['nodes']}
    boxes = {}
    for key, (x, y) in nodes.items():
        heights = [(abs(y-oy)-10)/2 for other, (ox, oy) in nodes.items()
                   if key != other and abs(x-ox) < 188 and y != oy]
        widths = [(abs(x-ox)-16)/2 for other, (ox, oy) in nodes.items()
                  if key != other and abs(y-oy) < 48 and x != ox]
        boxes[key] = (max(40, min([94] + widths)), max(12, min([24] + heights)))
    connections = []
    for edge in scene['edges']:
        a, b = nodes[edge['from']], nodes[edge['to']]
        start, end = _endpoint(a, b, boxes[edge['from']]), _endpoint(b, a, boxes[edge['to']])
        width = max(1, round(1 + edge['weight'] * 7))
        if edge['weight'] > 0:
            draw.line((start, end), fill='#b6c6e1', width=width)
            connections.append((edge, start, end, width))
        else:
            # A masked/zero-weight connection has no moving information parcel.
            distance = math.dist(start, end)
            for offset in range(0, math.ceil(distance), 12):
                lo, hi = offset/max(1, distance), min(1, (offset+5)/max(1, distance))
                draw.line(((start[0]+(end[0]-start[0])*lo, start[1]+(end[1]-start[1])*lo),
                           (start[0]+(end[0]-start[0])*hi, start[1]+(end[1]-start[1])*hi)), fill='#cbd2df', width=1)
    values = {v['node']: v['value'] for v in scene['values']}
    for node in plan['nodes']:
        x, y = nodes[node['id']]
        w, h = boxes[node['id']]
        font = fonts[20 if h >= 22 else 16]
        value = f"{values[node['id']]:.3f}" if node['id'] in values else ''
        value_width = draw.textlength(value, font=font) + 14 if value else 0
        label = _fit(draw, node['label'], font, max(12, 2*w-24-value_width))
        draw.rounded_rectangle((x-w, y-h, x+w, y+h), radius=9, fill='#ffffff', outline='#c0cde2', width=1)
        draw.text((x-w+12, y), label, font=font, fill='#243554', anchor='lm')
        if value:
            draw.text((x+w-12, y), value, font=font, fill='#168465', anchor='rm')
    draw.text((30, 510), '连线粗细表示权重 · 高亮依次说明连接 · 虚线不传递信息', font=fonts[16], fill='#6d7c95')
    return image, connections


def frame(plan, scene, progress, at, fonts, prepared=None):
    from PIL import ImageDraw
    base, connections = prepared or prepare_frame(plan, scene, fonts)
    image = base.copy()
    draw = ImageDraw.Draw(image)
    if connections:
        phase = max(0, at-scene.get('start', 0)) / 1.6
        edge, a, b, width = connections[int(phase) % len(connections)]
        draw.line((a, b), fill='#547fe5', width=width)
        fraction = phase % 1
        x, y = a[0]+(b[0]-a[0])*fraction, a[1]+(b[1]-a[1])*fraction
        draw.ellipse((x-5, y-5, x+5, y+5), fill='#159b7a')
        labels = {n['id']: n['label'] for n in plan['nodes']}
        detail = f"{labels[edge['from']]} → {labels[edge['to']]}"
        if edge['label']:
            detail += '    ' + edge['label']
        draw.text((30, 462), _fit(draw, detail, fonts[16], 900), font=fonts[16], fill='#29446b')
    draw.rounded_rectangle((30, 485, 930, 490), radius=2, fill='#dce5f5')
    draw.rounded_rectangle((30, 485, 30+max(1, 900*min(1, max(0, progress))), 490), radius=2, fill='#527be8')
    return image


def render(plan, directory, audio=None):
    from PIL import ImageFont
    if audio is None:
        raise ValueError('视频需要已验证的Provider原生音轨；不支持本地配音回退')
    tools = capabilities()
    needed = [key for key in ('ffmpeg', 'font') if not tools.get(key)]
    if needed:
        raise RuntimeError('视频生成依赖未就绪')
    fonts = {size: ImageFont.truetype(tools['font'], size) for size in (16, 20, 24)}
    scenes = [dict(s) for s in plan['scenes']]
    wav_path = directory / 'narration.wav'
    wav_path.write_bytes(audio)
    with wave.open(str(wav_path)) as wav:
        duration = wav.getnframes() / wav.getframerate()
        if wav.getnchannels() not in (1, 2) or wav.getsampwidth() != 2 or wav.getframerate() > 48000 or not 0 < duration <= 300:
            raise ValueError('音轨须为不超过五分钟的PCM16 WAV')
    cues = plan['cues']
    if scenes[-1]['end'] > duration+.05 or cues[-1]['end'] > duration+.05:
        raise ValueError('字幕或阶段时间超出实际音轨')
    voice = '固定音轨；原生来源核验由生成入口负责'
    captions = 'WEBVTT\n\n' + '\n\n'.join(f"{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text'].replace('-->', '→')}" for c in cues)
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
            prepared_scene, prepared = None, None
            for index in range(math.ceil(duration*12)):
                at = index/12
                scene = next((s for s in scenes if at < s['end']), scenes[-1])
                progress = min(1, max(0, (at-scene['start'])/max(.01, scene['end']-scene['start'])))
                if scene is not prepared_scene:
                    prepared_scene, prepared = scene, prepare_frame(plan, scene, fonts)
                image = frame(plan, scene, progress, at, fonts, prepared)
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
