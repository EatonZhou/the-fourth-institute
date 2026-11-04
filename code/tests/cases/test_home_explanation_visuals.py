"""Direct visual mathematics/timing checks; not a video-review Run."""
import io
import math
from pathlib import Path
import wave
import pytest
from management_portal.home_explanation_plan import validate, validate_visual, visual_math
from management_portal import home_explanation_render as renderer


def event(stage, index=0, at=0, duration=0, cue=None):
    return dict(stage=stage, index=index, at=at, duration=duration, cue=cue)


def visual(kind, data, events):
    return dict(kind=kind, condition='固定来源与尺度；人工测试数据', data=data, events=events)


def dot():
    return visual('dot', dict(query_label='source', query=[2, -1], divisor=2,
        items=[dict(id='a', label='alpha', vector=[3, 4]), dict(id='b', label='beta', vector=[-2, 2])]),
        [event('vectors'), event('products', at=1), event('sum', at=2), event('result', at=3)])


def normal():
    return visual('normalize', dict(temperature=1, items=[
        dict(id=str(i), label=str(i), score=s, masked=False) for i, s in enumerate([2, 0, -1])]),
        [event('scores'), event('exp', at=1), event('denominator', at=2), event('weights', 2, at=3)])


def weighted():
    return visual('weighted_sum', dict(items=[
        dict(id='a', label='alpha', vector=[2, -1], weight=.5),
        dict(id='b', label='beta', vector=[0, 0], weight=.25),
        dict(id='c', label='gamma', vector=[-2, 3], weight=.25)]),
        [event('scaled'), event('accumulate', at=1, duration=.5),
         event('accumulate', 1, at=2, duration=.5), event('result', 2, at=3)])


def scene(v):
    return dict(title='直接绘制测试', speech='用于核查计算与绘制时序的固定数据。', start=0, end=4, visual=v)


def test_math_from_inputs_preserves_signs_and_zero():
    m = visual_math(validate_visual(dot(), 0, 4, []))
    assert m['products'] == [[6, -4], [-4, -2]]
    assert m['scores'] == [1, -3]
    w = visual_math(validate_visual(weighted(), 0, 4, []))
    assert w['contributions'] == [[1, -.5], [0, 0], [-.5, .75]]
    assert w['partials'] == [[1, -.5], [1, -.5], [.5, .25]]
    assert w['output'] == [.5, .25]


def test_mask_recomputes_common_denominator_before_weights():
    v = normal()
    old = visual_math(v)
    v['data']['items'][0]['masked'] = True
    new = visual_math(validate_visual(v, 0, 4, []))
    assert new['exponentials'][0] == new['weights'][0] == 0
    assert new['denominator'] == pytest.approx(1+math.exp(-1))
    assert new['weights'][1] > old['weights'][1]
    assert sum(new['weights']) == pytest.approx(1, abs=1e-15)
    v['data']['items'][0].update(masked=False, score=50)
    v['data']['temperature'] = .1
    extreme = visual_math(validate_visual(v, 0, 4, []))
    assert math.isfinite(extreme['denominator'])
    assert extreme['shift'] == 460
    assert sum(extreme['weights']) == pytest.approx(1)


def cases():
    items = [dict(id=str(i), label=str(i), key=k, vector=v) for i, (k, v) in enumerate([
        ([1, 0], [2, 0]), ([0, 1], [0, 2]), ([-1, 0], [-1, 1])])]
    return items, dict(label='before', query=[1, 0], masked=[False]*3, temperature=1)


def test_counterfactual_and_causal_matrix():
    items, before = cases()
    after = dict(before, label='after', query=[0, 1])
    v = visual('compare', dict(items=items, divisor=1, before=before, after=after, change='query'), [event('after')])
    result = visual_math(validate_visual(v, 0, 4, []))['cases']
    assert result[0]['output'] != result[1]['output']
    assert result[0]['weights'][0] == pytest.approx(math.exp(1)/(math.exp(1)+1+math.exp(-1)))
    v['data']['after']['temperature'] = 2
    with pytest.raises(ValueError, match='只允许改变一个'):
        validate_visual(v, 0, 4, [])
    matrix = visual('matrix', dict(items=items, divisor=1, causal=True,
        rows=[dict(id=str(i), label=str(i), vector=item['key']) for i, item in enumerate(items)]), [event('result', 2)])
    rows = visual_math(validate_visual(matrix, 0, 4, []))['rows']
    assert rows[0]['weights'] == [1, 0, 0]
    assert rows[0]['output'] == items[0]['vector']
    for i, row in enumerate(rows):
        assert sum(row['weights']) == pytest.approx(1)
        assert all(v == 0 for v in row['weights'][i+1:])


