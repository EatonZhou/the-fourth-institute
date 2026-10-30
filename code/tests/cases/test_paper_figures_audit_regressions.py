"""Extraction commits and Figure identity; all filesystem writes are isolated."""
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from management_portal import paper_figure_extraction, paper_figure_jobs, paper_figures, paper_store


def noisy_image(seed=1):
    return Image.frombytes('RGB', (240, 180), random.Random(seed).randbytes(240 * 180 * 3))


def test_paper_root_resolves_symlinked_items_directory(tmp_path):
    """论文根路径解析符号链接指向的条目目录"""
    physical_items = tmp_path / 'physical-items'
    physical_items.mkdir()
    logical_items = tmp_path / 'items'
    logical_items.symlink_to(physical_items, target_is_directory=True)
    logical_paper = logical_items / 'paper-one'
    logical_paper.mkdir()

    root = paper_store._paper_root({'_store_root': str(logical_paper)})

    assert root == (physical_items / 'paper-one').resolve()
    assert (root / 'figures/extracted/fig-001.png').relative_to(root) == Path(
        'figures/extracted/fig-001.png'
    )


@pytest.fixture
def extraction(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_figure_extraction, '_poppler_command', lambda name: name)
    paper = {'id': 'paper_0123456789ab', '_store_root': str(tmp_path)}
    directory = tmp_path / 'figures'
    extracted = directory / 'extracted'
    extracted.mkdir(parents=True)
    source = tmp_path / 'paper.pdf'
    source.write_bytes(b'%PDF-fixture')
    image = extracted / 'fig-001.png'
    with noisy_image() as content:
        content.save(image)
    path = directory / 'figures.json'
    prior = {
        'figure_id': 'fig-001', 'paper_id': paper['id'], 'index': 1,
        'label': 'Figure 2', 'caption': 'Figure 2: A measured signal.', 'page': 1,
        'image_rel_path': str(image.relative_to(tmp_path)),
        'extraction_mode': 'caption_region_crop',
        'width': 240, 'height': 180, 'size_bytes': image.stat().st_size,
        'explanation': 'Explanation for Figure 2 only.', 'explanation_status': 'done',
        'explanation_invocation': {'schema': 'receipt-fixture', 'input_message_id': 'original-input'},
        'explanation_usage': {'total_tokens': 17},
        'generated': [{'generated_id': 'generated-existing', 'request': 'redraw Figure 2'}],
        'created_by': 'original-owner', 'created_at': 1,
    }
    path.write_text(json.dumps([prior]))
    monkeypatch.setattr(paper_figures, '_paper_by_id', lambda identifier: paper)
    monkeypatch.setattr(paper_figure_jobs, '_paper_by_id', paper_figures._paper_by_id)
    monkeypatch.setattr(paper_figures, '_local_pdf_for_paper', lambda value: source)
    monkeypatch.setattr(paper_figures, '_figures_dir', lambda value: directory)
    monkeypatch.setattr(paper_figures, '_figures_json_path', lambda value: path)
    monkeypatch.setattr(paper_figures, '_notify_paper_projection', lambda *args, **kwargs: None)
    captions = [{'number': '1', 'page': 1, 'caption': 'Figure 1: A different signal.'},
                {'number': '2', 'page': 1, 'caption': prior['caption']}]
    monkeypatch.setattr(paper_figures, '_extract_figure_caption_blocks', lambda pdf: captions)
    monkeypatch.setattr(paper_figures, '_prepare_figure_caption_blocks', lambda values: values)

    def render(pdf, page, prefix):
        page_path = prefix.with_suffix('.png')
        with noisy_image(12) as content:
            content.save(page_path)
        return page_path

    monkeypatch.setattr(paper_figures, '_render_pdf_page_png', render)
    monkeypatch.setattr(paper_figures, '_crop_figure_above_caption', lambda page, caption: page.copy())
    return SimpleNamespace(paper=paper, path=path, image=image, prior=prior, captions=captions,
                           directory=directory, root=tmp_path)


@pytest.mark.parametrize('stage', ['caption', 'second_crop', 'commit'])
def test_failed_extraction_keeps_previous_manifest_and_image(extraction, monkeypatch, stage):
    """插图提取任一阶段失败保留原清单与图片"""
    x = extraction
    old_json, old_image = x.path.read_bytes(), x.image.read_bytes()

    def fail(*args, **kwargs):
        raise RuntimeError('injected extraction failure')

    if stage == 'caption':
        monkeypatch.setattr(paper_figures, '_extract_figure_caption_blocks', fail)
    elif stage == 'second_crop':
        def crop(page, caption):
            if caption['number'] == '2':
                fail()
            return page.copy()
        monkeypatch.setattr(paper_figures, '_crop_figure_above_caption', crop)
    else:
        monkeypatch.setattr(paper_figures, '_write_json', fail)
    with pytest.raises(RuntimeError, match='injected extraction failure'):
        paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    assert x.path.read_bytes() == old_json
    assert x.image.is_file() and x.image.read_bytes() == old_image


