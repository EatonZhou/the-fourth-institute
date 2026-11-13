"""Reader borrows the ordinary Chat workspace; no Resident/model submission."""
import pytest
from tests.cases.test_paper_reader_fullscreen import run_host


@pytest.mark.parametrize('browser', ['chromium', 'firefox'])
def test_reader_chat_style(tmp_path, monkeypatch, browser):
    run_host(tmp_path, monkeypatch, browser, 'reader_chat_style.cjs')


@pytest.mark.parametrize('browser', ['chromium', 'firefox'])
def test_reader_chat_toggle(tmp_path, monkeypatch, browser):
    monkeypatch.setenv('READER_TOGGLE_ONLY', '1')
    run_host(tmp_path, monkeypatch, browser, 'reader_chat_style.cjs')


@pytest.mark.parametrize('browser', ['chromium', 'firefox'])
def test_reader_chat_timeline_style(tmp_path, monkeypatch, browser):
    monkeypatch.setenv('READER_TIMELINE_ONLY', '1')
    run_host(tmp_path, monkeypatch, browser, 'reader_chat_style.cjs')


@pytest.mark.parametrize('browser', ['chromium', 'firefox'])
def test_reader_chat_splitter(tmp_path, monkeypatch, browser):
    monkeypatch.setenv('READER_SPLIT_ONLY', '1')
    run_host(tmp_path, monkeypatch, browser, 'reader_chat_style.cjs')
