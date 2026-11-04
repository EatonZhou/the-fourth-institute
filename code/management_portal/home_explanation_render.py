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

    def axes(self, vectors, bounds=(70, 205, 640, 570)):
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
        self.text((right-45, origin[1]+7), 'd1', _MUTED, 20)
        self.text((origin[0]+9, top), 'd2', _MUTED, 20)
        self.text((origin[0]-18, origin[1]+7), '0', _MUTED, 20)
        for dim, lo, hi in ((0, lo_x, hi_x), (1, lo_y, hi_y)):
            step = 10**math.floor(math.log10(max(1, hi-lo)))
            for i in range(math.floor(lo/step), math.ceil(hi/step)+1):
                v = i*step
                if not v:
                    continue
                p = point((v, 0) if dim == 0 else (0, v))
                self.line((p[0]-4, p[1]-4), (p[0]+4, p[1]+4), _MUTED)
                self.text((p[0]+6, p[1]+7), _num(v), _MUTED, 20)
        return point


def _dot(c, d, m, e, t):
    items, q, idx, stage = d['items'], d['query'], e['index'], e['stage']
    if stage == 'scores':
        maximum = max(1, *[abs(v) for v in m['scores']])
        c.text((60, 155), f"q [{d['query_label']}] = {_vec(q)}    ÷ {_num(d['divisor'])}", _Q)
        c.line((810, 218), (810, 565), '#6e7c91')
        c.text((790, 575), '0', _MUTED, 20)
        for i, (item, value) in enumerate(zip(items, m['scores'])):
            y = 232+i*52
            c.text((60, y), f"{item['id']} · {item['label']}", width=240)
            c.text((310, y), _vec(item['vector']), _K)
            c.line((810, y+17), (810+value/maximum*220, y+17), _COLORS[i], 16)
            c.text((1120, y), _num(value), _INK, 28)
        c.text((60, 600), '匹配分数 · 尚未归一化 · 正负共用零线', _MUTED, 24)
        return
    item = items[idx]
    k = item['vector']
    point = c.axes([q]+[x['vector'] for x in items])
    c.arrow(point((0, 0)), point(k), _K, 8)
    # A narrower arrow on top keeps collinear vectors distinguishable.
    c.arrow(point((0, 0)), point(q), _Q, 3)
    c.text((60, 150), f"q [{d['query_label']}] = {_vec(q)}", _Q)
    c.text((680, 150), f"k [{item['label']}] = {_vec(k)}", _K)
    if stage != 'vectors':
        for j, product in enumerate(m['products'][idx]):
            c.text((700, 235+j*65), f'd{j+1}    {_num(q[j])} × {_num(k[j])} = {_num(product)}', _INK, 28)
    if stage in {'sum', 'result'}:
        c.line((700, 370), (1220, 370))
        terms = f"{_num(m['products'][idx][0])} + {_num(m['products'][idx][1])}"
        formula = f"({terms}) / {_num(d['divisor'])}" if stage == 'result' else f"{terms} = {_num(sum(m['products'][idx]))}"
        c.text((700, 395), formula)
    if stage == 'result':
        c.text((700, 470), f"score = {_num(m['scores'][idx])}", _Q, 40)
    c.text((60, 600), '同一原点、同一单位 · 分量乘加后缩放 · 显示为近似值', _MUTED, 24)


