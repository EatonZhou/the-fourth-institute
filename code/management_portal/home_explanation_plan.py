"""Bounded data-only animation plans. No code, expressions, paths or URLs execute."""
from __future__ import annotations

import math


VISUAL_STAGES = {
    'sequence': {'focus'}, 'projection': {'input', 'multiply', 'result'},
    'dot': {'vectors', 'products', 'sum', 'result', 'scores'},
    'normalize': {'scores', 'mask', 'exp', 'denominator', 'weights'},
    'weighted_sum': {'vector', 'scaled', 'accumulate', 'result'},
    'compare': {'before', 'change', 'after'}, 'matrix': {'row', 'all', 'result'},
    'pipeline': {'lanes', 'concat', 'project', 'residual', 'context'},
}


def _object(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError('视觉声明含缺失或未声明字段')
    return value


def _items(value, limit=6):
    if not isinstance(value, list) or not 1 <= len(value) <= limit:
        raise ValueError('视觉对象数量超限')
    return value


def _integer(value, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError('视觉索引超出范围')
    return value


def _vector(value, width=2):
    if not isinstance(value, list) or len(value) != width:
        raise ValueError('视觉向量维度不匹配')
    return [_number(v, -100, 100) for v in value]


def _identified(items, fields):
    result, ids = [], set()
    for item in _items(items):
        _object(item, {'id', 'label', *fields})
        key = _text(item['id'], 32)
        if key in ids:
            raise ValueError('视觉对象标识重复')
        ids.add(key)
        result.append({'id': key, 'label': _text(item['label'], 16), **{k: f(item[k]) for k, f in fields.items()}})
    return result


def _condition(value, count):
    _object(value, {'label', 'query', 'masked', 'temperature'})
    masked = value['masked']
    if not isinstance(masked, list) or len(masked) != count or any(type(v) is not bool for v in masked) or all(masked):
        raise ValueError('需明确每项掩码，且保留至少一项')
    return {'label': _text(value['label'], 32), 'query': _vector(value['query']),
            'masked': masked, 'temperature': _number(value['temperature'], .1, 10)}


def validate_visual(value, start, end, cues):
    """Closed mathematical operations and cue-bound events, never author code.

    Results are derived by visual_math, not accepted as claimed output values.
    An explicit null cue is a silent reading beat, not an inferred audio timing.
    """
    _object(value, {'kind', 'condition', 'data', 'events'})
    kind = value['kind']
    if not isinstance(kind, str) or kind not in VISUAL_STAGES:
        raise ValueError('不支持的声明式视觉操作')
    data = value['data']
    if kind == 'dot':
        _object(data, {'query_label', 'query', 'items', 'divisor'})
        data = {'query_label': _text(data['query_label'], 16), 'query': _vector(data['query']),
                'items': _identified(data['items'], {'vector': _vector}), 'divisor': _number(data['divisor'], .1, 100)}
    elif kind == 'normalize':
        _object(data, {'items', 'temperature'})
        def mask(v):
            if type(v) is not bool:
                raise ValueError('掩码须为布尔值')
            return v
        data = {'items': _identified(data['items'], {'score': lambda v: _number(v, -50, 50), 'masked': mask}),
                'temperature': _number(data['temperature'], .1, 10)}
        if all(v['masked'] for v in data['items']):
            raise ValueError('归一化必须至少保留一项')
    elif kind == 'weighted_sum':
        _object(data, {'items'})
        data = {'items': _identified(data['items'], {'vector': _vector, 'weight': lambda v: _number(v, 0, 1)})}
        if not math.isclose(sum(v['weight'] for v in data['items']), 1, abs_tol=1e-9):
            raise ValueError('加权向量需要总和为1的权重')
    elif kind == 'compare':
        _object(data, {'items', 'before', 'after', 'divisor', 'change'})
        items = _identified(data['items'], {'key': _vector, 'vector': _vector})
        before, after = (_condition(data[k], len(items)) for k in ('before', 'after'))
        change = data['change']
        if not isinstance(change, str) or change not in {'query', 'masked', 'temperature'} or before[change] == after[change]:
            raise ValueError('对照必须声明实际改变的一项条件')
        if any(before[k] != after[k] for k in ('query', 'masked', 'temperature') if k != change):
            raise ValueError('对照只允许改变一个条件')
        data = {'items': items, 'before': before, 'after': after, 'change': change, 'divisor': _number(data['divisor'], .1, 100)}
    elif kind == 'matrix':
        _object(data, {'rows', 'items', 'divisor', 'causal'})
        rows = _identified(data['rows'], {'vector': _vector})
        items = _identified(data['items'], {'key': _vector, 'vector': _vector})
        if type(data['causal']) is not bool or (data['causal'] and len(rows) != len(items)):
            raise ValueError('因果矩阵需要相同位置轴')
        data = {'rows': rows, 'items': items, 'divisor': _number(data['divisor'], .1, 100), 'causal': data['causal']}
    elif kind == 'projection':
        _object(data, {'input_label', 'input', 'lanes'})
        vector = _vector(data['input'], len(_items(data['input'])))
        def matrix(v):
            if not isinstance(v, list) or len(v) != len(vector):
                raise ValueError('投影矩阵与输入维数不符')
            return [_vector(row) for row in v]
        lanes = _identified(data['lanes'], {'matrix': matrix})
        if len(lanes) > 3:
            raise ValueError('单镜头最多三条投影通道')
        data = {'input_label': _text(data['input_label'], 16), 'input': vector, 'lanes': lanes}
    elif kind == 'pipeline':
        _object(data, {'input_label', 'input_width', 'lane_width', 'lanes', 'merge_label', 'projection_label', 'output_label', 'context'})
        lanes = _identified(data['lanes'], {'detail': lambda v: _text(v, 32)})
        if len(lanes) > 3:
            raise ValueError('单镜头最多三条符号通道')
        data = {**{k: _text(data[k], 20) for k in ('input_label', 'merge_label', 'projection_label', 'output_label')},
                **{k: _integer(data[k], 1, 4096) for k in ('input_width', 'lane_width')}, 'lanes': lanes,
                'context': [_text(v, 16) for v in _items(data['context'], 5)]}
    else:
        _object(data, {'items', 'note'})
        data = {'items': _identified(data['items'], {'detail': lambda v: _text(v, 56)}), 'note': _text(data['note'], 100)}
    count = len(data['rows']) if kind == 'matrix' else len(data.get('items', data.get('lanes', [])))
    events = []
    for event in _items(value['events'], 48):
        _object(event, {'at', 'stage', 'index', 'duration', 'cue'})
        at = _number(event['at'], start, end)
        duration = _number(event['duration'], 0, 3)
        if not isinstance(event['stage'], str) or event['stage'] not in VISUAL_STAGES[kind] or at + duration > end:
            raise ValueError('视觉阶段或时间无效')
        index = _integer(event['index'], 0, max(0, count-1))
        if events and (at <= events[-1]['at'] or at < events[-1]['at']+events[-1]['duration']):
            raise ValueError('视觉事件时间重叠或倒序')
        cue = event['cue']
        if cue is not None:
            _integer(cue, 0, len(cues)-1)
            if not cues[cue]['start'] <= at < cues[cue]['end']:
                raise ValueError('视觉事件未落在指定真实字幕时间内')
        elif any(at < c['end'] and at+max(duration, .001) > c['start'] for c in cues):
            raise ValueError('静默事件不能占用配音字幕时间')
        events.append({'at': at, 'stage': event['stage'], 'index': index, 'duration': duration, 'cue': cue})
    return {'kind': kind, 'condition': _text(value['condition'], 80), 'data': data, 'events': events}


def _normalize(scores, masked, temperature=1):
    scaled = [s/temperature for s in scores]
    # Subtracting a common maximum preserves ratios and avoids overflow.
    shift = max(0, max(s for s, m in zip(scaled, masked) if not m)-40)
    exponentials = [0.0 if m else math.exp(s-shift) for s, m in zip(scaled, masked)]
    denominator = math.fsum(exponentials)
    return {'scores': scores, 'exponentials': exponentials, 'denominator': denominator,
            'weights': [v/denominator for v in exponentials], 'shift': shift, 'masked': masked}


def _weighted(vectors, weights):
    contributions = [[w*v for v in vector] for vector, w in zip(vectors, weights)]
    partials = [[math.fsum(c[d] for c in contributions[:i+1]) for d in range(2)] for i in range(len(vectors))]
    return {'contributions': contributions, 'partials': partials, 'output': partials[-1]}


def visual_math(visual):
    """One numerical owner for diagrams, equations and direct verification."""
    kind, d = visual['kind'], visual['data']
    if kind == 'dot':
        products = [[q*k for q, k in zip(d['query'], v['vector'])] for v in d['items']]
        return {'products': products, 'scores': [math.fsum(v)/d['divisor'] for v in products]}
    if kind == 'normalize':
        return _normalize([v['score'] for v in d['items']], [v['masked'] for v in d['items']], d['temperature'])
    if kind == 'weighted_sum':
        return _weighted([v['vector'] for v in d['items']], [v['weight'] for v in d['items']])
    if kind in {'compare', 'matrix'}:
        conditions = [d['before'], d['after']] if kind == 'compare' else [
            {'query': r['vector'], 'masked': [d['causal'] and j > i for j in range(len(d['items']))], 'temperature': 1}
            for i, r in enumerate(d['rows'])]
        results = []
        for c in conditions:
            scores = [math.fsum(q*k for q, k in zip(c['query'], v['key']))/d['divisor'] for v in d['items']]
            norm = _normalize(scores, c['masked'], c['temperature'])
            results.append({**norm, **_weighted([v['vector'] for v in d['items']], norm['weights'])})
        return {'cases' if kind == 'compare' else 'rows': results}
    if kind == 'projection':
        return {'outputs': [[math.fsum(x*row[j] for x, row in zip(d['input'], lane['matrix'])) for j in range(2)] for lane in d['lanes']]}
    return {}


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
    nodes, scenes = value.get('nodes', []), value.get('scenes')
    if not isinstance(nodes, list) or len(nodes) > 16 or not isinstance(scenes, list) or not 1 <= len(scenes) <= 24:
        raise ValueError('计划最多16个节点、1–24个阶段')
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
        if not isinstance(scene, dict) or set(scene) - {'title', 'speech', 'duration', 'edges', 'values', 'start', 'end', 'visual'}:
            raise ValueError('无效阶段')
        if not nodes and 'visual' not in scene:
            raise ValueError('阶段需要声明式视觉或已有节点')
        if 'visual' in scene and (scene.get('edges') or scene.get('values')):
            raise ValueError('声明式视觉不能混用旧连线动画')
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
        if 'audio' in value or 'visual' in scene:
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
    for source, scene in zip(scenes, result['scenes']):
        if 'visual' in source:
            scene['visual'] = validate_visual(source['visual'], scene['start'], scene['end'], result.get('cues', []))
    return result