def test_projection_is_linear_combination():
    v = visual('projection', dict(input_label='x', input=[2, -1, .5], lanes=[
        dict(id='p', label='P', matrix=[[1, 2], [3, -1], [2, 4]])]), [event('result')])
    assert visual_math(validate_visual(v, 0, 4, []))['outputs'] == [[0, 7]]
    v['data']['lanes'][0]['matrix'].pop()
    with pytest.raises(ValueError, match='维数'):
        validate_visual(v, 0, 4, [])


@pytest.mark.parametrize('edit', [
    lambda v: v.update(script='raise Exception'),
    lambda v: v['data'].update(output=[1, 2]),
    lambda v: v['data']['query'].append(3),
    lambda v: v['data']['query'].__setitem__(0, float('nan')),
    lambda v: v['events'][0].update(stage='eval'),
    lambda v: v['events'][0].update(stage={'execute': 'code'}),
    lambda v: v['events'][1].update(at=0),
    lambda v: v['events'][0].update(duration=2),
    lambda v: v['events'][0].update(index=4),
    lambda v: v['data']['items'][1].update(id='a'),
])
def test_closed_bounded_contract(edit):
    v = dot()
    edit(v)
    with pytest.raises(ValueError):
        validate_visual(v, 0, 4, [])


def test_events_require_real_cue_or_actual_silence():
    cues = [dict(start=0, end=1, text='first'), dict(start=2, end=3, text='second')]
    v = dot()
    v['events'] = [event('vectors', cue=0), event('products', at=1.1), event('sum', at=2, cue=1)]
    validate_visual(v, 0, 4, cues)
    assert renderer.visual_event(v, -1) == (None, 0)
    assert renderer.visual_event(v, .9)[0]['stage'] == 'vectors'
    assert renderer.visual_event(v, 30)[0]['stage'] == 'sum'
    v['events'][0]['cue'] = None
    with pytest.raises(ValueError, match='静默'):
        validate_visual(v, 0, 4, cues)
    v['events'][0]['cue'] = 1
    with pytest.raises(ValueError, match='真实字幕'):
        validate_visual(v, 0, 4, cues)


def test_rectangular_matrix_indices_follow_query_rows():
    items, _ = cases()
    v = visual('matrix', dict(items=items[:1], divisor=1, causal=False,
        rows=[dict(id=str(i), label=str(i), vector=item['key']) for i, item in enumerate(items)]), [event('row', 2)])
    assert len(visual_math(validate_visual(v, 0, 4, []))['rows']) == 3
    v['events'][0]['index'] = 3
    with pytest.raises(ValueError):
        validate_visual(v, 0, 4, [])


@pytest.fixture
def fonts():
    from PIL import ImageFont
    from management_portal.media_tools import capabilities
    path = capabilities()['font']
    if not Path(path).is_file():
        pytest.skip('managed CJK font unavailable')
    return {s: ImageFont.truetype(path, s) for s in (16, 20, 24)}


def test_raster_reveal_hold_zero_and_partial_landing(fonts, monkeypatch):
    text = []
    original = renderer._Canvas.text
    def record(self, xy, value, *args, **kwargs):
        text.append(value)
        return original(self, xy, value, *args, **kwargs)
    monkeypatch.setattr(renderer._Canvas, 'text', record)
    v, p = dot(), dict(title='test')
    s = scene(v)
    prepared = renderer.prepare_frame(p, s, fonts)
    text.clear()
    early = renderer.frame(p, s, 0, .5, fonts, prepared)
    assert not any('score =' in value or '×' in value for value in text)
    text.clear()
    late = renderer.frame(p, s, 0, 3.5, fonts, prepared)
    assert '2 ÷ 2 = 1' in text
    assert early.tobytes() != late.tobytes()
    assert late.tobytes() == renderer.frame(p, s, 0, 30, fonts, prepared).tobytes()
    assert late.size == (1280, 720)
    assert len(late.crop((0, 644, 1280, 720)).getcolors()) == 1
    s = scene(weighted())
    prepared = renderer.prepare_frame(p, s, fonts)
    text.clear()
    renderer.frame(p, s, 0, 1.2, fonts, prepared)
    assert not any(value.startswith('= ') for value in text)
    assert any('平移中' in value for value in text)
    a = renderer.frame(p, s, 0, 2.05, fonts, prepared)
    b = renderer.frame(p, s, 0, 2.45, fonts, prepared)
    assert a.crop((60, 200, 650, 580)).tobytes() == b.crop((60, 200, 650, 580)).tobytes()