def _normalize_draw(c, d, m, e, t):
    stage = e['stage']
    exp_visible = stage in {'exp', 'denominator', 'weights'}
    z_visible = stage in {'denominator', 'weights'}
    weights_visible = stage == 'weights'
    c.text((60, 155), '来源 / 分数', _MUTED, 24)
    if exp_visible:
        c.text((420, 155), f"exp(s/T − {_num(m['shift'])})" if m['shift'] else 'exp(s/T) · 未归一化', _K, 24)
    if weights_visible:
        c.text((865, 155), '÷ 同一个 Z → 权重', _Q, 24)
    maximum = max(m['exponentials'])
    scoremax = max(1, *[abs(x) for x in m['scores']])
    for i, item in enumerate(d['items']):
        y = 210+i*48
        masked = item['masked'] and stage != 'scores'
        c.text((60, y), f"{item['id']} · {item['label']}", _COLORS[i], 24, 185)
        c.text((260, y), '−∞' if masked else _num(item['score']), _INK, 28)
        if masked:
            c.line((60, y+16), (380, y+16), _MUTED)
        elif not exp_visible:
            c.line((620, y+16), (620+item['score']/scoremax*200, y+16), _COLORS[i], 12)
            c.line((620, y+3), (620, y+30), _MUTED)
        if exp_visible:
            v = m['exponentials'][i]
            c.text((420, y), _num(v), _K)
            c.line((570, y+17), (570+v/maximum*230, y+17), _K, 12)
        if weights_visible and i <= e['index']:
            c.text((865, y), f"{_num(m['exponentials'][i])} / {_num(m['denominator'])}", _MUTED, 24, 280)
            c.text((1155, y), _num(m['weights'][i]), _Q, 24)
    if z_visible:
        c.text((60, 515), f"Z = {' + '.join(_num(v) for v in m['exponentials'])} = {_num(m['denominator'])}", _K, 24, 1150)
    if weights_visible:
        start = 60
        c.box((60, 559, 1220, 585), '#263244')
        for i, weight in enumerate(m['weights']):
            stop = start + 1160*weight
            if i <= e['index'] and stop > start:
                c.draw.rectangle((start, 559, stop, 585), fill=_COLORS[i])
            start = stop
        c.text((60, 596), '单位预算 0 → 1 · '+('Σ a = 1（读数为四位近似）' if e['index'] == len(d['items'])-1 else '按来源逐项填入'), _Q, 24)
    elif m['shift']:
        c.text((60, 596), f"先统一减 {_num(m['shift'])} 再取指数；归一化结果不变", _MUTED, 24)
    else:
        c.text((60, 596), f"exp(score / T)；T = {_num(d['temperature'])} · 被掩码项指数为 0", _MUTED, 24)


def _weighted_draw(c, d, m, e, t):
    idx, stage = e['index'], e['stage']
    vectors = [x['vector'] for x in d['items']]
    point = c.axes(vectors+m['partials'])
    item, contribution = d['items'][idx], m['contributions'][idx]
    c.text((60, 150), '二维贡献 · 坐标尺度保持不变', _MUTED, 24)
    if stage in {'vector', 'scaled'}:
        c.arrow(point((0, 0)), point(item['vector']), '#5f547b', 7)
        shown = item['vector'] if stage == 'vector' else [a+(b-a)*t for a, b in zip(item['vector'], contribution)]
        c.arrow(point((0, 0)), point(shown), _V, 3)
        c.text((700, 175), f"{item['id']} · {item['label']}", _COLORS[idx], 32)
        c.text((700, 245), f"v = {_vec(item['vector'])}", _V)
        c.text((700, 300), f"a = {_num(item['weight'])}", _Q)
        if stage == 'scaled':
            for j in range(2):
                c.text((700, 365+58*j), f"d{j+1}: {_num(item['weight'])} × {_num(item['vector'][j])} = {_num(contribution[j])}", _INK, 26)
            c.text((700, 495), '缩放过渡中' if t < 1 else f'贡献 = {_vec(contribution)}', _V, 28)
    else:
        last = len(vectors)-1 if stage == 'result' else idx
        for x, title in ((700, '来源'), (800, '累加前'), (940, '本项贡献'), (1080, '累加后')):
            c.text((x, 155), title, _MUTED, 24)
        # Stack coordinates so both numbers remain readable within each column.
        # Source IDs are labels only and must never appear as arithmetic operands.
        column = lambda v: f"({_num(v[0])},\n {_num(v[1])})"
        for i in range(last+1):
            start = m['partials'][i-1] if i else [0, 0]
            # Move the whole arrow without rotating/scaling. The partial sum is
            # revealed only when the current translation has landed.
            moving = stage == 'accumulate' and i == idx and t < 1 and any(m['contributions'][i])
            origin = [x*t for x in start] if moving else start
            end = [x+y for x, y in zip(origin, m['contributions'][i])]
            c.arrow(point(origin), point(end), _COLORS[i], 4)
            c.text((700, 195+54*i), d['items'][i]['label'], _COLORS[i], 20, 90)
            c.text((800, 195+54*i), column(start), _INK, 20)
            c.text((940, 195+54*i), column(m['contributions'][i]), _COLORS[i], 20)
            if not moving:
                c.text((1080, 195+54*i), column(m['partials'][i]), _INK, 20)
        if stage == 'result':
            c.arrow(point((0, 0)), point(m['output']), _INK, 2)
            c.text((700, 545), '输出 = '+_vec(m['output']), _INK, 32)
        elif t < 1 and any(contribution):
            c.text((700, 550), '平移中 · 不把移动端点当作部分和', _MUTED, 24)
    c.text((60, 600), '零贡献保留为点；不制造信息流动 · 显示值为四位近似', _MUTED, 24)


