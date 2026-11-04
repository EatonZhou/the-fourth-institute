"""Trusted continuous node/edge animation renderer for declarative plans."""
from __future__ import annotations

import math
import hashlib
import subprocess
import threading
import wave

from management_portal.media_tools import capabilities, stamp
from management_portal.home_explanation_plan import visual_math


_BG, _INK, _MUTED = '#111824', '#f4f2e9', '#9caabc'
_Q, _K, _V = '#f2b65e', '#64c4c2', '#c5a5ef'
_COLORS = [_Q, _K, _V, '#ed9696', '#9dbef7', '#afd58f']


def _num(v):
    return f'{v:.4f}'.rstrip('0').rstrip('.') if abs(v) < 1000 else f'{v:.3g}'


def _vec(v):
    return '(' + ', '.join(_num(x) for x in v) + ')'


def visual_event(visual, at):
    """An event changes only at its authored time; elapsed time never cycles focus."""
    event = next((e for e in reversed(visual['events']) if e['at'] <= at), None)
    if event is None:
        return None, 0
    fraction = min(1, max(0, (at-event['at'])/event['duration'])) if event['duration'] else 1
    return event, fraction*fraction*(3-2*fraction)


class _Canvas:
    def __init__(self, image, fonts):
        from PIL import ImageDraw
        self.draw, self.fonts = ImageDraw.Draw(image), fonts

    def text(self, xy, text, color=_INK, size=28, width=None, anchor=None):
        font = self.fonts[size]
        if width:
            text = _fit(self.draw, text, font, width)
        self.draw.text(xy, text, font=font, fill=color, anchor=anchor)

    def line(self, a, b, color=_MUTED, width=2):
        self.draw.line((a, b), fill=color, width=width)

    def arrow(self, a, b, color, width=4):
        distance = math.dist(a, b)
        if distance < 1:
            self.draw.ellipse((a[0]-4, a[1]-4, a[0]+4, a[1]+4), outline=color, width=2)
            return
        self.line(a, b, color, width)
        dx, dy = (b[0]-a[0])/distance, (b[1]-a[1])/distance
        length = min(12, distance*.4)
        self.draw.polygon((b, (b[0]-dx*length+dy*length*.45, b[1]-dy*length-dx*length*.45),
                            (b[0]-dx*length-dy*length*.45, b[1]-dy*length+dx*length*.45)), fill=color)

    def box(self, bounds, color='#263244', outline=None):
        self.draw.rounded_rectangle(bounds, radius=8, fill=color, outline=outline, width=2)

    def axes(self, vectors, bounds=(70, 205, 640, 570), label_size=20):
        # Both coordinates have the same unit length. Bounds include original
        # vectors AND all partials, so an event cannot silently rescale geometry.
        left, top, right, bottom = bounds
        xs, ys = [0]+[v[0] for v in vectors], [0]+[v[1] for v in vectors]
        lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)
        scale = min((right-left-80)/max(1, hi_x-lo_x), (bottom-top-70)/max(1, hi_y-lo_y))
        origin = left+40-lo_x*scale, bottom-35+lo_y*scale
        def point(v):
            return origin[0]+v[0]*scale, origin[1]-v[1]*scale
        self.line((left, origin[1]), (right, origin[1]), '#506075')
        self.line((origin[0], top), (origin[0], bottom), '#506075')
        self.text((right-45, origin[1]+7), 'd1', _MUTED, label_size)
        self.text((origin[0]+9, top), 'd2', _MUTED, label_size)
        self.text((origin[0]-18, origin[1]+7), '0', _MUTED, label_size)
        for dim, lo, hi in ((0, lo_x, hi_x), (1, lo_y, hi_y)):
            step = 10**math.floor(math.log10(max(1, hi-lo)))
            for i in range(math.floor(lo/step), math.ceil(hi/step)+1):
                v = i*step
                if not v:
                    continue
                p = point((v, 0) if dim == 0 else (0, v))
                self.line((p[0]-4, p[1]-4), (p[0]+4, p[1]+4), _MUTED)
                self.text((p[0]+6, p[1]+7), _num(v), _MUTED, label_size)
        return point


