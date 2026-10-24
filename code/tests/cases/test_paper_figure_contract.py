from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image, ImageDraw

from management_portal import paper_backend, paper_projection
from tests.cases.test_paper_figure_transactions import figure_store


RESULT_SCHEMA = 'tfi.paper.figure-explanation.result.v1'


@pytest.mark.parametrize('dpi', [120, 180])
def test_heading_chart_keeps_unprefixed_footer_source_pixels(tmp_path, monkeypatch, dpi):
    """保留原图的无前缀完整句图注，包括数值、坐标和解释，不带入后续正文。

    :importance: P1
    :design: decision_e37f240e186d
    :design-point:
    """
    source = Path(__file__).parent / 'fixtures' / 'paper-synthetic-counts.pdf'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        'eac5fe85f2b77c8f8e78fe41ddc5397614fc19ab0883a9b9ee0b0130f8b7debf'
    )
    caption = next(item for item in paper_backend._prepare_figure_caption_blocks(
        paper_backend._extract_figure_caption_blocks(source)) if item['page'] == 2)
    rendered = paper_backend._render_pdf_page_png(source, 2, tmp_path / 'page', dpi=dpi)
    with Image.open(rendered) as opened:
        page = opened.convert('RGB')
    origins = {id(page): (0, 0)}
    tracked = [page]
    original_crop = Image.Image.crop

    def crop_with_origin(image, box, *args, **kwargs):
        result = original_crop(image, box, *args, **kwargs)
        if id(image) in origins:
            left, top = origins[id(image)]
            origins[id(result)] = (left + box[0], top + box[1])
            tracked.append(result)
        return result

    monkeypatch.setattr(Image.Image, 'crop', crop_with_origin)
    result = paper_backend._crop_figure_above_caption(page, caption)
    assert result is not None
    left, top = origins[id(result)]
    right, bottom = left + result.width, top + result.height
    scale_x, scale_y = page.width / caption['page_width'], page.height / caption['page_height']
    figure_lines = [line for line in caption['page_lines'] if 336 <= line['y_min'] <= 707]
    assert {
        'Difference = 6 markers; relative increase = 50%.',
        'Numbers are not empirical performance claims.', 'Baseline', 'Treatment', '12', '18',
    } <= {line['text'] for line in figure_lines}
    for line in figure_lines:
        box = (int(line['x_min'] * scale_x), int(line['y_min'] * scale_y),
               int(line['x_max'] * scale_x) + 1, int(line['y_max'] * scale_y) + 1)
        assert left <= box[0] < box[2] <= right, line['text']
        assert top <= box[1] < box[3] <= bottom, line['text']
        assert result.crop((box[0] - left, box[1] - top,
                            box[2] - left, box[3] - top)).tobytes() == page.crop(box).tobytes()
    # The preceding limitations and following full-width text are not figure annotations.
    assert top > 223 * scale_y
    assert bottom < 741 * scale_y


@pytest.mark.parametrize('scale', [1, 2])
@pytest.mark.parametrize('note_gap', [31, 90])
def test_heading_chart_groups_aligned_footer_sentences_across_label_whitespace(scale, note_gap):
    """图下留白较大时保留紧凑对齐的双行说明，仍排除远处正文。"""
    page = Image.new('RGB', (600 * scale, 800 * scale), 'white')
    draw = ImageDraw.Draw(page)
    draw.rectangle(tuple(v * scale for v in (100, 200, 450, 400)), fill='teal')
    note_top = 424 + note_gap
    lines = [
        {'text': 'Figure 1. Invented scores', 'x_min': 60, 'x_max': 480, 'y_min': 165, 'y_max': 180},
        {'text': 'Method A / Method B', 'x_min': 100, 'x_max': 450, 'y_min': 412, 'y_max': 424},
        {'text': 'Difference = 6 markers; relative increase = 50%.',
         'x_min': 90, 'x_max': 410, 'y_min': note_top, 'y_max': note_top + 8},
        {'text': 'Numbers are not empirical performance claims.',
         'x_min': 90, 'x_max': 395, 'y_min': note_top + 18, 'y_max': note_top + 26},
        {'text': 'Acceptance: preserve all titles, labels, axes and explanations.',
         'x_min': 60, 'x_max': 550, 'y_min': note_top + 68, 'y_max': note_top + 80},
    ]
    colors = ['black', 'blue', 'green', 'red', 'magenta']
    for line, color in zip(lines, colors):
        draw.rectangle(tuple(line[k] * scale for k in ('x_min', 'y_min', 'x_max', 'y_max')), fill=color)
    caption = {**lines[0], 'caption': lines[0]['text'], 'number': '1',
               'page_width': 600, 'page_height': 800, 'page_lines': lines}
    result = paper_backend._crop_figure_above_caption(page, caption)
    assert result is not None
    for color in ((0, 128, 0), (255, 0, 0)):
        expected = list(page.getdata()).count(color) if note_gap == 31 else 0
        assert list(result.getdata()).count(color) == expected
    assert (255, 0, 255) not in result.getdata()


