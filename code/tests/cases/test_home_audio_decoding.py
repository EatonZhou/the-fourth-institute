"""Fixed audio decoding, source integrity and format limits."""
import hashlib
import io
import math
from pathlib import Path
import struct
import subprocess
from types import SimpleNamespace
import wave
import pytest
from management_portal import home_explanation_render as rendering
from management_portal.home_explanation_plan import validate
PACKAGE = 'workspace_0123456789ab/file/explanation/'


def tone(seconds=2, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as stream:
        stream.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        stream.writeframes(b''.join(struct.pack('<h', int(4000*math.sin(i*2*math.pi*440/rate)))
                                  for i in range(round(seconds*rate))))
    return buf.getvalue()


def test_real_mp3_decode_and_movie_keep_original_hash(tmp_path):
    tools = rendering.capabilities()
    assert tools.get('ffmpeg') and Path(tools['font']).is_file(), 'configured media dependencies required'
    source_wav, source_mp3 = tmp_path/'fixture.wav', tmp_path/'fixture.mp3'
    source_wav.write_bytes(tone())
    subprocess.run([tools['ffmpeg'], '-nostdin', '-y', '-loglevel', 'error', '-i', str(source_wav),
                    '-c:a', 'libmp3lame', '-b:a', '64k', str(source_mp3)], check=True, timeout=30)
    audio = source_mp3.read_bytes()
    output = tmp_path/'render'
    output.mkdir()
    plan = {'schema': 'portal-explanation-video/v1', 'title': 'Synthetic fixture',
            'audio': {'address': PACKAGE+'voice.mp3', 'sha256': hashlib.sha256(audio).hexdigest()},
            'nodes': [{'id': 'source', 'label': 'Source', 'x': .2, 'y': .3},
                      {'id': 'target', 'label': 'Target', 'x': .8, 'y': .5}],
            'cues': [{'start': 0, 'end': 1.8, 'text': 'Synthetic audio fixture'}],
            'scenes': [{'title': 'Fixture', 'speech': 'Synthetic audio fixture', 'start': 0, 'end': 1.8,
                        'edges': [{'from': 'source', 'to': 'target', 'weight': .8}], 'values': []}]}
    result = rendering.render(validate(plan), output, audio)
    assert result['duration'] == pytest.approx(2, abs=.05)
    assert result['audio_decoding']['source_sha256'] == hashlib.sha256(audio).hexdigest()
    assert result['audio_decoding']['source_media_type'] == 'audio/mpeg'
    assert (output/'narration-source.mp3').read_bytes() == audio
    decoded = (output/'narration.wav').read_bytes()
    assert hashlib.sha256(decoded).hexdigest() != result['audio_decoding']['source_sha256']
    with wave.open(io.BytesIO(decoded)) as stream:
        assert (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) == (1, 2, 24000)
        assert any(stream.readframes(stream.getnframes()))
    assert (output/'video.mp4').stat().st_size > 1000
    subprocess.run([tools['ffmpeg'], '-nostdin', '-loglevel', 'error', '-i', str(output/'video.mp4'),
                    '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], check=True, timeout=30)


def test_original_pcm_needs_no_decoder(tmp_path, monkeypatch):
    monkeypatch.setattr(rendering.subprocess, 'run', lambda *a, **kw: pytest.fail('PCM must not use a decoder'))
    audio = tone(.1)
    path, duration, detail = rendering.prepare_audio(audio, tmp_path, '/unused')
    assert path.read_bytes() == audio and duration == .1 and detail['decoding'] == 'original-pcm16'


def test_invalid_mp3_is_rejected_by_fixed_demuxer(tmp_path):
    with pytest.raises(ValueError, match='MP3解码失败'):
        rendering.prepare_audio(b'not an audio container', tmp_path, rendering.capabilities()['ffmpeg'])


@pytest.mark.parametrize('failure', ['timeout', 'too-long'])
def test_decode_time_and_duration_are_bounded(tmp_path, monkeypatch, failure):
    def run(args, **kwargs):
        assert kwargs['timeout'] == 30
        assert args[args.index('-protocol_whitelist')+1] == 'file,pipe'
        assert args[args.index('-f')+1] == 'mp3'
        assert args[args.index('-t')+1] == '301'
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args, 30)
        with wave.open(args[-1], 'wb') as stream:
            stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\0\0'*24000*301)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(rendering.subprocess, 'run', run)
    with pytest.raises((RuntimeError, ValueError), match='超时|五分钟'):
        rendering.prepare_audio(b'fixture mp3', tmp_path, '/fixture-ffmpeg')