def _compare(c, d, m, e, t):
    changed = {'query': '查询向量', 'masked': '掩码', 'temperature': '温度'}[d['change']]
    c.text((60, 150), f'仅改变：{changed}；K、V及其它条件固定', _Q, 28)
    for side, key in enumerate(('before', 'after')):
        x, condition, result = 60+side*620, d[key], m['cases'][side]
        if side and e['stage'] == 'before':
            continue
        c.text((x, 205), condition['label'], _K if side else _MUTED, 28, 550)
        c.text((x, 250), f"q={_vec(condition['query'])}   T={_num(condition['temperature'])}", _MUTED, 24)
        if side and e['stage'] == 'change':
            c.text((x, 355), '改变条件 → 重新计算', _Q, 32)
            c.text((x, 410), '过渡不显示伪造的中间权重', _MUTED, 24)
            continue
        for i, item in enumerate(d['items']):
            y = 300+40*i
            masked = condition['masked'][i]
            c.text((x, y), item['label'], _COLORS[i], 24, 150)
            c.text((x+155, y), 'mask' if masked else _num(result['weights'][i]), _MUTED, 24)
            c.line((x+270, y+15), (x+270+result['weights'][i]*270, y+15), _COLORS[i], 12)
        c.text((x, 560), '输出 '+_vec(result['output']), _INK, 28)
    c.text((60, 605), '两侧权重条同一 0…1 尺度；输出由各自新权重重新计算', _MUTED, 24)


def _projection(c, d, m, e, t):
    c.text((60, 150), f"{d['input_label']} = {_vec(d['input'])}", _Q, 32)
    c.text((60, 198), f"同一输入 {len(d['input'])} 维 → 独立矩阵 → 各 2 维结果", _MUTED, 24)
    for i, lane in enumerate(d['lanes']):
        x = 60+i*410
        c.box((x, 250, x+370, 570))
        c.text((x+18, 270), lane['label']+' · 固定参数', _COLORS[i], 28, 335)
        if e['stage'] == 'input':
            continue
        for j, (weight, row) in enumerate(zip(d['input'], lane['matrix'])):
            c.text((x+18, 320+j*31), f'{_num(weight)} × {_vec(row)}', _INK if weight else _MUTED, 24)
        if e['stage'] == 'result':
            c.text((x+18, 520), 'Σ = '+_vec(m['outputs'][i]), _COLORS[i], 28)
    c.text((60, 605), '输入系数逐行乘矩阵，再相加；参数与计算结果分别显示', _MUTED, 24)