@pytest.mark.parametrize('scale', [1, 2])
def test_heading_chart_keeps_title_and_explicit_footer_without_body(scale):
    """标题在上方的图表裁剪保留标题、图例和图注，排除其后的正文"""
    page = Image.new('RGB', (600 * scale, 800 * scale), 'white')
    draw = ImageDraw.Draw(page)
    draw.rectangle(tuple(v * scale for v in (100, 200, 450, 400)), fill='teal')
    lines = [
        {'text': 'Figure 1. Invented scores', 'x_min': 60, 'x_max': 480, 'y_min': 165, 'y_max': 180},
        {'text': 'Method A / Method B', 'x_min': 100, 'x_max': 450, 'y_min': 412, 'y_max': 424},
        {'text': 'Caption: fabricated values A=10, B=12; difference=2 units (20%).',
         'x_min': 60, 'x_max': 500, 'y_min': 440, 'y_max': 452},
        {'text': 'This unrelated body paragraph must stay outside the extracted image.',
         'x_min': 60, 'x_max': 550, 'y_min': 475, 'y_max': 490},
    ]
    for line, color in zip(lines, ['red', 'blue', 'green', 'magenta']):
        draw.rectangle(tuple(line[k] * scale for k in ('x_min', 'y_min', 'x_max', 'y_max')), fill=color)
    caption = {**lines[0], 'caption': lines[0]['text'], 'number': '1',
               'page_width': 600, 'page_height': 800, 'page_lines': lines}
    result = paper_backend._crop_figure_above_caption(page, caption)
    assert result is not None
    for color in ((255, 0, 0), (0, 0, 255), (0, 128, 0), (0, 128, 128)):
        assert list(result.getdata()).count(color) == list(page.getdata()).count(color)
    assert (255, 0, 255) not in result.getdata()


@pytest.mark.parametrize('framed', [True, False])
def test_heading_inside_frame_is_not_a_caption_below_another_figure(framed):
    """图标题位于边框内时裁出整个框内图，仅有上方分隔线时不裁剪"""
    page = Image.new('RGB', (600, 800), 'white')
    draw = ImageDraw.Draw(page)
    draw.line((40, 380, 560, 380), fill='gray', width=2)
    if framed:
        draw.rectangle((40, 380, 560, 640), outline='gray', width=2)
    draw.rectangle((50, 450, 350, 480), fill='teal')
    draw.rectangle((50, 510, 410, 540), fill='navy')
    lines = [
        {'text': 'Figure 1: Throughput', 'x_min': 50, 'x_max': 300, 'y_min': 395, 'y_max': 410},
        {'text': 'Both bars share a zero origin.', 'x_min': 50, 'x_max': 400, 'y_min': 565, 'y_max': 578},
        {'text': 'Synthetic sample: not scientific evidence.', 'x_min': 50, 'x_max': 480, 'y_min': 600, 'y_max': 613},
    ]
    for line, color in zip(lines, ['black', 'green', 'red']):
        draw.rectangle((line['x_min'], line['y_min'], line['x_max'], line['y_max']), fill=color)
    caption = {**lines[0], 'caption': lines[0]['text'], 'number': '1',
               'page_width': 600, 'page_height': 800, 'page_lines': lines}
    result = paper_backend._crop_figure_below_heading(page, caption)
    if not framed:
        # A separate rule above the caption provides no evidence of an enclosing frame.
        assert result is None
        return
    assert result is not None
    colors = set(result.getdata())
    assert {(0, 128, 128), (0, 0, 128), (0, 128, 0), (255, 0, 0)} <= colors
    assert 260 <= result.height < 310


