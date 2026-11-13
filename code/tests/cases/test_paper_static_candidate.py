"""Current Reader and personal-tree composition, using the same source tree."""
import pytest
from tests.cases.test_paper_reader_fullscreen import run_host


@pytest.mark.parametrize('browser', ['chromium', 'firefox'])
def test_current_reader_personal_composition(tmp_path, monkeypatch, browser):
    run_host(tmp_path, monkeypatch, browser, 'paper_static_candidate.cjs')
