"""Synthetic plot contracts, never a game/music content or listening review."""
import copy
import math
from pathlib import Path

import pytest

from management_portal.home_explanation_plan import validate, validate_visual, visual_math, plot_state
from management_portal import home_explanation_render as renderer


def plot():
    return {'kind': 'plot', 'condition': '合成数据 · 校验绘图，不代表真实主题验收', 'data': {
        'parameter': {'label': '数据时间', 'unit': 's', 'domain': [0, 2]},
        'axes': {'x': {'label': '时间', 'unit': 's'}, 'y': {'label': '数值', 'unit': ''}},
        'views': [{'id': 'wide', 'label': '全貌', 'x': [0, 2], 'y': [-1, 1], 'aspect': 'free'},
                  {'id': 'zoom', 'label': '局部', 'x': [.25, .75], 'y': [.4, 1.2], 'aspect': 'equal'}],
        'series': [{'id': 'a', 'label': '分段线性信号', 'mode': 'linear',
                    'segments': [[[0, 0, 0], [.5, .5, 1], [1, 1, 0], [1.5, 1.5, -1], [2, 2, 0]]]},
                   {'id': 'b', 'label': '含缺测的信号', 'mode': 'linear',
                    'segments': [[[0, 0, -1], [.5, .5, 0]], [[1.5, 1.5, 0], [2, 2, 1]]]}]},
        'events': [{'at': 1, 'stage': 'trace', 'index': 0, 'duration': 2, 'cue': None,
                    'view': 'wide', 'visible': ['a', 'b'], 'range': [0, 2]},
                   {'at': 3, 'stage': 'inspect', 'index': 1, 'duration': 0, 'cue': None,
                    'view': 'wide', 'visible': ['a', 'b'], 'range': [0, 2], 'cursor': 1}]}


def checked(value=None):
    return validate_visual(value or plot(), 0, 4, [])


def scene(value):
    return {'title': '数值绘图 · 合成验证', 'speech': '合成验证', 'start': 0, 'end': 4, 'visual': value}


@pytest.fixture
def fonts():
    from PIL import ImageFont
    from management_portal.media_tools import capabilities
    path = capabilities()['font']
    if not Path(path).is_file():
        pytest.skip('managed CJK font unavailable')
    return {s: ImageFont.truetype(path, s) for s in (16, 20, 24)}


def test_samples_and_linear_interpolation_share_one_bounded_owner():
    v = checked()
    series = visual_math(v)['series']
    state = plot_state(series['a'], [.25, .75], .6)
    assert state['paths'] == [[[.25, .25, .5], [.5, .5, 1], [.75, .75, .5]]]
    assert state['sample'] == pytest.approx([.6, .6, .8])
    assert state['interpolated']
    series['a']['mode'] = 'samples'
    state = plot_state(series['a'], [.25, .75], .6)
    assert state == {'paths': [[[.5, .5, 1]]], 'sample': [.5, .5, 1], 'interpolated': False}
    assert plot_state(series['a'], [.6, .75], .65)['sample'] is None


def test_gaps_and_missing_sources_never_interpolate_or_extrapolate():
    source = visual_math(checked())['series']['b']
    assert len(plot_state(source, [0, 2])['paths']) == 2
    for cursor in (-1, .75, 1, 1.25, 3):
        assert plot_state(source, [0, 2], cursor)['sample'] is None
    assert plot_state(source, [.6, 1.4], 1) == {'paths': [], 'sample': None, 'interpolated': False}
    assert plot_state(source, [.5, .5], .5)['sample'] == [.5, .5, 0]


def test_parameter_order_allows_coordinate_turning():
    v = plot()
    v['data']['series'][0]['segments'] = [[[0, -1, -1], [.5, 1, -1], [1, 1, 1], [1.5, -1, 1], [2, -1, -1]]]
    source = visual_math(checked(v))['series']['a']
    assert plot_state(source, [0, 2], 1.75)['sample'] == [1.75, -1, 0]


@pytest.mark.parametrize('edit', [
    lambda v: v['data'].update(script='arbitrary()'),
    lambda v: v['data']['series'][0].update(mode='spline'),
    lambda v: v['data']['views'][0].update(aspect='auto'),
    lambda v: v['data']['series'][0]['segments'][0][1].__setitem__(0, 0),
    lambda v: v['data']['series'][0]['segments'][0][1].__setitem__(1, math.nan),
    lambda v: v['data']['series'][0]['segments'][0][1].__setitem__(2, math.inf),
    lambda v: v['data']['series'][0]['segments'][0][1].__setitem__(1, True),
    lambda v: v['data']['series'][0]['segments'][0][1].__setitem__(2, 1e10),
    lambda v: v['data']['series'][0]['segments'][0][0].__setitem__(0, -1),
    lambda v: v['data']['series'][1].update(id='a'),
    lambda v: v['data']['views'][1].update(id='wide'),
    lambda v: v['data']['views'][0].update(x=[1, 1]),
    lambda v: v['events'][0].update(visible=['a', 'a']),
    lambda v: v['events'][0].update(visible=['b']),
    lambda v: v['events'][0].update(view='missing'),
    lambda v: v['events'][0].update(range=[0, 3]),
    lambda v: v['events'][0].update(range=[1, 1]),
    lambda v: v['events'][0].update(cursor=.5),
    lambda v: v['events'][0].update(duration=0),
    lambda v: v['events'][1].pop('cursor'),
    lambda v: v['events'][1].update(cursor=3),
    lambda v: v['events'][1].update(duration=1),
    lambda v: v['events'][1].update(at=2),
])
def test_plot_rejects_undeclared_or_unbounded_data(edit):
    v = plot()
    edit(v)
    with pytest.raises(ValueError):
        checked(v)