def test_new_preceding_figure_does_not_steal_identity_or_attached_artifacts(extraction):
    """新增前置插图不占用旧图身份与附属产物"""
    x = extraction
    result = paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    by_label = {f['label']: f for f in result['figures']}
    assert by_label['Figure 1']['figure_id'] != x.prior['figure_id']
    assert not by_label['Figure 1']['explanation']
    assert not by_label['Figure 1']['generated']
    same = by_label['Figure 2']
    assert same['figure_id'] == x.prior['figure_id'] and same['index'] == 2
    for field in ('explanation', 'explanation_status', 'explanation_invocation',
                  'explanation_usage', 'created_by', 'created_at'):
        assert same[field] == x.prior[field]
    assert same['generated'][0]['generated_id'] == 'generated-existing'
    for item in json.loads(x.path.read_text()):
        assert (x.root / item['image_rel_path']).is_file()


def test_reused_label_with_different_caption_does_not_inherit_explanation(extraction):
    """相同图编号配不同图注时不继承旧解释"""
    x = extraction
    x.captions[:] = [{'number': '2', 'page': 1, 'caption': 'Figure 2: An unrelated experiment.'}]
    item = paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')['figures'][0]
    assert item['figure_id'] != x.prior['figure_id']
    assert not item['explanation'] and not item['generated']


def test_concurrent_explanation_completion_is_preserved_at_commit(extraction, monkeypatch):
    """插图提取提交保留并发完成的解释"""
    x = extraction
    def crop(page, caption):
        current = json.loads(x.path.read_text())
        current[0]['explanation'] = 'Completed while extracting.'
        current[0]['explanation_status'] = 'done'
        x.path.write_text(json.dumps(current))
        return page.copy()
    monkeypatch.setattr(paper_figures, '_crop_figure_above_caption', crop)
    paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    stored = {f['label']: f for f in json.loads(x.path.read_text())}
    assert stored['Figure 2']['explanation'] == 'Completed while extracting.'


def test_embedded_images_of_equal_dimensions_and_size_are_distinct(extraction, monkeypatch):
    """尺寸与大小相同的内嵌图按像素区分并去重"""
    x = extraction
    x.captions[:] = []
    monkeypatch.setattr(paper_figure_extraction, '_pdfimages_pages', lambda path: [1, 1, 1])
    def images(command, **kwargs):
        prefix = Path(command[-1])
        for index, seed in enumerate((3, 7, 3)):
            with noisy_image(seed) as image:
                image.save(prefix.parent / f'{prefix.name}-{index:03}.png')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(paper_figure_extraction.process_runtime, 'run_process', images)
    result = paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    assert result['count'] == 2  # two different pixels, one exact duplicate
    assert all(not f['explanation'] and not f['generated'] for f in result['figures'])


def test_pdfimages_failure_is_error_and_preserves_previous_assets(extraction, monkeypatch):
    """pdfimages 失败时报告错误并保留原图片"""
    x = extraction
    x.captions[:] = []
    monkeypatch.setattr(paper_figure_extraction, '_pdfimages_pages', lambda path: [])
    monkeypatch.setattr(paper_figure_extraction.process_runtime, 'run_process', lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout='', stderr='PDF image extraction failed'))
    before = x.path.read_bytes(), x.image.read_bytes()
    with pytest.raises(RuntimeError, match='PDF image extraction failed'):
        paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    assert (x.path.read_bytes(), x.image.read_bytes()) == before


def test_post_commit_notification_error_keeps_published_images(extraction, monkeypatch):
    """提交后通知失败仍保留已发布图片"""
    x = extraction
    def fail(*args, **kwargs):
        raise RuntimeError('notification unavailable')
    monkeypatch.setattr(paper_figures, '_notify_paper_projection', fail)
    result = paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    assert result['count'] == 2
    for item in json.loads(x.path.read_text()):
        assert (x.root / item['image_rel_path']).is_file()


def test_repeated_extract_preserves_ids_and_reclaims_only_obsolete_files(extraction):
    """重复提取保留图标识且仅回收未被引用的旧文件"""
    x = extraction
    paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    first = json.loads(x.path.read_text())
    attached = x.root / first[0]['image_rel_path']
    first[1]['generated'][0]['image_rel_path'] = str(attached.relative_to(x.root))
    x.path.write_text(json.dumps(first))
    paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    second = json.loads(x.path.read_text())
    assert [f['figure_id'] for f in first] == [f['figure_id'] for f in second]
    assert attached.is_file()  # now referenced by a generated image
    assert not (x.root / first[1]['image_rel_path']).exists()
    assert all((x.root / f['image_rel_path']).is_file() for f in second)