def _dot_number(value):
    """Compact math notation without turning rounded decimals into exact roots."""
    if value == int(value):
        return str(int(value)).replace('-', '−')
    for radicand in (2, 3, 5, 6, 7, 10, 11, 13, 14, 15):
        coefficient = round(value / math.sqrt(radicand))
        if 0 < abs(coefficient) <= 12 and abs(value-coefficient*math.sqrt(radicand)) <= 4*math.ulp(value):
            sign = '−' if coefficient < 0 else ''
            multiple = '' if abs(coefficient) == 1 else str(abs(coefficient))
            return f'{sign}{multiple}√{radicand}'
    return _num(value).replace('-', '−')


def _dot_operand(value):
    text = _dot_number(value)
    return f'({text})' if value < 0 else text


def _dot(c, d, m, e, t):
    items, q, idx, stage = d['items'], d['query'], e['index'], e['stage']
    if stage == 'scores':
        maximum = max(1, *[abs(v) for v in m['scores']])
        c.text((60, 150), f"q [{d['query_label']}]", _Q, 44, 230)
        c.text((320, 144), '= (' + ', '.join(_dot_number(v) for v in q) + ')', _Q, 56)
        c.text((60, 212), f"÷ {_dot_number(d['divisor'])}", _Q, 44)
        c.text((350, 224), '匹配分数 · 尚未归一化', _MUTED, 28)
        c.line((870, 256), (870, 598), '#6e7c91')
        c.text((853, 604), '0', _MUTED, 28)
        for i, (item, value) in enumerate(zip(items, m['scores'])):
            y = 258+i*57
            c.text((60, y), item['label'], size=44, width=225)
            c.text((300, y), '(' + ', '.join(_dot_number(v) for v in item['vector']) + ')', _K, 44)
            c.line((870, y+28), (870+value/maximum*100, y+28), _COLORS[i], 16)
            c.text((1020, y), _dot_number(value), _INK, 44)
        c.text((60, 608), '正负共用零线 · 小数为近似值', _MUTED, 24)
        return
    item = items[idx]
    k = item['vector']
    # Keep inputs in the same place through every step. At 480px playback width,
    # operands remain 21px instead of sharing half the frame with empty axes.
    for y, name, label, vector, color in ((150, 'q', d['query_label'], q, _Q),
                                          (215, 'k', item['label'], k, _K)):
        c.text((60, y), f'{name} [{label}]', color, 44, 230)
        c.text((320, y-6), '= (' + ', '.join(_dot_number(v) for v in vector) + ')', color, 56)
    if stage == 'vectors':
        point = c.axes([q]+[x['vector'] for x in items], bounds=(70, 300, 1210, 565), label_size=40)
        c.arrow(point((0, 0)), point(k), _K, 8)
        # A narrower arrow on top keeps collinear vectors distinguishable.
        c.arrow(point((0, 0)), point(q), _Q, 3)
    if stage != 'vectors':
        for j, product in enumerate(m['products'][idx]):
            y = 294+j*66
            c.text((60, y+10), f'分量 {j+1}', _MUTED, 32)
            c.text((260, y), f'{_dot_operand(q[j])} × {_dot_operand(k[j])} = {_dot_number(product)}', _INK, 52)
    if stage in {'sum', 'result'}:
        c.line((60, 428), (1220, 428), '#344153')
        terms = f"{_dot_number(m['products'][idx][0])} + {_dot_operand(m['products'][idx][1])}"
        c.text((60, 452), '求和', _MUTED, 32)
        c.text((260, 440), f'{terms} = {_dot_number(sum(m["products"][idx]))}', _INK, 56)
    if stage == 'result':
        c.text((60, 538), 'score', _Q, 32)
        c.text((260, 524), f'{_dot_number(sum(m["products"][idx]))} ÷ {_dot_number(d["divisor"])} = {_dot_number(m["scores"][idx])}', _Q, 56)
    root = next((v for v in [*q, *k, d['divisor']] if '√' in _dot_number(v)), None)
    note = f'{_dot_number(root)} ≈ {_num(root)} · ' if root is not None else ''
    c.text((60, 605), note + '同一原点、同一单位 · 小数为近似值', _MUTED, 24)


