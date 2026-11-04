"""Bounded data-only animation plans. No code, expressions, paths or URLs execute."""
from __future__ import annotations

import math


def _text(value, limit, required=True):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError('无效或过长的生成计划文字')
    return value


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('生成计划数值超出范围')
    return float(value)


def validate(value):
    if not isinstance(value, dict) or value.get('schema') != 'portal-explanation-video/v1':
        raise ValueError('不支持的视频生成计划')
    if set(value) - {'schema', 'title', 'nodes', 'scenes', 'audio', 'cues', 'audio_provenance'}:
        raise ValueError('生成计划含未声明字段')
    result = {'schema': value['schema'], 'title': _text(value.get('title'), 100)}
    nodes, scenes = value.get('nodes'), value.get('scenes')
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 16 or not isinstance(scenes, list) or not 1 <= len(scenes) <= 12:
        raise ValueError('计划需含1–16个节点、1–12个阶段')
    ids = set()
    result['nodes'] = []
    for node in nodes:
        if not isinstance(node, dict) or set(node) - {'id', 'label', 'x', 'y'}:
            raise ValueError('无效节点')
        key = _text(node.get('id'), 40)
        if key in ids:
            raise ValueError('节点标识重复')
        ids.add(key)
        result['nodes'].append({'id': key, 'label': _text(node.get('label'), 40),
                                'x': _number(node.get('x'), 0, 1), 'y': _number(node.get('y'), 0, 1)})
    result['scenes'] = []
    for scene in scenes:
        if not isinstance(scene, dict) or set(scene) - {'title', 'speech', 'duration', 'edges', 'values', 'start', 'end'}:
            raise ValueError('无效阶段')
        edges, values = scene.get('edges', []), scene.get('values', [])
        if not isinstance(edges, list) or len(edges) > 32 or not isinstance(values, list) or len(values) > 16:
            raise ValueError('阶段内容过多')
        output = {'title': _text(scene.get('title'), 80), 'speech': _text(scene.get('speech'), 500),
                  'duration': _number(scene.get('duration', 8), 2, 60), 'edges': [], 'values': []}
        for edge in edges:
            if not isinstance(edge, dict) or set(edge) - {'from', 'to', 'weight', 'label'} or edge.get('from') not in ids or edge.get('to') not in ids:
                raise ValueError('连线必须引用已有节点')
            output['edges'].append({'from': edge['from'], 'to': edge['to'], 'weight': _number(edge.get('weight', 1), 0, 1),
                                    'label': _text(edge.get('label', ''), 40, False)})
        for item in values:
            if not isinstance(item, dict) or set(item) - {'node', 'value'} or item.get('node') not in ids:
                raise ValueError('数值必须引用已有节点')
            output['values'].append({'node': item['node'], 'value': _number(item.get('value'), -1000000, 1000000)})
        if 'audio' in value:
            output['start'] = _number(scene.get('start'), 0, 300)
            output['end'] = _number(scene.get('end'), 0, 300)
            if output['end'] <= output['start'] or (result['scenes'] and output['start'] < result['scenes'][-1]['end']):
                raise ValueError('音轨阶段时间无效或重叠')
        result['scenes'].append(output)
    if sum(s['duration'] for s in result['scenes']) > 300 or sum(len(s['speech']) for s in result['scenes']) > 2500:
        raise ValueError('计划超过五分钟或讲稿长度限制')
    if 'audio' in value:
        audio = value['audio']
        if not isinstance(audio, dict) or set(audio) != {'address', 'sha256'}:
            raise ValueError('音轨需要固定地址与SHA256')
        result['audio'] = audio
        cues = value.get('cues')
        if not isinstance(cues, list) or not 1 <= len(cues) <= 250:
            raise ValueError('音轨需要逐句字幕时间')
        result['cues'] = []
        for cue in cues:
            if not isinstance(cue, dict) or set(cue) != {'start', 'end', 'text'}:
                raise ValueError('无效字幕')
            start, end = _number(cue['start'], 0, 300), _number(cue['end'], 0, 300)
            if end <= start or (result['cues'] and start < result['cues'][-1]['end']):
                raise ValueError('字幕时间无效或重叠')
            result['cues'].append({'start': start, 'end': end, 'text': _text(cue['text'], 250)})
    elif 'cues' in value:
        raise ValueError('字幕时间须配合固定音轨')
    if 'audio_provenance' in value:
        proof = value['audio_provenance']
        if not isinstance(proof, dict) or set(proof) != {'address', 'sha256'} or 'audio' not in value:
            raise ValueError('原生音轨来源需要音轨及固定记录引用')
        result['audio_provenance'] = proof
    return result