def test_partial_sum_operands_and_query_weights_have_distinct_labels(fonts, monkeypatch):
    drawn = []
    original = renderer._Canvas.text
    def record(self, xy, value, *args, **kwargs):
        drawn.append((xy, value))
        return original(self, xy, value, *args, **kwargs)
    monkeypatch.setattr(renderer._Canvas, 'text', record)
    p, s = dict(title='test'), scene(weighted())
    renderer.frame(p, s, 0, 3.5, fonts)
    text = [value for _, value in drawn]
    assert '累加前' in text and '本项贡献' in text and '输出' in text
    assert 'gamma' in text and 'alpha' not in text, 'expand only the final active addition'
    assert '(1, -0.5)' in text
    assert '+ (-0.5, 0.75)' in text
    assert '= (0.5, 0.25)' in text
    drawn.clear()
    renderer.frame(p, s, 0, 1.2, fonts)
    assert not any(value.startswith('= ') for _, value in drawn), 'moving endpoint is not a partial sum'
    assert '平移中 · 到位后读数' in [value for _, value in drawn]
    drawn.clear()
    items = [dict(id='a', label='Alpha', key=[1, 0], vector=[2, 0]),
             dict(id='b', label='Beta', key=[0, 1], vector=[0, 2])]
    v = visual('matrix', dict(items=items, divisor=1, causal=False,
        rows=[dict(id='r', label='Selected query', vector=[1, 0])]), [event('result')])
    renderer.frame(p, scene(v), 0, 1, fonts)
    text = [value for _, value in drawn]
    assert '查询 = Selected query' in text
    assert '本行权重 a[Selected query, j]' in text
    assert '本行输出 (1.4621, 0.5379)' in text
    assert 'A (1×2) · V (2×2) = O (1×2)' in text


def test_narrow_normalization_keeps_common_denominator_and_reveal_order(fonts, monkeypatch):
    drawn = []
    original = renderer._Canvas.text
    def record(self, xy, text, color=renderer._INK, size=28, width=None, anchor=None):
        drawn.append((text, size))
        return original(self, xy, text, color, size, width, anchor)
    monkeypatch.setattr(renderer._Canvas, 'text', record)
    v = normal()
    v['data']['items'][2]['masked'] = True
    v['events'] = [event('scores'), event('denominator', at=1), event('weights', 0, at=2), event('weights', 2, at=3)]
    s = scene(v)
    for time in (.5, 1.5, 2.5, 3.5):
        drawn.clear()
        renderer.frame({}, s, 0, time, fonts)
        text = [value for value, _ in drawn]
        if time < 1:
            assert '共同分母 Z' not in text and '≈ 0.8808' not in text
        elif time < 2:
            assert ('= 8.3891', 56) in drawn and '≈ 0.8808' not in text
        elif time < 3:
            assert ('Z = 8.3891', 44) in drawn and ('≈ 0.8808', 56) in drawn
            assert '0.1192' not in text, 'later weights still wait for their event'
        else:
            assert ('Z = 8.3891', 44) in drawn and ('≈ 0', 56) in drawn
            assert ('0.1192', 44) in drawn, 'earlier revealed weights persist'


def test_projection_focus_keeps_nonzero_terms_and_exact_signed_operands(fonts, monkeypatch):
    drawn = []
    original = renderer._Canvas.text
    def record(self, xy, text, *args, **kwargs):
        drawn.append(text)
        return original(self, xy, text, *args, **kwargs)
    monkeypatch.setattr(renderer._Canvas, 'text', record)
    data = dict(input_label='x', input=[0, -1, 0], lanes=[
        dict(id='p', label='P', matrix=[[1, 2], [math.sqrt(2), -1], [3, 4]])])
    v = visual('projection', data, [event('multiply'), event('result', at=2)])
    renderer.frame({}, scene(v), 0, 1, fonts)
    assert '−1 × (√2, −1)' in drawn
    assert not any(value.startswith('Σ =') for value in drawn)
    drawn.clear()
    renderer.frame({}, scene(v), 0, 2.5, fonts)
    assert 'Σ = (−√2, 1)' in drawn
    # Dense input uses the same authored lane focus; it must not discard terms.
    data['input'] = [2, -1, .5]
    drawn.clear()
    renderer.frame({}, scene(v), 0, 1, fonts)
    assert {'2 × (1, 2)', '−1 × (√2, −1)', '0.5 × (3, 4)'} <= set(drawn)