def _normalize_draw(c, d, m, e, t):
    stage = e['stage']
    exp_visible = stage in {'exp', 'denominator', 'weights'}
    z_visible = stage in {'denominator', 'weights'}
    weights_visible = stage == 'weights'
    # Keep the six sources as context, but give the current operation its own
    # large working area. Repeating six small fractions hides their denominator.
    c.text((60, 155), '来源', _MUTED, 32)
    c.text((250, 155), 'exp(s/T)' if exp_visible else '分数', _K, 32)
    if weights_visible:
        c.text((470, 155), '权重', _Q, 32)
    maximum = max(m['exponentials'])
    scoremax = max(1, *[abs(x) for x in m['scores']])
    for i, item in enumerate(d['items']):
        y = 215+i*55
        masked = item['masked'] and stage != 'scores'
        c.text((60, y), item['label'], _COLORS[i], 44, 180)
        if not exp_visible:
            c.text((250, y), '−∞' if masked else _num(item['score']).replace('-', '−'), _INK, 44)
            if masked:
                c.text((750, y), '不可见', _MUTED, 44)
            else:
                c.line((750, y+29), (750+item['score']/scoremax*350, y+29), _COLORS[i], 14)
                c.line((750, y+10), (750, y+47), _MUTED)
        if exp_visible:
            v = m['exponentials'][i]
            c.text((250, y), _num(v), _K if not masked else _MUTED, 44)
            if not z_visible:
                c.line((750, y+29), (750+v/maximum*350, y+29), _K, 14)
        if weights_visible and i <= e['index']:
            c.text((470, y), _num(m['weights'][i]), _Q, 44)
    if z_visible:
        c.line((700, 155), (700, 565), '#344153')
        if weights_visible:
            idx = e['index']
            c.text((750, 155), d['items'][idx]['label'], _COLORS[idx], 44, 470)
            c.text((750, 225), _num(m['exponentials'][idx]), _K, 56)
            c.line((750, 305), (1180, 305), _INK, 3)
            c.text((750, 320), f"Z = {_num(m['denominator'])}", _K, 44)
            c.text((750, 440), '≈ '+_num(m['weights'][idx]), _Q, 56)
        else:
            c.text((750, 205), '左侧各项相加', _K, 44)
            c.text((750, 285), '共同分母 Z', _MUTED, 44)
            c.text((750, 355), '= '+_num(m['denominator']), _K, 56)
            c.text((750, 470), '被掩码项取 0', _MUTED, 32)
    if weights_visible:
        start = 60
        c.box((60, 578, 1220, 590), '#263244')
        for i, weight in enumerate(m['weights']):
            stop = start + 1160*weight
            if i <= e['index'] and stop > start:
                c.draw.rectangle((start, 578, stop, 590), fill=_COLORS[i])
            start = stop
        c.text((60, 605), '单位预算 0 → 1 · '+('Σ a = 1（读数为四位近似）' if e['index'] == len(d['items'])-1 else '按来源逐项填入'), _Q, 24)
    elif m['shift']:
        c.text((60, 605), f"先统一减 {_num(m['shift'])} 再取指数；归一化结果不变", _MUTED, 24)
    else:
        c.text((60, 605), f"exp(score / T)；T = {_num(d['temperature'])} · 被掩码项指数为 0", _MUTED, 24)


