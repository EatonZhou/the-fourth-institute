"""Only the display projection is shortened; retrieval uses the original text."""
import pytest
from management_portal import paper_library as library


@pytest.mark.parametrize('text', ['Short abstract.', 'x' * 300, '  Preserve short whitespace.  '])
def test_short_abstract_is_unchanged_after_original_whitespace_cleanup(text):
    assert library._paper_library_list_item({'abstract': text})['abstract'] == text.strip()


@pytest.mark.parametrize(('text', 'expected'), [
    ('word ' * 70, ('word ' * 59 + 'word') + '…'),
    ('a' * 185 + ' ' + 'tail' * 40, ('a' * 185 + ' ' + 'tail' * 40)[:299] + '…'),
    ('word ' * 55 + 'done,; ' + 'x' * 80, 'word ' * 55 + 'done…'),
    ('中文' * 200, ('中文' * 200)[:299] + '…'),
    ('tiny ' + '中' * 400, ('tiny ' + '中' * 400)[:299] + '…'),
    ('中' * 297 + '，。' + '余' * 20, '中' * 297 + '…'),
    ('word\t' * 70, ('word\t' * 70)[:299] + '…'),
])
def test_long_abstract_budget_word_boundary_and_punctuation(text, expected):
    source = {'abstract': text}
    result = library._paper_library_list_item(source)['abstract']
    assert result == expected and len(result) <= 300
    assert source['abstract'] == text


def test_empty_abstract_remains_absent():
    assert 'abstract' not in library._paper_library_list_item({'abstract': '  '})


def test_search_still_finds_terms_beyond_preview(isolated_portal_case, monkeypatch):
    text = 'word ' * 100 + 'unique_tail_term'
    paper = {'id': 'paper_000000000001', 'title': 'Fixture', 'abstract': text}
    with isolated_portal_case():
        monkeypatch.setattr(library, 'resource_index_papers', lambda **kw: [paper])
        result = library.list_library({'q': 'unique_tail_term', 'compact': '1'}, actor='sample_admin')
    assert [row['id'] for row in result['papers']] == [paper['id']]
    assert 'unique_tail_term' not in result['papers'][0]['abstract']
    assert result['papers'][0]['abstract'].endswith('…')


def test_trailing_whitespace_does_not_make_a_short_abstract_look_truncated():
    text = '\n  ' + 'x' * 280 + (' \n' * 50)
    assert len(text) > 300
    assert library._paper_library_list_item({'abstract': text})['abstract'] == 'x' * 280


def test_chinese_with_an_earlier_english_word_keeps_character_budget():
    text = '中' * 190 + ' term ' + '文' * 200
    preview = library._paper_library_list_item({'abstract': text})['abstract']
    assert preview == text[:299] + '…'
    assert len(preview) == 300


@pytest.mark.parametrize(('boundary', 'use_word_boundary'), [(259, True), (258, False)])
def test_only_the_last_forty_characters_can_supply_a_word_boundary(boundary, use_word_boundary):
    text = '中' * boundary + ' ' + 'x' * 150
    expected = ('中' * boundary if use_word_boundary else text[:299]) + '…'
    assert library._paper_library_list_item({'abstract': text})['abstract'] == expected
