"""Every LaTeX diagnostic form obeys the same result budget.

:module: workflow.article
:design: decision_956154cd02f6
:importance: P1
"""
import pytest
import tracemalloc

from management_portal.article_build_results import parse_latex_diagnostics


@pytest.mark.parametrize('kind', ['location', 'warning', 'mixed'])
def test_diagnostic_budget_is_independent_of_latex_message_form(kind):
    locations = [f'main.tex:{i + 1}: Undefined control sequence {i}' for i in range(300)]
    warnings = [f'LaTeX Warning: Citation {i} undefined' for i in range(300)]
    rows = locations if kind == 'location' else warnings
    if kind == 'mixed':
        rows = [locations[0], warnings[0], *locations[1:]]
    # Duplicate messages must not consume additional slots.
    log = '\n'.join(item for row in rows for item in (row, row))
    diagnostics = parse_latex_diagnostics(log, limit=3)
    assert len(diagnostics) == 3
    assert len({(d['message'], d['file'], d['line']) for d in diagnostics}) == 3
    assert diagnostics[0]['message'] == rows[0].split(': ', 1)[1]
    assert len(parse_latex_diagnostics(log)) == 240


def test_error_lookahead_and_empty_budget():
    log = '! Undefined control sequence\ncontext\nl.42 bad macro\nmain.tex:42: Undefined control sequence\n'
    diagnostics = parse_latex_diagnostics(log, limit=1)
    assert len(diagnostics) == 1
    assert diagnostics[0]['file'] == 'main.tex'
    assert diagnostics[0]['line'] == 42
    assert parse_latex_diagnostics(log, limit=0) == []


def test_large_log_does_not_materialize_all_lines():
    log = '\n'.join(f'main.tex:{i}: Undefined control sequence {i}' for i in range(100000))
    tracemalloc.start()
    try:
        diagnostics = parse_latex_diagnostics(log)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(diagnostics) == 240
    assert peak < 2 * 1024 * 1024, f'parser allocated {peak} bytes after input existed'


def test_blank_lines_and_unicode_separators_keep_six_line_lookahead():
    for separator in ('\n', '\r\n', '\r', '\v', '\f', '\x1c', '\x1d', '\x1e', '\x85', '\u2028', '\u2029'):
        in_range = separator.join(['! Error', '', '', '', '', '', 'l.42 macro', ''])
        out_of_range = separator.join(['! Error', '', '', '', '', '', '', 'l.42 macro'])
        assert parse_latex_diagnostics(in_range)[0]['line'] == 42
        assert parse_latex_diagnostics(out_of_range)[0]['line'] == 0