def _weighted_draw(c, d, m, e, t):
    idx, stage = e['index'], e['stage']
    vectors = [x['vector'] for x in d['items']]
    if stage == 'result':
        idx = len(vectors)-1
    point = c.axes(vectors+m['partials'], (70, 240, 475, 545), label_size=32)
    item, contribution = d['items'][idx], m['contributions'][idx]
    c.text((60, 155), '同一坐标尺度', _MUTED, 32)
    c.text((540, 150), item['label'], _COLORS[idx], 44, 680)
    if stage in {'vector', 'scaled'}:
        c.arrow(point((0, 0)), point(item['vector']), '#5f547b', 7)
        shown = item['vector'] if stage == 'vector' else [a+(b-a)*t for a, b in zip(item['vector'], contribution)]
        c.arrow(point((0, 0)), point(shown), _V, 3)
        c.text((540, 225), f"v = {_vec(item['vector'])}", _V, 48)
        c.text((540, 300), f"a = {_num(item['weight'])}", _Q, 48)
        if stage == 'scaled':
            for j in range(2):
                c.text((540, 385+65*j), f"{_num(item['weight'])} × {_dot_operand(item['vector'][j])} = {_num(contribution[j])}", _INK, 44)
            c.text((540, 535), '缩放过渡中' if t < 1 else f'贡献 = {_vec(contribution)}', _V, 44)
    else:
        last = len(vectors)-1 if stage == 'result' else idx
        for i in range(last+1):
            start = m['partials'][i-1] if i else [0, 0]
            # Move the whole arrow without rotating/scaling. The partial sum is
            # revealed only when the current translation has landed.
            moving = stage == 'accumulate' and i == idx and t < 1 and any(m['contributions'][i])
            origin = [x*t for x in start] if moving else start
            end = [x+y for x, y in zip(origin, m['contributions'][i])]
            c.arrow(point(origin), point(end), _COLORS[i], 4)
        # One active addition replaces the six-row decimal table. Earlier
        # contributions remain in the geometry, with no new animation schedule.
        active = last
        start = m['partials'][active-1] if active else [0, 0]
        c.text((540, 220), '累加前', _MUTED, 32)
        c.text((540, 260), _vec(start), _INK, 52)
        c.text((540, 335), '本项贡献', _MUTED, 32)
        c.text((540, 375), '+ '+_vec(m['contributions'][active]), _COLORS[active], 52)
        c.line((540, 460), (1220, 460), '#506075')
        moving = stage == 'accumulate' and t < 1 and any(contribution)
        c.text((540, 470), '输出' if stage == 'result' else '当前部分和', _MUTED, 32)
        if not moving:
            c.text((540, 510), '= '+_vec(m['partials'][active]), _INK, 52)
        if stage == 'result':
            c.arrow(point((0, 0)), point(m['output']), _INK, 2)
        elif moving:
            c.text((540, 525), '平移中 · 到位后读数', _MUTED, 32)
    c.text((60, 600), '零贡献保留为点；不制造信息流动 · 显示值为四位近似', _MUTED, 24)


def _compare(c, d, m, e, t):
    for i, item in enumerate(d['items']):
        c.text((60, 265+48*i), item['label'], _COLORS[i], 44, 180)
    c.text((60, 560), '输出', _MUTED, 32)
    for side, key in enumerate(('before', 'after')):
        x, condition, result = 290+side*485, d[key], m['cases'][side]
        if side and e['stage'] == 'before':
            continue
        c.text((x, 150), condition['label'], _K if side else _Q, 40, 445)
        query = '('+', '.join(_dot_number(v) for v in condition['query'])+')'
        c.text((x, 210), f"q={query}  T={_num(condition['temperature'])}", _MUTED, 32)
        if side and e['stage'] == 'change':
            c.text((x, 340), '改变条件', _Q, 44)
            c.text((x, 410), '→ 重新计算', _Q, 44)
            continue
        for i, item in enumerate(d['items']):
            y = 265+48*i
            masked = condition['masked'][i]
            c.text((x, y), 'mask' if masked else _num(result['weights'][i]), _INK, 44)
            c.line((x+215, y+28), (x+215+result['weights'][i]*220, y+28), _COLORS[i], 12)
        c.text((x, 550), _vec(result['output']), _INK, 44)
    c.text((60, 605), '两侧权重条同一 0…1 尺度；输出由各自新权重重新计算', _MUTED, 24)