def test_heading_figure_keeps_annotation_and_excludes_following_section():
    """标题在上方的图保留紧随的说明文字，排除下一节的标题和正文"""
    page = Image.new('RGB', (600, 800), 'white')
    draw = ImageDraw.Draw(page)
    draw.rectangle((50, 80, 300, 110), fill='teal')
    draw.rectangle((50, 130, 350, 160), fill='navy')
    lines = [
        {'text': 'Figure 1: Throughput', 'x_min': 50, 'x_max': 300, 'y_min': 40, 'y_max': 55},
        {'text': 'Baseline: 10 units/s', 'x_min': 55, 'x_max': 250, 'y_min': 85, 'y_max': 97},
        {'text': 'Cache: 12 units/s', 'x_min': 55, 'x_max': 250, 'y_min': 135, 'y_max': 147},
        {'text': 'The second bar is longer than the first by twenty', 'x_min': 50, 'x_max': 500, 'y_min': 174, 'y_max': 185},
        {'text': 'percent. The colors only distinguish the two methods.', 'x_min': 50, 'x_max': 500, 'y_min': 186, 'y_max': 197},
        {'text': 'Discussion', 'x_min': 50, 'x_max': 230, 'y_min': 210, 'y_max': 227},
        {'text': 'This unrelated paragraph must remain outside the extracted figure.', 'x_min': 50, 'x_max': 550, 'y_min': 239, 'y_max': 251},
    ]
    for line, color in zip(lines, ['black', 'white', 'white', 'black', 'green', 'magenta', 'red']):
        draw.rectangle((line['x_min'], line['y_min'], line['x_max'], line['y_max']), fill=color)
    caption = {**lines[0], 'caption': lines[0]['text'], 'number': '1',
               'page_width': 600, 'page_height': 800, 'page_lines': lines}
    result = paper_backend._crop_figure_above_caption(page, caption)
    assert result is not None
    colors = set(result.getdata())
    assert (0, 128, 0) in colors
    assert (255, 0, 255) not in colors
    assert (255, 0, 0) not in colors


def test_figure_crop_preserves_sparse_title_pixels_below_distant_body_text():
    """图片裁剪保留像素稀疏的标题顶部，不纳入上方较远的正文"""
    page = Image.new('RGB', (600, 800), 'white')
    draw = ImageDraw.Draw(page)
    draw.rectangle((40, 50, 540, 62), fill='black')
    # A glyph's top can occupy fewer pixels than the row-detection threshold.
    draw.point((280, 200), fill=(200, 0, 0))
    draw.rectangle((275, 201, 290, 212), fill='black')
    draw.rectangle((50, 230, 540, 430), outline='black', width=2)
    caption = {'page_width': 600, 'page_height': 800,
               'x_min': 40, 'x_max': 550, 'y_min': 500, 'y_max': 520,
               'caption': 'Figure 1. Measured signal.', 'number': '1',
               'page_lines': [{'text': 'This is preceding body prose that is separate from the chart.',
                               'x_min': 40, 'x_max': 540, 'y_min': 50, 'y_max': 62}]}
    result = paper_backend._crop_figure_above_caption(page, caption)
    assert result is not None
    assert (200, 0, 0) in result.getdata()
    # The unrelated prose must remain outside the crop.
    assert result.height < 300
    assert all(pixel == (255, 255, 255) for pixel in result.crop((0, 0, result.width, 1)).getdata())


def test_heading_figure_padding_does_not_cut_through_excluded_prose():
    """标题在上方的图裁剪留白时不切入被排除的正文行"""
    page = Image.new('RGB', (600, 800), 'white')
    draw = ImageDraw.Draw(page)
    draw.rectangle((50, 80, 350, 160), fill='teal')
    draw.rectangle((50, 175, 550, 189), fill='red')
    caption = {'page_width': 600, 'page_height': 800, 'number': '1',
               'caption': 'Figure 1: Throughput', 'x_min': 50, 'x_max': 300,
               'y_min': 40, 'y_max': 55,
               'page_lines': [
                   {'text': 'Figure 1: Throughput', 'x_min': 50, 'x_max': 300, 'y_min': 40, 'y_max': 55},
                   {'text': 'This following body paragraph must not enter the figure as partial text.',
                    'x_min': 50, 'x_max': 550, 'y_min': 175, 'y_max': 189},
               ]}
    result = paper_backend._crop_figure_above_caption(page, caption)
    assert result is not None
    assert (0, 128, 128) in result.getdata()
    assert (255, 0, 0) not in result.getdata()