def _matrix(c, d, m, e, t):
    rows, cols = len(d['rows']), len(d['items'])
    cell = 46
    c.text((60, 150), '行 = query；列 = key · 每行归一化', _Q, 28)
    query = d['rows'][e['index']]['label']
    c.text((730, 150), f'查询 = {query} · 本行权重', _K, 28, 490)
    c.text((730, 190), f'a[{query}, j] · 按 key 列排列', _MUTED, 24, 490)
    if e['stage'] == 'result':
        c.text((500, 205), '输出 O', _V, 24)
    for j, item in enumerate(d['items']):
        c.text((210+j*cell, 205), item['id'], _MUTED, 20, cell-4)
    for i, row in enumerate(d['rows']):
        y = 242+i*cell
        c.text((60, y+10), row['label'], _Q if i == e['index'] else _MUTED, 24, 145)
        visible = e['stage'] != 'row' or i == e['index']
        for j, weight in enumerate(m['rows'][i]['weights']):
            x = 206+j*cell
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
                c.text((730, 225+j*42), f"{d['items'][j]['label']}   {_num(weight)}", _K, 26, 470)
        if visible and e['stage'] == 'result':
            c.text((500, y+10), _vec(m['rows'][i]['output']), _V, 24)
    c.text((60, 585), '圆面积 ∝ 权重（0…1）；斜线 = 不可见位置', _MUTED, 24)
    if e['stage'] == 'result':
        c.text((730, 535), f'O = AV  ({rows}×2)', _INK, 32)
        c.text((730, 580), f'A ({rows}×{cols}) · V ({cols}×2)', _V, 24)


def _pipeline(c, d, m, e, t):
    stage = e['stage']
    c.text((60, 150), f"符号维数示意 · {d['input_label']} : n×{d['input_width']}", _Q, 28)
    n = len(d['lanes'])
    for i, lane in enumerate(d['lanes']):
        left, y = 60+i*400, 215
        c.box((left, y, left+360, y+90), outline=_COLORS[i])
        c.text((left+18, y+9), lane['label'], _COLORS[i], 28, 324)
        c.text((left+18, y+48), lane['detail'], _MUTED, 20, 324)
        if stage != 'lanes':
            x = 360+i*120
            c.arrow((left+180, y+90), (x+58, 365), _COLORS[i], 3)
            c.box((x, 370, x+116, 425), _COLORS[i])
            c.text((x+10, 383), f"{lane['id']} ({d['lane_width']})", _BG, 20)
    if stage != 'lanes':
        c.text((60, 380), d['merge_label'], _INK, 26, 280)
        c.text((360, 450), f"n×({n}×{d['lane_width']}) · 沿特征维拼接", _MUTED, 24)
    if stage in {'project', 'residual', 'context'}:
        c.arrow((360+n*120, 397), (990, 397), _INK)
        c.text((790, 335), d['projection_label'], _V, 26, 180)
        c.text((790, 425), f"{n*d['lane_width']}×{d['input_width']}", _MUTED, 24)
        c.box((995, 365, 1220, 440), outline=_V)
        c.text((1010, 375), d['output_label'], _V, 24, 195)
        c.text((1010, 407), f"n×{d['input_width']}", _MUTED, 20)
    if stage in {'residual', 'context'}:
        c.text((60, 530), f"{d['input_label']} (n×{d['input_width']}) + {d['output_label']} (n×{d['input_width']}) → 同维残差", _Q, 28)
    if stage == 'context':
        width = 1150/len(d['context'])
        for i, label in enumerate(d['context']):
            x = 60+i*width
            c.box((x, 583, x+width-15, 623))
            c.text((x+10, 588), label, _MUTED, 20, width-35)
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
    fonts = {**fonts, **{size: ImageFont.truetype(fonts[24].path, size) for size in (26, 28, 32, 40)}}
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
        raise ValueError('视频需要已验证的正式语音服务音轨；不支持本地配音回退')
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
    voice = '固定音轨；实际云服务与模型以正式语音执行回执为准'
    captions = 'WEBVTT\n\n' + '\n\n'.join(f"{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text'].replace('-->', '→')}" for c in cues)
    (directory/'captions.vtt').write_text(captions, encoding='utf-8')
    (directory/'transcript.txt').write_text('\n\n'.join(s['speech'] for s in scenes), encoding='utf-8')
    size = (1280, 720) if any('visual' in s for s in scenes) else (960, 540)
    args = [tools['ffmpeg'], '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{size[0]}x{size[1]}',
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
                if image.size != size:
                    image = image.resize(size)
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
    return {'duration': duration, 'chapters': [{'time': s['start'], 'title': s['title']} for s in scenes],
            'voice_note': voice, 'audio_decoding': audio_decoding}