def _projection(c, d, m, e, t):
    c.text((60, 150), f"{d['input_label']} = {_vec(d['input'])}", _Q, 44)
    nonzero = [j for j, weight in enumerate(d['input']) if weight]
    vector = lambda row: '('+', '.join(_dot_number(v) for v in row)+')'
    # Sparse inputs can show all independent projections without six rows of
    # zero products. Dense inputs focus the event's lane; all terms are retained.
    dense = len(nonzero) > 1 and e['stage'] == 'multiply'
    if dense:
        lane = d['lanes'][e['index']]
        c.text((60, 215), lane['label']+' · 逐项相乘再求和', _COLORS[e['index']], 40, 1160)
        for i, j in enumerate(nonzero):
            c.text((60, 275+i*50), f"{_dot_number(d['input'][j])} × {vector(lane['matrix'][j])}", _INK, 44)
    else:
        for i, lane in enumerate(d['lanes']):
            y = 255+i*110
            c.text((60, y), lane['label'], _COLORS[i], 44, 300)
            if e['stage'] == 'input':
                c.text((400, y), f"{len(d['input'])}×2 独立矩阵", _MUTED, 44)
            elif e['stage'] == 'result':
                c.text((400, y), 'Σ = '+vector(m['outputs'][i]), _COLORS[i], 56)
            elif nonzero:
                j = nonzero[0]
                c.text((400, y), f"{_dot_number(d['input'][j])} × {vector(lane['matrix'][j])}", _INK, 52)
            else:
                c.text((400, y), '0 × 矩阵各行 = (0, 0)', _INK, 44)
    omitted = len(d['input'])-len(nonzero)
    c.text((60, 605), f'{omitted} 个零系数项贡献为 (0, 0)；其余项逐行相乘再相加', _MUTED, 24)


def _matrix(c, d, m, e, t):
    rows, cols = len(d['rows']), len(d['items'])
    cell = 48
    c.text((60, 150), '行 query / 列 key', _Q, 32)
    query = d['rows'][e['index']]['label']
    c.text((590, 150), f'查询 = {query}', _K, 44, 630)
    c.text((590, 205), f'本行权重 a[{query}, j]', _MUTED, 32, 630)
    for j, item in enumerate(d['items']):
        c.text((180+j*60, 205), item['label'], _MUTED, 28, 58)
    for i, row in enumerate(d['rows']):
        y = 250+i*cell
        c.text((60, y+3), row['label'], _Q if i == e['index'] else _MUTED, 32, 115)
        visible = e['stage'] != 'row' or i == e['index']
        for j, weight in enumerate(m['rows'][i]['weights']):
            x = 180+j*60
            c.box((x, y, x+cell-4, y+cell-4), '#263244')
            if not visible:
                continue
            if m['rows'][i]['masked'][j]:
                c.line((x+5, y+cell-8), (x+cell-8, y+5), _MUTED)
            else:
                radius = math.sqrt(weight)*(cell/2-6)
                centre = cell/2-2
                c.draw.ellipse((x+centre-radius, y+centre-radius, x+centre+radius, y+centre+radius), fill=_K)
            if i == e['index']:
                column, line = divmod(j, 3)
                c.text((590+column*340, 270+line*70), d['items'][j]['label'], _K, 44, 130)
                c.text((730+column*340, 270+line*70), _num(weight), _K, 44)
    c.text((60, 605), '圆面积 ∝ 权重（0…1）；斜线 = 不可见位置', _MUTED, 24)
    if e['stage'] == 'result':
        c.text((590, 485), '本行输出 '+_vec(m['rows'][e['index']]['output']), _V, 44)
        c.text((60, 555), f'A ({rows}×{cols}) · V ({cols}×2) = O ({rows}×2)', _INK, 44)