def test_comparison_does_not_show_after_weights_during_condition_change(fonts, monkeypatch):
    drawn = []
    original = renderer._Canvas.text
    def record(self, xy, text, *args, **kwargs):
        drawn.append(text)
        return original(self, xy, text, *args, **kwargs)
    monkeypatch.setattr(renderer._Canvas, 'text', record)
    items, before = cases()
    after = dict(before, label='after', query=[0, 1])
    v = visual('compare', dict(items=items, divisor=1, before=before, after=after, change='query'),
        [event('before'), event('change', at=1), event('after', at=2)])
    output = renderer._vec(visual_math(v)['cases'][1]['output'])
    for at in (.5, 1.5, 2.5):
        drawn.clear()
        renderer.frame({}, scene(v), 0, at, fonts)
        assert (output in drawn) == (at >= 2)
        assert ('after' in drawn) == (at >= 1)


def test_dot_sum_does_not_reveal_scaling_before_its_event(fonts, monkeypatch):
    text = []
    original = renderer._Canvas.text
    def record(self, xy, value, *args, **kwargs):
        text.append(value)
        return original(self, xy, value, *args, **kwargs)
    monkeypatch.setattr(renderer._Canvas, 'text', record)
    p, s = dict(title='test'), scene(dot())
    renderer.frame(p, s, 0, 2.5, fonts)
    assert '6 + (−4) = 2' in text
    assert not any('÷' in value or value == 'score' for value in text)
    text.clear()
    renderer.frame(p, s, 0, 3.5, fonts)
    assert '6 + (−4) = 2' in text and '2 ÷ 2 = 1' in text


def test_dot_narrow_math_uses_full_width_without_changing_event_order(fonts, monkeypatch):
    root = math.sqrt(2)
    v = visual('dot', dict(query_label='它', query=[root, 0], divisor=root,
        items=[dict(id='positive', label='小猫', vector=[2, 0]),
               dict(id='negative', label='因为', vector=[-1, 0])]), [
        event('vectors', at=.1), event('products', at=2.3), event('sum', at=3.775), event('result', at=6.475),
        event('vectors', 1, at=9.6), event('products', 1, at=10),
        event('sum', 1, at=11), event('result', 1, at=12.45)])
    s = {**scene(v), 'end': 17.664}
    prepared = renderer.prepare_frame(dict(title='fixed sample'), s, fonts)
    drawn = []
    original = renderer._Canvas.text
    def record(self, xy, text, color=renderer._INK, size=28, width=None, anchor=None):
        drawn.append((text, size, self.draw.textbbox(xy, text, font=self.fonts[size], anchor=anchor)))
        return original(self, xy, text, color, size, width, anchor)
    monkeypatch.setattr(renderer._Canvas, 'text', record)
    def at(time):
        drawn.clear()
        image = renderer.frame(dict(title='fixed sample'), s, 0, time, fonts, prepared)
        # Critical input and calculation lines are at least 19.5px at 480px wide;
        # they are complete, unellipsized and clear of the subtitle safe area.
        for text, size, box in drawn:
            if size >= 52:
                assert size * 480 / 1280 >= 19.5
                assert 0 <= box[0] < box[2] <= 1232, (text, box)
                assert 140 <= box[1] < box[3] < 605, (text, box)
        assert len(image.crop((0, 644, 1280, 720)).getcolors()) == 1
        return [text for text, _, _ in drawn]
    assert not any('×' in text or '÷' in text for text in at(.5))
    assert '√2 × 2 = 2√2' in at(2.5)
    assert '2√2 + 0 = 2√2' in at(4)
    assert '2√2 ÷ √2 = 2' in at(7)
    assert not any('×' in text or '求和' in text or '÷' in text for text in at(9.7))
    assert '√2 × (−1) = −√2' in at(10.5)
    assert not any('求和' in text or '÷' in text for text in at(10.99))
    assert '−√2 + 0 = −√2' in at(11.7)
    assert not any('÷' in text for text in at(12.449))
    assert '−√2 ÷ √2 = −1' in at(12.45)
    # Authored negative-key dwell remains 24 product-only frames and 35 sum frames.
    assert math.ceil(11*24)-math.ceil(10*24) == 24
    assert math.ceil(12.45*24)-math.ceil(11*24) == 35