@pytest.fixture
def explanation(figure_store, monkeypatch):
    paper, path = figure_store
    paper['created_by'] = 'test-actor'
    image = path.parent / 'figure.png'
    Image.new('RGB', (20, 20), 'white').save(image)
    monkeypatch.setattr(paper_backend, '_figure_image_path', lambda *args: image)
    payload = {'schema': RESULT_SCHEMA, 'paper_id': paper['id'], 'figure_id': 'fig-001',
               'status': 'generated', 'explanation': '## 图在表达什么\n合成样本 A=20 ms，B=10 ms。', 'error': ''}
    generate = Mock(side_effect=lambda *args, **kwargs: (json.dumps(payload, ensure_ascii=False), {'message_id': 'msg_fixture'}))
    monkeypatch.setattr(paper_backend, '_run_figure_resident', generate)
    return paper, path, payload, generate


def test_explanation_generation_and_formal_save_have_distinct_verified_states(explanation):
    """图解释的生成与正式保存分别记录状态，保存后附带内容哈希回执"""
    paper, path, payload, generate = explanation
    response = paper_backend.run_figure_explain_job(paper['id'], 'fig-001', 'test-actor')
    stored = json.loads(path.read_text())[0]
    assert stored['explanation'] == payload['explanation']
    assert stored['explanation_status'] == 'done'
    assert stored['explanation_generation_status'] == 'generated'
    assert stored['explanation_persistence_status'] == 'saved'
    receipt = stored['explanation_commit']
    assert receipt['paper_id'] == paper['id'] and receipt['figure_id'] == 'fig-001'
    assert receipt['content_sha256'] == hashlib.sha256(payload['explanation'].encode()).hexdigest()
    assert receipt['committed_by'] == 'test-actor'
    assert response['figure']['explanation_commit'] == receipt
    prompt = generate.call_args.args[0]
    assert RESULT_SCHEMA in prompt and paper['id'] in prompt and 'fig-001' in prompt
    assert 'Paper 服务负责校验和正式保存' in prompt


@pytest.mark.parametrize('change', [
    {'schema': 'unknown'}, {'paper_id': 'paper_ffffffffffff'}, {'figure_id': 'other-figure'},
    {'status': 'done'}, {'status': 'error', 'error': 'Cannot read supplied image'},
    {'explanation': ''}, {'explanation': {'text': 'not a string'}},
])
def test_invalid_or_failed_generation_is_never_saved_as_done(explanation, change):
    """图解释结果格式不符、ID 不匹配、报错或内容为空时不会保存为完成"""
    paper, path, payload, _ = explanation
    payload.update(change)
    result = paper_backend.run_figure_explain_job(paper['id'], 'fig-001', 'test-actor')['figure']
    assert result['explanation_status'] == 'error'
    assert result['explanation_generation_status'] == 'error'
    assert result['explanation_persistence_status'] == 'not_saved'
    assert result['explanation_error']
    assert not json.loads(path.read_text())[0].get('explanation')


def test_free_text_is_not_mistaken_for_a_structured_generation_receipt(explanation):
    """自由文本回复不被当作结构化生成结果，图解释记为失败且不保存"""
    paper, path, _, generate = explanation
    generate.side_effect = None
    generate.return_value = ('正式产物提交未执行。以下为解读正文，请补充提交协议。', {})
    result = paper_backend.run_figure_explain_job(paper['id'], 'fig-001', 'test-actor')['figure']
    assert result['explanation_status'] == 'error'
    assert not json.loads(path.read_text())[0].get('explanation')


def test_contract_validation_does_not_filter_phrases_from_valid_content(explanation):
    """合法的图解释即使含有“正式产物提交未执行”等字样也原样保存"""
    paper, path, payload, _ = explanation
    payload['explanation'] = '## 图在表达什么\n图中文字引用了“正式产物提交未执行”，这是待解释的原文。'
    paper_backend.run_figure_explain_job(paper['id'], 'fig-001', 'test-actor')
    assert json.loads(path.read_text())[0]['explanation'] == payload['explanation']