def test_changed_pdf_cannot_publish_stale_extraction(extraction, monkeypatch):
    """提取过程中 PDF 内容变化会阻止旧结果发布"""
    x = extraction
    before = x.path.read_bytes(), x.image.read_bytes()
    def crop(page, caption):
        (x.root / 'paper.pdf').write_bytes(b'%PDF-new-source-different-size')
        return page.copy()
    monkeypatch.setattr(paper_figures, '_crop_figure_above_caption', crop)
    with pytest.raises(RuntimeError, match='PDF changed'):
        paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    assert (x.path.read_bytes(), x.image.read_bytes()) == before


def test_extractions_are_serialized_without_blocking_annotation_commit(extraction, monkeypatch):
    """插图提取串行执行且不阻塞并发标注提交"""
    x = extraction
    started, release = threading.Event(), threading.Event()
    entered = []
    def crop(page, caption):
        entered.append(threading.get_ident())
        started.set()
        assert release.wait(5)
        return page.copy()
    monkeypatch.setattr(paper_figures, '_crop_figure_above_caption', crop)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(paper_figures.extract_figures, {'paper_id': x.paper['id']}, 'alice')
        assert started.wait(5)
        second = pool.submit(paper_figures.extract_figures, {'paper_id': x.paper['id']}, 'alice')
        annotation = pool.submit(paper_figure_jobs._set_figure_fields, x.paper['id'], 'fig-001', {'explanation': 'parallel annotation'})
        try:
            assert annotation.result(timeout=5)['figure']['explanation'] == 'parallel annotation'
            assert len(set(entered)) == 1
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    stored = {f['label']: f for f in json.loads(x.path.read_text())}
    assert stored['Figure 2']['explanation'] == 'parallel annotation'
    assert len({f['figure_id'] for f in stored.values()}) == 2


