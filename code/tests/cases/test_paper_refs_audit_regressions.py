"""Reference extraction regressions from the September 2026 library audit."""
import pytest
from management_portal import paper_references, paper_refs


@pytest.mark.parametrize('heading', ['References:', 'References', '7 References:', 'Bibliography:', 'REFERENCES:'])
@pytest.mark.parametrize('count', [1, 3, 6])
def test_explicit_bibliography_keeps_short_lists_and_colon_headings(heading, count):
    """显式参考文献标题支持冒号与短列表"""
    entries = [f'[{i}] B. Munger et al., “Processor architecture number {i},” ISSCC, pp. 38-39, 2023.' for i in range(1, count + 1)]
    # References can start before the final 55% of a short conference PDF.
    text = 'Introduction\n' + heading + '\n' + '\n'.join(entries) + '\nAppendix\n' + 'Supplementary material.\n' * 100
    assert paper_references._parse_references_from_text(text) == entries


def test_short_numbered_body_list_is_not_a_bibliography():
    """正文中的短编号步骤列表不作为参考文献"""
    text = 'Introduction\n1. Our evaluation method uses these steps.\n2. We examine task completion.\n3. The robot stops.\n'
    assert paper_references._parse_references_from_text(text) == []


@pytest.mark.parametrize('entry', [
    '1. Richard S. Sutton and Andrew G. Barto. Reinforcement Learning: An Introduction. MIT Press, 2018.',
    '2. Alex Smith and Bob Jones. Evaluation of robot learning. Journal of Robotics, 2024.',
])
def test_bibliography_entries_are_not_fragments_due_to_topic_or_missing_venue_keyword(entry):
    """参考文献不因主题或缺少出版场所关键词被视为残片"""
    assert not paper_references._looks_like_reference_fragment(entry)


@pytest.mark.parametrize('raw,title', [
    ('[10] Tom B. Brown, Benjamin Mann, Nick Ryder, and Dario Amodei. Language models are few-shot learners. In Advances in Neural Information Processing Systems, 2020.', 'Language models are few-shot learners'),
    ('[17] Zipeng Fu, Tony Z. Zhao, and Chelsea Finn. Mobile aloha: Learning bimanual mobile manipulation with low-cost whole-body teleoperation. In Conference on Robot Learning (CoRL), 2024.', 'Mobile aloha: Learning bimanual mobile manipulation with low-cost whole-body teleoperation'),
    ('[32] S. Lloyd, “Universal quantum simulators”, Science, vol.273, pp.1073-1078, 1996, DOI: 10.1126/science.273.5278.1073.', 'Universal quantum simulators'),
    ('[20] J. Lagos et al., “A fast pipelined-SAR ADC with background calibration,” 2021 doi: 10.23919/VLSICircuits52068.2021.9492354.', 'A fast pipelined-SAR ADC with background calibration'),
    ('[1] John O\'Brien and Anne O\'Neil. “Learning from observations.” Journal of Robotics, 2024.', 'Learning from observations'),
    ('[1] Smith, J., and Jones, B. 2024. Learning from observations. In Conference on Robotics.', 'Learning from observations'),
])
def test_title_boundaries_keep_initials_authors_and_doi_out_of_title(raw, title):
    """参考标题边界排除作者缩写与 DOI"""
    assert paper_references._guess_ref_title(raw) == title


def test_structured_rules_continue_after_invalid_candidate():
    """参考标题候选无效时继续尝试后续结构规则"""
    # The generic author/title rule sees the year first; the next precise rule owns it.
    raw = 'John Smith and Bob Jones. 2024. Learning from observations. In Conference on Robotics.'
    assert paper_references._guess_ref_title(raw) == 'Learning from observations'


@pytest.mark.parametrize('line', [
    '[1] Jürgen Schmidhuber. Reinforcement learning upside down.',
    '[2] Michał Zawalski, William Chen, Karl Pertsch, Oier Mees.',
    '[3] Timothée Darcet, Maxime Oquab, Julien Mairal, and Piotr Bojanowski.',
    '[4] F.R. Willett, et al., “High-Performance Brain-to-Text Communication via Handwriting,” Nature, 2021.',
])
def test_numbered_reference_authors_support_unicode_and_compact_initials(line):
    """编号参考文献支持 Unicode 作者与紧凑缩写"""
    assert paper_references._looks_like_numbered_reference_line(line)


def test_nested_typographic_quotes_keep_full_title():
    """参考标题包含嵌套弯引号时保留完整标题"""
    raw = '[3] B. Cohen, M. Subramony, M. Clark, “Next Generation “Zen 5” Core,” Hot Chips, 2024.'
    assert paper_references._guess_ref_title(raw) == 'Next Generation “Zen 5” Core'