def test_series_segment_and_whole_plan_budgets():
    v = plot()
    v['data']['series'] = [v['data']['series'][0]]
    v['events'] = [v['events'][0]]
    v['events'][0]['visible'] = ['a']
    v['data']['series'][0]['segments'] = [[[2*i/2047, 0, 0] for i in range(2048)]]
    checked(v)
    bad = copy.deepcopy(v)
    bad['data']['series'][0]['segments'][0].append([2.001, 0, 0])
    with pytest.raises(ValueError):
        checked(bad)
    bad = copy.deepcopy(v)
    bad['data']['series'][0]['segments'] = [[[i/16, 0, 0]] for i in range(17)]
    with pytest.raises(ValueError):
        checked(bad)
    plan = {'schema': 'portal-explanation-video/v1', 'title': 'budget', 'scenes': []}
    for i in range(5):
        s = scene(copy.deepcopy(v))
        s.update(start=i*4, end=(i+1)*4)
        s['visual']['events'][0]['at'] += i*4
        plan['scenes'].append(s)
    with pytest.raises(ValueError, match='8192'):
        validate(plan)
    plan['scenes'].pop()
    assert len(validate(plan)['scenes']) == 4


def test_plot_clock_cues_and_non_plot_event_contract():
    v = plot()
    v['events'][0]['cue'] = 0
    cues = [{'start': 1, 'end': 3, 'text': '合成时序夹具'}]
    out = validate_visual(v, 0, 4, cues)
    assert renderer.visual_event(out, .9) == (None, 0)
    assert renderer.visual_event(out, 1.5)[1] == .25  # not smoothstep .15625
    assert renderer.visual_event(out, 2.5)[1] == .75
    v['events'][0]['cue'] = None
    with pytest.raises(ValueError, match='静默'):
        validate_visual(v, 0, 4, cues)
    other = {'kind': 'sequence', 'condition': 'test', 'data': {'items': [{'id': 'a', 'label': 'a', 'detail': 'a'}], 'note': 'a'},
             'events': [{'at': 0, 'stage': 'focus', 'index': 0, 'duration': 2, 'cue': None}]}
    assert renderer.visual_event(other, .5)[1] == .15625
    other['events'][0]['range'] = [0, 1]
    with pytest.raises(ValueError):
        checked(other)


def test_fixed_domains_equal_units_and_source_space_clipping():
    v = checked()
    free = renderer._plot_geometry(v['data']['views'][0])
    assert renderer._plot_point(free, (0, -1)) == (160, 490)
    assert renderer._plot_point(free, (2, 1)) == (1190, 228)
    equal = renderer._plot_geometry(v['data']['views'][1])
    assert equal['scale'][0] == equal['scale'][1]
    assert renderer._clip_plot_line((-1e9, 0), (1e9, 0), (0, -1, 2, 1)) == ((0, 0), (2, 0))
    assert renderer._clip_plot_line((-2, 2), (3, 2), (0, -1, 2, 1)) is None
    assert renderer._clip_plot_line((-1e9, -1e9), (1e9, 1e9), (0, 0, 1e-9, 1e-9)) == ((0, 0), (1e-9, 1e-9))


def test_raster_seek_readouts_narrow_and_subtitle_safe(fonts, monkeypatch):
    v = checked()
    s = scene(v)
    prepared = renderer.prepare_frame({}, s, fonts)
    seen, texts = [], []
    original, text = renderer.plot_state, renderer._Canvas.text
    def state(series, window, cursor):
        seen.append((window, cursor))
        return original(series, window, cursor)
    def record(self, xy, value, color=renderer._INK, size=28, width=None, anchor=None):
        texts.append((value, size))
        return text(self, xy, value, color, size, width, anchor)
    monkeypatch.setattr(renderer, 'plot_state', state)
    monkeypatch.setattr(renderer._Canvas, 'text', record)
    # No model/math re-preparation during repeated frames.
    monkeypatch.setattr(renderer, 'visual_math', lambda *a: pytest.fail('per-frame math'))
    before = renderer.frame({}, s, 0, .5, fonts, prepared)
    a = renderer.frame({}, s, 0, 1.5, fonts, prepared)
    assert seen == [([0., .5], .5), ([0., .5], .5)]
    assert ('u ≈ 0.5', 40) in texts and ('y ≈ 1', 40) in texts
    gap = renderer.frame({}, s, 0, 3.5, fonts, prepared)
    assert any('无样本' in t for t, _ in texts)
    assert a.tobytes() == renderer.frame({}, s, 0, 1.5, fonts, prepared).tobytes()
    assert before.tobytes() != a.tobytes() != gap.tobytes()
    assert len(a.crop((0, 644, 1280, 720)).getcolors()) == 1
    assert a.resize((480, 270)).getbbox() == (0, 0, 480, 270)
    assert all(size >= 32 for t, size in texts if t.startswith(('u ≈', 'x ≈', 'y ≈')))


def test_discrete_points_do_not_draw_a_continuous_line(fonts, monkeypatch):
    v = plot()
    v['data']['series'][0]['mode'] = 'samples'
    v['events'] = [{**v['events'][0], 'stage': 'overview', 'duration': 0, 'visible': ['a']}]
    lines, original = [], renderer._Canvas.line
    def record(self, a, b, color=renderer._MUTED, width=2):
        lines.append(color)
        return original(self, a, b, color, width)
    monkeypatch.setattr(renderer._Canvas, 'line', record)
    renderer.frame({}, scene(checked(v)), 0, 1, fonts)
    assert renderer._COLORS[0] not in lines