def test_successful_generation_does_not_hide_a_failed_artifact_write(explanation, monkeypatch):
    """图解释生成成功但写入失败时报告保存错误，不留下解释内容"""
    paper, path, _, _ = explanation
    write = paper_backend._write_json

    def fail_commit(target, figures):
        if figures[0].get('explanation_persistence_status') == 'saved':
            raise OSError('fixture disk full')
        return write(target, figures)

    monkeypatch.setattr(paper_backend, '_write_json', fail_commit)
    result = paper_backend.run_figure_explain_job(paper['id'], 'fig-001', 'test-actor')['figure']
    assert result['explanation_status'] == 'error'
    assert result['explanation_generation_status'] == 'generated'
    assert result['explanation_persistence_status'] == 'error'
    assert 'disk full' in result['explanation_error']
    stored = json.loads(path.read_text())[0]
    assert not stored.get('explanation') and not stored.get('explanation_commit')


def test_notification_failure_after_durable_commit_does_not_falsely_report_unsaved(explanation, monkeypatch):
    """图解释已持久保存后通知失败，仍报告为已保存"""
    paper, path, payload, _ = explanation
    monkeypatch.setattr(paper_backend, '_notify_paper_projection', Mock(side_effect=RuntimeError('notification unavailable')))
    result = paper_backend.explain_figure({'paper_id': paper['id'], 'figure_id': 'fig-001'}, 'test-actor')['figure']
    assert result['explanation_status'] == 'done'
    assert result['explanation_persistence_status'] == 'saved'
    assert json.loads(path.read_text())[0]['explanation'] == payload['explanation']


def test_an_old_identical_artifact_is_not_a_receipt_for_a_new_unwritten_commit(explanation, monkeypatch):
    """新一次图解释未能写入时报错，内容相同的旧产物不能充当保存回执"""
    paper, path, _, _ = explanation
    payload = {'paper_id': paper['id'], 'figure_id': 'fig-001'}
    paper_backend.explain_figure(payload, 'test-actor')
    original = path.read_bytes()
    monkeypatch.setattr(paper_backend, '_write_figures', lambda *args: None)
    with pytest.raises(RuntimeError, match='persist'):
        paper_backend.explain_figure(payload, 'test-actor')
    assert path.read_bytes() == original


def test_fixed_effect_supports_direct_lineage_use_without_page_submission_protocol(monkeypatch):
    """Lineage 直接调用图解释角色时提示词说明没有提交协议，不按生成失败处理"""
    native = Mock(return_value={'message': 'Direct figure explanation', 'resident_id': 'resident_fixture', 'message_id': 'msg_input_fixture'})
    monkeypatch.setattr(paper_projection.native, 'turn', native)
    result = paper_projection.turn(actor='test-actor', resident_scope='paper:figure', prompt='Explain the attached chart')
    prompt = native.call_args.kwargs['workflow']['prompt']
    assert '未提供提交协议' in prompt
    assert '不视为生成失败' in prompt
    assert '只有取得业务保存回执' in prompt
    assert '必须按本次要求提交至正式产物位置' not in prompt
    assert result['message'] == 'Direct figure explanation'