def test_ambiguous_old_identity_is_not_guessed(extraction):
    """旧图身份存在歧义时分配新标识且不继承解释"""
    x = extraction
    duplicate = {**x.prior, 'figure_id': 'fig-002', 'explanation': 'different annotation'}
    x.path.write_text(json.dumps([x.prior, duplicate]))
    item = next(f for f in paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')['figures'] if f['label'] == 'Figure 2')
    assert item['figure_id'] not in {'fig-001', 'fig-002'}
    assert not item['explanation']


def test_embedded_identity_uses_pixels_when_source_order_changes(extraction, monkeypatch):
    """内嵌图顺序改变后按像素保留身份与标注"""
    x = extraction
    x.captions[:] = []
    seeds = [3, 7]
    monkeypatch.setattr(paper_figure_extraction, '_pdfimages_pages', lambda path: [1, 2])
    def images(command, **kwargs):
        prefix = Path(command[-1])
        for index, seed in enumerate(seeds):
            with noisy_image(seed) as image:
                image.save(prefix.parent / f'{prefix.name}-{index:03}.png')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(paper_figure_extraction.process_runtime, 'run_process', images)
    paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    stored = json.loads(x.path.read_text())
    stored[0]['explanation'] = 'First pixels'; stable_id = stored[0]['figure_id']
    stored[0]['caption'] = 'Manually annotated image'
    x.path.write_text(json.dumps(stored))
    seeds.reverse()
    result = paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')['figures']
    assert result[1]['figure_id'] == stable_id and result[1]['explanation'] == 'First pixels'
    assert result[1]['caption'] == 'Manually annotated image'
    assert not result[0]['explanation']


def test_pdfimages_listing_failure_is_not_treated_as_no_images(extraction, monkeypatch):
    """pdfimages 列表失败时报告错误并停止提取"""
    x = extraction
    x.captions[:] = []
    calls = []
    def fail(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout='', stderr='xref missing')
    monkeypatch.setattr(paper_figure_extraction.process_runtime, 'run_process', fail)
    with pytest.raises(RuntimeError, match='xref missing'):
        paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    assert len(calls) == 1 and '-list' in calls[0]
    assert x.image.is_file()


def test_poppler_embedded_fallback_end_to_end(tmp_path, monkeypatch):
    """无图注 PDF 通过 Poppler 提取分页内嵌图为 PNG"""
    paper = {'id': 'paper_0123456789ab', '_store_root': str(tmp_path)}
    pdf = tmp_path / 'paper.pdf'
    with noisy_image(3) as first, noisy_image(7) as second:
        first.save(pdf, format='PDF', save_all=True, append_images=[second])
    monkeypatch.setattr(paper_figures, '_paper_by_id', lambda identifier: paper)
    monkeypatch.setattr(paper_figures, '_local_pdf_for_paper', lambda value: pdf)
    monkeypatch.setattr(paper_figures, '_notify_paper_projection', lambda *args, **kwargs: None)
    result = paper_figures.extract_figures({'paper_id': paper['id']}, 'alice')
    assert result['count'] == 2
    assert [f['page'] for f in result['figures']] == [1, 2]
    for f in result['figures']:
        assert f['extraction_mode'] == 'embedded_pdf_image' and not f['caption']
        with Image.open(tmp_path / f['image_rel_path']) as image:
            assert image.format == 'PNG' and image.size == (240, 180)


def test_empty_extraction_never_recycles_removed_figure_identity(extraction, monkeypatch):
    """空提取后新增插图不复用已删除身份"""
    x = extraction
    x.captions[:] = []
    monkeypatch.setattr(paper_figures, '_extract_embedded_pdf_images', lambda *args: [])
    assert paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')['count'] == 0
    x.captions[:] = [{'number': '1', 'page': 1, 'caption': 'Figure 1: New content.'}]
    new = paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')['figures'][0]
    assert new['figure_id'] != x.prior['figure_id'] and new['index'] == 1
    with pytest.raises(KeyError, match='figure not found'):
        paper_figure_jobs._set_figure_fields(x.paper['id'], x.prior['figure_id'], {'explanation': 'late old completion'})
    assert not json.loads(x.path.read_text())[0]['explanation']


def test_uncertain_confirmation_keeps_images_after_metadata_commit(extraction, monkeypatch):
    """元数据提交后无法确认结果时保留新旧图片"""
    x = extraction
    read_text = Path.read_text
    notification_failed = False
    def notify(*args, **kwargs):
        nonlocal notification_failed
        notification_failed = True
        raise RuntimeError('notify after commit failed')
    def read(path, *args, **kwargs):
        if path == x.path and notification_failed:
            raise OSError('commit confirmation unavailable')
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(paper_figures, '_notify_paper_projection', notify)
    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(OSError, match='commit confirmation unavailable'):
        paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    persisted = json.loads(read_text(x.path))
    assert len(persisted) == 2
    assert all((x.root / item['image_rel_path']).is_file() for item in persisted)
    assert x.image.is_file()  # uncertainty also prevents old-generation cleanup


@pytest.mark.parametrize('failure', ['io', 'json', 'shape'])
def test_unreadable_previous_metadata_is_not_overwritten(extraction, monkeypatch, failure):
    """旧插图元数据不可读时停止提取且不覆盖"""
    x = extraction
    if failure != 'io':
        x.path.write_text('broken json' if failure == 'json' else '{}')
    original = x.path.read_bytes(), x.image.read_bytes()
    if failure == 'io':
        read_text = Path.read_text
        def read(path, *args, **kwargs):
            if path == x.path:
                raise OSError('old metadata unavailable')
            return read_text(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises((OSError, ValueError)):
        paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    assert (x.path.read_bytes(), x.image.read_bytes()) == original


def test_unreadable_pdfimages_output_does_not_replace_previous_assets(extraction, monkeypatch):
    """pdfimages 输出不可读时保留原清单与图片"""
    x = extraction
    x.captions[:] = []
    before = x.path.read_bytes(), x.image.read_bytes()
    monkeypatch.setattr(paper_figure_extraction, '_pdfimages_pages', lambda path: [1])
    def malformed(command, **kwargs):
        prefix = Path(command[-1])
        (prefix.parent / f'{prefix.name}-000.png').write_bytes(b'truncated output')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(paper_figure_extraction.process_runtime, 'run_process', malformed)
    with pytest.raises(RuntimeError, match='unreadable image'):
        paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    assert (x.path.read_bytes(), x.image.read_bytes()) == before


def test_pdf_path_replacement_cannot_publish_old_file_extraction(extraction, monkeypatch):
    """论文 PDF 路径更换会阻止原文件提取结果发布"""
    x = extraction
    source = x.root / 'paper.pdf'
    original_pdf = source.stat(), source.read_bytes()
    x.paper['pdf_path'] = str(source)
    replacement = x.root / 'replacement.pdf'
    replacement.write_bytes(b'%PDF-a-different-document')
    before = x.path.read_bytes(), x.image.read_bytes()
    def crop(page, caption):
        x.paper['pdf_path'] = str(replacement)
        return page.copy()
    monkeypatch.setattr(paper_figures, '_crop_figure_above_caption', crop)
    with pytest.raises(RuntimeError, match='PDF changed'):
        paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    assert (source.stat(), source.read_bytes()) == original_pdf
    assert (x.path.read_bytes(), x.image.read_bytes()) == before
