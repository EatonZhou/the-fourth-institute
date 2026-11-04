"""New-generation audio gate; author-written provenance is not provider proof.

Legacy ready media bypasses this gate. The installed execution contract currently
has no native audio generation/receipt capability. Until an authoritative receipt
reader is supplied by that owner, even well-formed claims fail closed here.
"""
import json


def verify_native_receipt(record, actor):
    # No external URL fetches, local TTS, secrets or author-provided commands.
    # Do not replace this with a check of a caller-writable 'verified' flag.
    raise ValueError('当前尚无可核验的Provider原生音频调用能力；不能生成视频，也不会回退本地配音。')


def require_native_audio(plan, read, same_package, actor):
    if not plan.get('audio') or not plan.get('audio_provenance'):
        raise ValueError('新视频需要Provider原生生成的音轨及可核验来源记录；不会自动使用本地配音。')
    same_package(plan['audio_provenance'])
    record = json.loads(read(plan['audio_provenance'], actor))
    if not isinstance(record, dict) or set(record) != {'schema', 'provider', 'model', 'request_id', 'audio', 'evidence'}:
        raise ValueError('原生音轨来源记录字段无效')
    if record['schema'] != 'portal-native-audio-provenance/v1':
        raise ValueError('不支持的原生音轨来源记录')
    if any(not isinstance(record[k], str) or not record[k].strip() or len(record[k]) > 200
           for k in ('provider', 'model', 'request_id')):
        raise ValueError('原生音轨来源必须列明实际Provider、模型与请求ID')
    if record['audio'] != plan['audio']:
        raise ValueError('原生音轨来源记录与本次固定音轨不符')
    same_package(record['evidence'])
    # Read/hash/ACL checks establish which evidence was supplied, not its truth.
    read(record['evidence'], actor)
    verify_native_receipt(record, actor)
    return record