def write_synthetic_axes_pdf(path: Path) -> None:
    def text(content, horizontal, vertical, size=12):
        escaped = content.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        return f'BT /F1 {size} Tf {horizontal} {vertical} Td ({escaped}) Tj ET\n'

    first = text('TAU SYNTHETIC SAMPLE - NOT A REAL PAPER', 45, 790, 18)
    first += text('All data are fabricated for a two-page figure crop regression.', 45, 720)
    second = text('TAU SYNTHETIC SAMPLE - FIGURE PAGE', 45, 790, 18)
    second += text('Figure 1. Toy mean latency: lower is faster.', 45, 705, 16)
    second += '0 0 0 RG 2 w 100 300 m 100 620 l S 100 300 m 490 300 l S\n'
    second += '0.1 0.55 0.6 rg 170 300 90 240 re f\n0.9 0.5 0.15 rg 340 300 90 120 re f\n0 0 0 rg\n'
    for label, horizontal, vertical in [('20 ms', 190, 555), ('10 ms', 355, 435),
                                        ('Algorithm A', 170, 275), ('Algorithm B', 340, 275),
                                        ('0', 75, 295), ('10', 70, 415), ('20', 70, 535), ('Latency (ms)', 45, 645)]:
        second += text(label, horizontal, vertical)
    second += text('The teal bar is twice as high as the orange bar.', 45, 220)
    second += text('This visual encodes A=20 ms and B=10 ms; B uses half the time.', 45, 198)
    second += text('Page 2 of 2 - TAU synthetic content', 45, 45)
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>']
    for content_id in (6, 7):
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents {content_id} 0 R >>'.encode())
    objects.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    for content in (first, second):
        encoded = content.encode('ascii')
        objects.append(f'<< /Length {len(encoded)} >>\nstream\n'.encode() + encoded + b'endstream')
    output = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, item in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f'{index} 0 obj\n'.encode() + item + b'\nendobj\n')
    xref = len(output)
    output.extend(f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        output.extend(f'{offset:010d} 00000 n \n'.encode())
    output.extend(f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    path.write_bytes(output)


@pytest.mark.parametrize('dpi', [72, 160])
def test_real_pdf_crop_keeps_axis_title_ticks_and_categories_without_body(tmp_path, monkeypatch, dpi):
    """从真实 PDF 裁剪图时保留坐标轴标题、刻度和类别标签，不含正文"""
    source = tmp_path / 'tau-synthetic-two-pages.pdf'
    write_synthetic_axes_pdf(source)
    captions = paper_backend._extract_figure_caption_blocks(source)
    assert len(captions) == 1 and captions[0]['page'] == 2
    caption = captions[0]
    rendered = paper_backend._render_pdf_page_png(source, 2, tmp_path / 'rendered', dpi=dpi)
    with Image.open(rendered) as opened:
        page = opened.convert('RGB')
    captured = []
    crop = Image.Image.crop

    def capture(image, box):
        if image is page:
            captured.append(box)
        return crop(image, box)

    monkeypatch.setattr(Image.Image, 'crop', capture)
    result = paper_backend._crop_figure_above_caption(page, caption)
    assert result is not None
    left, top, right, bottom = captured[-1]
    scale_x = page.width / caption['page_width']
    scale_y = page.height / caption['page_height']
    body = next(line for line in caption['page_lines'] if line['text'].startswith('The teal bar'))
    for label in ('Latency (ms)', 'Algorithm A', 'Algorithm B', '0', '10', '20', '20 ms', '10 ms'):
        matching = [line for line in caption['page_lines'] if label in line['text'] and line['y_min'] < body['y_min']]
        assert matching, label
        for line in matching:
            assert left <= line['x_min'] * scale_x and right >= line['x_max'] * scale_x, label
            assert top <= line['y_min'] * scale_y and bottom >= line['y_max'] * scale_y, label
    assert bottom < body['y_min'] * scale_y

@pytest.mark.parametrize('dpi', [72, 160])
def test_numbered_chart_heading_above_matching_caption_keeps_source_title(tmp_path, monkeypatch, dpi):
    """图标题与同编号图注之间的完整图像裁剪

    真实 PDF 的图标题与图形有空白间隔时保留完整标题像素，并排除下方图注。

    :design: decision_e37f240e186d
    :design-point:
    """
    source = Path(__file__).parent / 'fixtures' / 'paper-synthetic-heading-caption.pdf'
    captions = paper_backend._extract_figure_caption_blocks(source)
    lower, = [item for item in captions if item['page'] == 2]
    heading = next(line for line in lower['page_lines'] if line['text'] == 'Figure 1. Invented group means')
    rendered = paper_backend._render_pdf_page_png(source, 2, tmp_path / 'page', dpi=dpi)
    with Image.open(rendered) as opened:
        page = opened.convert('RGB')
    origins = {id(page): (0, 0)}
    tracked = [page]
    original_crop = Image.Image.crop

    def capture(image, box):
        result = original_crop(image, box)
        if id(image) in origins:
            left, top = origins[id(image)]
            origins[id(result)] = (left + box[0], top + box[1])
            tracked.append(result)
        return result

    monkeypatch.setattr(Image.Image, 'crop', capture)
    result = paper_backend._crop_figure_above_caption(page, lower)
    assert result is not None
    left, top = origins[id(result)]
    right, bottom = left + result.width, top + result.height
    sx, sy = page.width / lower['page_width'], page.height / lower['page_height']
    title = (int(heading['x_min'] * sx), int(heading['y_min'] * sy),
             int(heading['x_max'] * sx) + 1, int(heading['y_max'] * sy) + 1)
    assert left <= title[0] < title[2] <= right
    assert top <= title[1] < title[3] <= bottom
    assert result.crop((title[0] - left, title[1] - top, title[2] - left, title[3] - top)).tobytes() == page.crop(title).tobytes()
    assert bottom <= lower['y_min'] * sy


@pytest.mark.parametrize('dpi', [72, 160])
def test_short_caption_real_pdf_preserves_complete_chart(tmp_path, monkeypatch, dpi):
    """短图注的真实 PDF 裁图保留完整图形

    在72和160 DPI核对真实源图像素，完整保留标题、刻度、两根柱形、标签和数值，同时排除邻近表格和图注。

    :importance: P1
    :design: decision_e37f240e186d
    :design-point:
    """
    # Exact synthetic PDF from the repaired TAU, archived as an isolated fixture.
    source = Path(__file__).parent / 'fixtures' / 'paper-synthetic-means.pdf'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        '4c2230d239cc25068987fe73988d5435c1beacadb9410b59e8d3c9e1e012f49c'
    )
    captions = paper_backend._prepare_figure_caption_blocks(
        paper_backend._extract_figure_caption_blocks(source)
    )
    caption = next(item for item in captions if item['page'] == 2)
    rendered = paper_backend._render_pdf_page_png(source, 2, tmp_path / 'page', dpi=dpi)
    with Image.open(rendered) as opened:
        page = opened.convert('RGB')
    origins = {id(page): (0, 0)}
    tracked = [page]
    original_crop = Image.Image.crop

    def crop_with_origin(image, box, *args, **kwargs):
        result = original_crop(image, box, *args, **kwargs)
        if id(image) in origins:
            left, top = origins[id(image)]
            origins[id(result)] = (left + box[0], top + box[1])
            tracked.append(result)
        return result

    monkeypatch.setattr(Image.Image, 'crop', crop_with_origin)
    result = paper_backend._crop_figure_above_caption(page, caption)
    assert result is not None
    left, top = origins[id(result)]
    right, bottom = left + result.width, top + result.height
    scale_x, scale_y = page.width / caption['page_width'], page.height / caption['page_height']
    title = next(line for line in caption['page_lines'] if line['text'] == 'TAU synthetic means')
    chart_lines = [
        line for line in caption['page_lines']
        if title['y_min'] <= line['y_min'] and line['y_max'] < caption['y_min']
    ]
    assert {'TAU synthetic means', 'Arbitrary score', 'A', 'B', '10', '12', '14'} <= {
        line['text'] for line in chart_lines
    }
    assert sum(line['text'] == '12' for line in chart_lines) == 2

    def assert_source_pixels_preserved(box):
        assert left <= box[0] < box[2] <= right
        assert top <= box[1] < box[3] <= bottom
        assert result.crop((box[0] - left, box[1] - top, box[2] - left, box[3] - top)).tobytes() == page.crop(box).tobytes()

    for line in chart_lines:
        box = (int(line['x_min'] * scale_x), int(line['y_min'] * scale_y),
               int(line['x_max'] * scale_x) + 1, int(line['y_max'] * scale_y) + 1)
        ink = page.crop(box).convert('L').point(lambda value: 255 if value < 245 else 0).getbbox()
        assert ink, line['text']
        assert_source_pixels_preserved((box[0] + ink[0], box[1] + ink[1],
                                        box[0] + ink[2], box[1] + ink[3]))

    # Both bars, including their full width and height, must survive as source pixels.
    from PIL import ImageChops
    red, green, blue = page.split()
    for mask in (
        ImageChops.multiply(red.point(lambda v: 255 if v > 180 else 0),
                            blue.point(lambda v: 255 if v < 100 else 0)),
        ImageChops.multiply(blue.point(lambda v: 255 if v > 140 else 0),
                            red.point(lambda v: 255 if v < 100 else 0)),
    ):
        box = mask.getbbox()
        assert box
        assert_source_pixels_preserved(box)
    preceding_end = max(line['y_max'] for line in caption['page_lines'] if line['y_max'] < title['y_min'])
    assert top > preceding_end * scale_y
    assert bottom < caption['y_min'] * scale_y