def _pipeline(c, d, m, e, t):
    stage = e['stage']
    n = len(d['lanes'])
    if stage in {'lanes', 'concat'}:
        c.text((60, 155), f"{d['input_label']} : n×{d['input_width']} → {n} 个独立通道", _Q, 44)
        step = 1160/n
        for i, lane in enumerate(d['lanes']):
            left = 60+i*step
            c.box((left, 250, left+step-20, 405), outline=_COLORS[i])
            c.text((left+18, 262), lane['label'], _COLORS[i], 44, step-56)
            c.text((left+18, 325), f"n×{d['lane_width']}", _INK, 56)
            if stage == 'concat':
                c.arrow((left+step/2-10, 405), (left+step/2-10, 460), _COLORS[i], 3)
                c.box((left, 465, left+step-20, 515), _COLORS[i])
        if stage == 'lanes':
            for i, lane in enumerate(d['lanes']):
                c.text((60, 435+50*i), lane['label']+' : '+lane['detail'], _COLORS[i], 32, 1160)
        if stage == 'concat':
            c.text((60, 535), f"{d['merge_label']} : n×({n}×{d['lane_width']}) = n×{n*d['lane_width']}", _INK, 44)
    else:
        # Zoom into projection once the lane/concat events have explained it.
        c.text((60, 155), d['merge_label'], _Q, 40, 380)
        c.text((545, 155), d['projection_label'], _V, 44, 400)
        c.text((1040, 155), d['output_label'], _K, 44, 180)
        c.text((60, 225), f"n×{n*d['lane_width']}", _Q, 56)
        c.text((390, 225), '×', _INK, 56)
        c.text((545, 225), f"{n*d['lane_width']}×{d['input_width']}", _V, 56)
        c.text((880, 225), '→', _INK, 56)
        c.text((1040, 225), f"n×{d['input_width']}", _K, 56)
    if stage in {'residual', 'context'}:
        c.line((60, 325), (1220, 325), '#344153')
        c.text((60, 360), f"{d['input_label']} (n×{d['input_width']}) + {d['output_label']} (n×{d['input_width']})", _INK, 56)
        c.text((60, 450), f"→ n×{d['input_width']} 同维残差", _Q, 56)
    if stage == 'context':
        width = 1150/len(d['context'])
        for i, label in enumerate(d['context']):
            x = 60+i*width
            c.box((x, 570, x+width-15, 630))
            c.text((x+10, 575), label, _MUTED, 32, width-35)
    else:
        c.text((60, 600), '各通道参数独立；拼接保留段边界；数值未定义的部分只展示维数', _MUTED, 24)


def _sequence(c, d, m, e, t):
    items = d['items']
    for i, item in enumerate(items):
        y = 165+i*66
        c.box((60, y, 1220, y+56), '#263244', _Q if i == e['index'] else None)
        c.text((80, y+10), item['label'], _Q if i == e['index'] else _MUTED, 28, 260)
        c.text((355, y+13), item['detail'], _INK, 24, 835)
    c.text((60, 605), d['note'], _MUTED, 24, 1150)


_DRAW = {'dot': _dot, 'normalize': _normalize_draw, 'weighted_sum': _weighted_draw,
         'compare': _compare, 'projection': _projection, 'matrix': _matrix,
         'pipeline': _pipeline, 'sequence': _sequence}


def _prepare_visual(plan, scene, fonts):
    from PIL import Image, ImageFont
    fonts = {**fonts, **{size: ImageFont.truetype(fonts[24].path, size) for size in (26, 28, 32, 40, 44, 48, 52, 56)}}
    image = Image.new('RGB', (1280, 720), _BG)
    c = _Canvas(image, fonts)
    c.text((48, 24), scene['title'], _INK, 40, 1180)
    c.text((48, 89), scene['visual']['condition'], _MUTED, 24, 1180)
    c.line((48, 132), (1232, 132), '#344153')
    # Lower 80px stay empty for the real subtitle track, including paused frames.
    return image, {'visual': scene['visual'], 'math': visual_math(scene['visual']), 'fonts': fonts}


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
    if 'visual' in scene:
        return _prepare_visual(plan, scene, fonts)
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
    if 'visual' in scene:
        event, fraction = visual_event(connections['visual'], at)
        if event:
            _DRAW[connections['visual']['kind']](_Canvas(image, connections['fonts']), connections['visual']['data'],
                                                connections['math'], event, fraction)
        return image
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