def test_dot_radical_notation_does_not_claim_rounded_decimal_is_exact():
    assert renderer._dot_number(math.sqrt(2)) == '√2'
    assert renderer._dot_number(-math.sqrt(2)) == '−√2'
    assert renderer._dot_number(2*math.sqrt(2)) == '2√2'
    assert renderer._dot_number(0) == '0'
    assert renderer._dot_number(-1) == '−1'
    assert renderer._dot_number(1.4142) == '1.4142'
    assert '√' not in renderer._dot_number(math.sqrt(2)+1e-8)


def test_dot_vectors_and_six_score_terminal_remain_legible_at_480(fonts, monkeypatch):
    drawn = []
    original = renderer._Canvas.text
    def record(self, xy, text, color=renderer._INK, size=28, width=None, anchor=None):
        drawn.append((xy, text, size, self.draw.textbbox(xy, text, font=self.fonts[size], anchor=anchor)))
        return original(self, xy, text, color, size, width, anchor)
    monkeypatch.setattr(renderer._Canvas, 'text', record)
    v = visual('dot', dict(query_label='它', query=[math.sqrt(2), 0], divisor=math.sqrt(2),
        items=[dict(id=str(i), label=label, vector=vector) for i, (label, vector) in enumerate([
            ('小猫', [2, 0]), ('跳上', [0, 1]), ('沙发', [1, 1]),
            ('因为', [-1, 0]), ('它', [0, 0]), ('累了', [1, 2])])]), [event('vectors')])
    renderer.frame(dict(title='terminal fixture'), scene(v), 0, .5, fonts)
    assert all(size == 40 for _, text, size, _ in drawn if text in {'d1', 'd2', '0', '1', '2', '-1'})
    assert any(text == '= (√2, 0)' and size == 56 for _, text, size, _ in drawn)
    assert not any('÷' in text for _, text, _, _ in drawn)
    drawn.clear()
    v['events'] = [event('scores')]
    image = renderer.frame(dict(title='terminal fixture'), scene(v), 0, .5, fonts)
    scores = [(text, size, box) for xy, text, size, box in drawn if xy[0] == 1020]
    assert [text for text, _, _ in scores] == ['2', '0', '1', '−1', '0', '1']
    assert all(size * 480/1280 >= 16.5 for _, size, _ in scores)
    assert any(text == '÷ √2' and size == 44 for _, text, size, _ in drawn)
    for _, text, size, box in drawn:
        if size >= 44:
            assert 0 <= box[0] < box[2] <= 1232 and box[3] <= 605, (text, box)
    assert len(image.crop((0, 644, 1280, 720)).getcolors()) == 1


def test_mixed_scene_encoder_geometry_and_cache(tmp_path, fonts, monkeypatch):
    v = dot()
    v['events'] = [event('result', cue=0)]
    s = scene(v)
    s['end'] = 1
    graph = dict(title='legacy graph', speech='fixture', start=1, end=2, edges=[], values=[])
    plan = validate(dict(schema='portal-explanation-video/v1', title='test',
        nodes=[dict(id='a', label='a', x=.5, y=.5)], scenes=[s, graph],
        audio=dict(address='workspace_0123456789ab/file/a.wav', sha256='0'*64),
        cues=[dict(start=0, end=2, text='fixture')]))
    plan['_quality'] = 'low'  # Mixed legacy scenes use the low-quality rendering policy.
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(bytes(64000))
    class Sink:
        lengths = []
        def write(self, data): self.lengths.append(len(data))
        def close(self): pass
    class Process:
        def __init__(self, args, **kwargs):
            assert args[args.index('-s')+1] == '1280x720'
            self.stdin = sink
        def wait(self, **kwargs): return 0
        def poll(self): return 0
        def kill(self): pass
    sink = Sink()
    monkeypatch.setattr(renderer.subprocess, 'Popen', Process)
    calls, owner = [], renderer.visual_math
    def counted(v):
        calls.append(v['kind'])
        return owner(v)
    monkeypatch.setattr(renderer, 'visual_math', counted)
    renderer.render(plan, tmp_path, audio.getvalue())
    assert len(sink.lengths) == 24
    assert set(sink.lengths) == {1280*720*3}
    assert calls == ['dot']

