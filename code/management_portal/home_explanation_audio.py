"""Audio package integrity; synthesis is an optional Resident tool concern.

Tool reports describe source claims, not Portal/vendor attestations. Historical
server receipts remain readable for the media already produced under that contract.
"""
import hashlib
import json


def verify_legacy_receipt(record, actor):
    from management_portal.media_audio_receipts import verified_receipt
    # This owner re-reads the immutable execution record, authorizes the original
    # upload for THIS consumer, and hashes its bytes. Never impersonate its author.
    receipt = verified_receipt(record['request_id'], actor=actor)
    if receipt.get('schema') != 'tfi.native-speech-receipt.v1' or receipt.get('execution_id') != record['request_id']:
        raise ValueError('语音执行回执与请求ID不符')
    if receipt.get('provider') != record['provider'] or receipt.get('model') != record['model']:
        raise ValueError('语音来源的Provider或模型与正式执行回执不符')
    audio = receipt.get('audio')
    if not isinstance(audio, dict) or audio.get('sha256') != record['audio'].get('sha256'):
        raise ValueError('包内音轨必须与正式执行的原始音频SHA256一致，不接受作者转换后的替代文件')
    if receipt.get('media_type') not in {'audio/mpeg', 'audio/wav', 'audio/x-wav'}:
        raise ValueError('正式语音回执的音频格式暂不支持')
    return receipt


def require_audio_provenance(plan, read, same_package, actor):
    if not plan.get('audio') or not plan.get('audio_provenance'):
        raise ValueError('新视频需要固定音轨及来源记录；配音由可选工具提供，Portal不会自动生成。')
    same_package(plan['audio_provenance'])
    record = json.loads(read(plan['audio_provenance'], actor))
    if not isinstance(record, dict) or set(record) != {'schema', 'provider', 'model', 'request_id', 'audio', 'evidence'}:
        raise ValueError('音轨来源记录字段无效')
    if record['schema'] not in {'portal-native-audio-provenance/v1', 'portal-audio-provenance/v1'}:
        raise ValueError('不支持的音轨来源记录')
    if any(not isinstance(record[k], str) or not record[k].strip() or len(record[k]) > 200
           for k in ('provider', 'model', 'request_id')):
        raise ValueError('音轨来源必须列明实际Provider、模型与请求ID')
    if record['audio'] != plan['audio']:
        raise ValueError('音轨来源记录与本次固定音轨不符')
    same_package(plan['audio'])
    same_package(record['evidence'])
    # Read/hash/ACL checks establish which evidence was supplied, not its truth.
    evidence = read(record['evidence'], actor)
    if record['schema'] == 'portal-native-audio-provenance/v1':
        receipt = verify_legacy_receipt(record, actor)
    else:
        report = json.loads(evidence)
        if (not isinstance(report, dict) or report.get('schema') != 'resident-tool-audio/v1'
                or report.get('run_id') != record['request_id']
                or report.get('provider') != record['provider'] or report.get('model') != record['model']
                or not isinstance(report.get('audio'), dict)
                or report['audio'].get('sha256') != record['audio'].get('sha256')):
            raise ValueError('工具来源记录与固定音轨不一致')
        # Integrity only. A tool/author JSON is not a server or vendor attestation.
        receipt = report
    # Hash package bytes under the consumer's ACL. Historical receipts also
    # require the original upload's ACL; tool reports carry no such attestation.
    data = read(plan['audio'], actor, 64*1024*1024)
    if hashlib.sha256(data).hexdigest() != receipt['audio']['sha256']:
        raise ValueError('包内音轨字节与来源记录不符')
    return record