def prepare_audio(audio, directory, ffmpeg):
    """Decode a fixed source locally; no synthesis, network or author command.

    Keep the original MP3 as provenance evidence. Its hash is never attributed
    to the decoded WAV. Bound both the compressed input and decoded duration.
    """
    if not isinstance(audio, bytes) or not 0 < len(audio) <= 64*1024*1024:
        raise ValueError('固定音轨为空或超过大小限制')
    wav_path = directory/'narration.wav'
    mp3 = not (audio[:4] == b'RIFF' and audio[8:12] == b'WAVE')
    if mp3:
        source = directory/'narration-source.mp3'
        source.write_bytes(audio)
        # The fixed MP3 demuxer rejects other formats. Decode one extra second
        # and reject >300s below: limiting work must not silently trim a movie.
        args = [ffmpeg, '-y', '-nostdin', '-loglevel', 'error', '-protocol_whitelist', 'file,pipe',
                '-f', 'mp3', '-i', str(source), '-map', '0:a:0', '-vn', '-sn', '-dn',
                '-t', '301', '-threads', '1', '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le',
                '-f', 'wav', str(wav_path)]
        try:
            decoded = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('固定MP3解码超时') from exc
        if decoded.returncode:
            raise ValueError('固定MP3解码失败')
    else:
        wav_path.write_bytes(audio)
    try:
        with wave.open(str(wav_path)) as wav:
            duration = wav.getnframes()/wav.getframerate()
            if wav.getnchannels() not in (1, 2) or wav.getsampwidth() != 2 or not 0 < wav.getframerate() <= 48000 or not 0 < duration <= 300:
                raise ValueError('解码音轨须为不超过五分钟的PCM16 WAV')
    except (wave.Error, EOFError) as exc:
        raise ValueError('音轨不是有效的PCM WAV') from exc
    return wav_path, duration, {'source_sha256': hashlib.sha256(audio).hexdigest(),
        'source_media_type': 'audio/mpeg' if mp3 else 'audio/wav', 'decoded_duration': duration,
        'decoding': 'ffmpeg/mp3-to-pcm16-mono-24000' if mp3 else 'original-pcm16'}


def render(plan, directory, audio=None):
    from PIL import ImageFont
    if audio is None:
        raise ValueError('视频需要固定音轨文件')
    tools = capabilities()
    needed = [key for key in ('ffmpeg', 'font') if not tools.get(key)]
    if needed:
        raise RuntimeError('视频生成依赖未就绪')
    fonts = {size: ImageFont.truetype(tools['font'], size) for size in (16, 20, 24)}
    scenes = [dict(s) for s in plan['scenes']]
    wav_path, duration, audio_decoding = prepare_audio(audio, directory, tools['ffmpeg'])
    cues = plan['cues']
    if scenes[-1]['end'] > duration+.05 or cues[-1]['end'] > duration+.05:
        raise ValueError('字幕或阶段时间超出实际音轨')
    voice = '固定音轨文件'
    captions = 'WEBVTT\n\n' + '\n\n'.join(f"{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text'].replace('-->', '→')}" for c in cues)
    (directory/'captions.vtt').write_text(captions, encoding='utf-8')
    (directory/'transcript.txt').write_text('\n\n'.join(s['speech'] for s in scenes), encoding='utf-8')
    size = (1280, 720) if any('visual' in s for s in scenes) else (960, 540)
    # Better temporal continuity supports authored transformations; it is not a
    # substitute for the independent high-quality content verdict.
    fps = 12 if plan.get('_quality', 'medium') == 'low' else 24
    crf = '24' if fps == 12 else '20'
    args = [tools['ffmpeg'], '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{size[0]}x{size[1]}',
            '-r', str(fps), '-i', 'pipe:0', '-i', str(wav_path), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', crf,
            '-pix_fmt', 'yuv420p', '-threads', '2', '-c:a', 'aac', '-shortest', '-movflags', '+faststart',
            '-fs', str(128*1024*1024), str(directory/'video.mp4')]
    with (directory/'encoding.log').open('wb') as log:
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        timer = threading.Timer(300, process.kill)
        timer.start()
        try:
            prepared_scene, prepared = None, None
            for index in range(math.ceil(duration*fps)):
                at = index/fps
                scene = next((s for s in scenes if at < s['end']), scenes[-1])
                progress = min(1, max(0, (at-scene['start'])/max(.01, scene['end']-scene['start'])))
                if scene is not prepared_scene:
                    prepared_scene, prepared = scene, prepare_frame(plan, scene, fonts)
                image = frame(plan, scene, progress, at, fonts, prepared)
                if image.size != size:
                    image = image.resize(size)
                if index == fps:
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
    return {'duration': duration, 'chapters': [{'time': s['start'], 'title': s['title']} for s in scenes],
            'voice_note': voice, 'audio_decoding': audio_decoding}