def test_extract_refs_persists_correct_short_bibliography(tmp_path, monkeypatch):
    """提取参考文献持久化短列表及正确标题"""
    import json

    paper = {'id': 'paper_0123456789ab', 'title': 'Source', '_store_root': str(tmp_path), '_files': {'refs': 'refs/references.json'}}
    text = 'References:\n[1] S. Lloyd, “Universal quantum simulators”, Science, vol.273, pp.1073-1078, 1996, DOI: 10.1126/science.273.5278.1073.\n'
    monkeypatch.setattr(paper_refs, '_all_papers', lambda: [paper])
    monkeypatch.setattr(paper_refs, '_paper_by_id', lambda _: paper)
    monkeypatch.setattr(paper_refs, '_paper_public', lambda p: p)
    monkeypatch.setattr(paper_refs, '_local_pdf_for_paper', lambda _: tmp_path / 'paper.pdf')
    monkeypatch.setattr(paper_refs, '_extract_pdf_text_raw', lambda *args, **kwargs: text)
    result = paper_refs.extract_refs({'paper_id': paper['id']}, 'test-actor')
    assert result['count'] == 1
    persisted = json.loads((tmp_path / 'refs/references.json').read_text())
    assert persisted == result['refs']
    assert persisted[0]['title'] == 'Universal quantum simulators'
    assert persisted[0]['paper_id'] == paper['id']


@pytest.mark.parametrize('author', ['Michał Zawalski', 'Jürgen Schmidhuber'])
def test_unicode_single_author_is_not_returned_as_title(author):
    """Unicode 单作者姓名不会误识别为参考标题"""
    raw = f'[1] {author}. Learning from demonstrations. In Conference on Robot Learning, 2024.'
    assert paper_references._guess_ref_title(raw) == 'Learning from demonstrations'


def test_unnumbered_bibliography_is_not_reclassified_by_wrapped_page_numbers():
    """无编号参考文献不因换行页码被重新分类"""
    text = ('References\nBellemare, Marc G., and Bowling, Michael. Learning from demonstrations. '
            'Journal of Robotics, pp. 60–\n74. Springer-Verlag, 2008.\n'
            'Sutton, Richard S., and Barto, Andrew G. Learning value functions. Journal of Robotics, 2018.\n')
    refs = paper_references._parse_references_from_text(text)
    assert len(refs) == 2
    assert refs[0].startswith('Bellemare,')
    assert refs[1].startswith('Sutton,')


def test_author_year_precedes_generic_author_title_rule():
    """参考标题识别优先处理作者年份格式"""
    raw = 'John Smith and Jane Doe. 2024. A Task Learning Method. In Proceedings of Robotics, pp. 1–10.'
    assert paper_references._guess_ref_title(raw) == 'A Task Learning Method'


def test_spaced_hyphenated_initials_are_numbered_reference_start():
    """带空格与连字符的作者缩写可识别为文献起始"""
    assert paper_references._looks_like_numbered_reference_line('[7] M. -D. Tsai et al., “A 12nm CMOS RF Transceiver,” ISSCC, 2024.')


@pytest.mark.parametrize('raw,title', [
    ('[78] Zhong, Y., Orlovich, M., Shen, X., and Ding, C. Array Regrouping and Structure Splitting Using Whole-Program Reference Affinity. In International Conference on Programming Language Design and Implementation (2004).', 'Array Regrouping and Structure Splitting Using Whole-Program Reference Affinity'),
    ('[31] GARNETT, P., AND ROBERTS, S. Overview of Internet service provider technology considerations for rural broadband deployments. Tech. rep., Microsoft Airband Initiative Team, Redmond, WA, USA, 2018.', 'Overview of Internet service provider technology considerations for rural broadband deployments'),
])
def test_terminal_author_initial_is_also_a_title_boundary(raw, title):
    """作者末尾缩写句点可分隔参考标题"""
    assert paper_references._guess_ref_title(raw) == title


def test_comma_separated_initial_authors_are_not_the_title():
    """逗号分隔的缩写作者不会混入参考标题"""
    raw = '[195] A. Borah, R. Mihalcea, Towards implicit bias detection and mitigation in multi-agent llm interactions, 2024, arXiv preprint arXiv:2410.02584.'
    assert paper_references._guess_ref_title(raw) == 'Towards implicit bias detection and mitigation in multi-agent llm interactions'


def test_short_list_support_preserves_existing_detached_label_fallback():
    """短列表解析保留编号与正文分离时的回退提取"""
    numbered = '[1] A. Smith and B. Jones. First learning method. Journal of Robotics, 2024.\n'
    detached = '\n'.join(f'[{i}]' for i in range(2, 8))
    entries = '\n'.join(f'{name}, A., and Jones, B. Learning method {i}. Journal of Robotics, 2024.' for i, name in enumerate(['Brown', 'Davis', 'Evans', 'Foster', 'Grant', 'Hill']))
    result = paper_references._parse_references_from_text('References\n' + numbered + detached + '\n' + entries)
    assert len(result) >= 6
    assert any('Hill,' in entry for entry in result)
