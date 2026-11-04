"""Optional tool provenance, historical audio audit and MP3 decoding.

These checks make no cloud requests and do not certify a production voice/video.
"""
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import subprocess
from types import SimpleNamespace
import wave

import pytest

from management_portal import home_explanation_audio as gate
from management_portal import home_explanation_render as rendering
from management_portal.home_explanation_plan import validate
from management_portal import media_audio_receipts as speech


PACKAGE = 'workspace_0123456789ab/file/explanation/'
EXECUTION = 'execution-' + 'a' * 24
ORIGINAL = '/api/uploads/fixture-original-speech'
ACTOR = 'fixture-consumer'


@pytest.fixture
def provenance(tmp_path, monkeypatch):
    from management_portal.resources.service import ResourceService
    monkeypatch.setattr(speech, 'execution_state_root', lambda: tmp_path/'execution')
    blobs, denied, reads = {}, set(), []
    source = b'fixture-original-audio-bytes'
    def put(name, value):
        data = json.dumps(value).encode() if isinstance(value, dict) else value
        ref = {'address': PACKAGE+name, 'sha256': hashlib.sha256(data).hexdigest()}
        blobs[ref['address']] = data
        return ref
    audio = put('voice.mp3', source)
    receipt = {'schema': 'tfi.native-speech-receipt.v1', 'execution_id': EXECUTION,
               'provider': 'microsoft', 'model': 'edge-neural-tts', 'api_id': 'microsoft-edge-tts',
               'media_type': 'audio/mpeg', 'source': 'Microsoft Edge online speech / edge-tts',
               'model_identity_kind': 'service_engine_alias',
               'audio': {'address': ORIGINAL, 'sha256': audio['sha256']}}
    state = {'actor': 'fixture-author', 'status': 'completed', 'receipt': receipt}
    record = {'schema': 'portal-native-audio-provenance/v1', 'provider': 'microsoft',
              'model': 'edge-neural-tts', 'request_id': EXECUTION, 'audio': audio,
              'evidence': put('receipt.json', receipt)}
    plan = {'audio': audio}
    def save():
        path = speech._path(EXECUTION)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
        plan['audio_provenance'] = put('audio-source.json', record)
    save()
    original = {'bytes': source, 'denied': False}
    def read_original(self, address, *, actor, max_bytes):
        reads.append(('original', address, actor))
        assert actor == ACTOR and address == ORIGINAL
        if original['denied']:
            raise PermissionError('original revoked')
        return original['bytes'], 'audio/mpeg', {}
    monkeypatch.setattr(ResourceService, 'read_content', read_original)
    def read(ref, actor, max_bytes=1024*1024):
        reads.append(('package', ref['address'], actor))
        assert actor == ACTOR
        if ref['address'] in denied:
            raise PermissionError('package revoked')
        data = blobs[ref['address']]
        if len(data) > max_bytes or hashlib.sha256(data).hexdigest() != ref['sha256']:
            raise ValueError('fixed package hash mismatch')
        return data
    def same_package(ref):
        if not ref['address'].startswith(PACKAGE):
            raise ValueError('outside package')
    def check():
        return gate.require_audio_provenance(plan, read, same_package, ACTOR)
    return SimpleNamespace(**locals())


def test_authoritative_receipt_and_original_copy_use_actual_consumer(provenance):
    f = provenance
    assert f.check() == f.record
    assert ('original', ORIGINAL, ACTOR) in f.reads
    assert ('package', f.audio['address'], ACTOR) in f.reads
    # A saved author JSON cannot upgrade an unfinished/absent execution.
    f.state['status'] = 'dispatched'
    f.save()
    with pytest.raises(ValueError, match='no completed receipt'):
        f.check()
    speech._path(EXECUTION).unlink()
    with pytest.raises(ValueError, match='not found'):
        f.check()


def test_optional_tool_audio_needs_no_execution_receipt(provenance):
    f = provenance
    speech._path(EXECUTION).unlink()
    f.record.update(schema='portal-audio-provenance/v1', request_id='edge-speech-fixture')
    report = {'schema': 'resident-tool-audio/v1', 'run_id': 'edge-speech-fixture',
              'provider': 'microsoft', 'model': 'edge-neural-tts',
              'audio': {'sha256': f.audio['sha256']}}
    f.record['evidence'] = f.put('tool-report.json', report)
    f.plan['audio_provenance'] = f.put('audio-source.json', f.record)
    assert f.check()['schema'] == 'portal-audio-provenance/v1'
    assert not any(row[0] == 'original' for row in f.reads)
    # Tool provenance remains a claim, even when pinned and internally consistent.
    f.record['schema'] = 'portal-native-audio-provenance/v1'
    f.plan['audio_provenance'] = f.put('audio-source.json', f.record)
    with pytest.raises(ValueError, match='Invalid speech execution ID'):
        f.check()
    f.record['schema'] = 'portal-audio-provenance/v1'
    report['audio']['sha256'] = '0'*64
    f.record['evidence'] = f.put('tool-report.json', report)
    f.plan['audio_provenance'] = f.put('audio-source.json', f.record)
    with pytest.raises(ValueError, match='不一致'):
        f.check()
    report['audio']['sha256'] = f.audio['sha256']
    f.record['evidence'] = f.put('tool-report.json', report)
    f.plan['audio_provenance'] = f.put('audio-source.json', f.record)
    f.denied.add(f.record['evidence']['address'])
    with pytest.raises(PermissionError):
        f.check()


@pytest.mark.parametrize('field,value,error', [
    ('provider', 'microsoft-edge-tts', 'Provider'),
    ('model', 'invented-model-version', '模型'),
    ('schema', 'author-claimed-receipt', '请求ID'),
    ('execution_id', 'execution-'+'b'*24, '请求ID'),
    ('media_type', 'text/plain', '格式'),
])
def test_receipt_identity_and_supported_format(provenance, field, value, error):
    f = provenance
    f.receipt[field] = value
    f.save()
    with pytest.raises(ValueError, match=error):
        f.check()


@pytest.mark.parametrize('target', ['original', 'package', 'evidence'])
def test_both_source_acl_and_package_acl_are_required(provenance, target):
    f = provenance
    if target == 'original':
        f.original['denied'] = True
    else:
        f.denied.add(f.audio['address'] if target == 'package' else f.record['evidence']['address'])
    with pytest.raises(PermissionError):
        f.check()


def test_reencoding_or_changed_bytes_cannot_borrow_original_receipt(provenance):
    f = provenance
    f.original['bytes'] = b'changed-original'
    with pytest.raises(ValueError, match='does not match'):
        f.check()
    f.original['bytes'] = f.source
    f.blobs[f.audio['address']] = b'converted-wav'
    with pytest.raises(ValueError, match='hash mismatch'):
        f.check()
    # Even if the author repins the converted bytes, the execution SHA differs.
    f.audio['sha256'] = hashlib.sha256(b'converted-wav').hexdigest()
    f.save()
    with pytest.raises(ValueError, match='原始音频SHA256'):
        f.check()


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
