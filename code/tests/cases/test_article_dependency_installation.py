"""Explicit TeX installation uses detected files and the configured package repository.

:module: article
:design: decision_956154cd02f6
:profiles: local.fixture
:risk: no-external-side-effects
"""
from unittest.mock import Mock
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from management_portal import article_dependencies as deps
from management_portal import article_build_profile


def profile(*files):
    return {'status': 'blocked' if files else 'ready', 'dependencies': [
        {'file': f, 'resolved': ''} for f in files], 'issues': []}


def test_install_detected_files_deduplicates_and_ignores_doc_examples(monkeypatch):
    preflight = Mock(side_effect=[profile('IEEEtran.cls', 'IEEEtran.bst'), profile()])
    monkeypatch.setattr(deps.article_build_profile, 'article_build_preflight', preflight)
    monkeypatch.setattr(deps.article_build_results, 'load_article_build_report', lambda _: {})
    monkeypatch.setattr(deps.article_toolchain, '_tex_which', lambda _: '/tools/tlmgr')
    run = Mock(side_effect=['ieeetran:\n texmf-dist/tex/latex/ieeetran/IEEEtran.cls\n texmf-dist/bibtex/bst/ieeetran/IEEEtran.bst\nother:\n texmf-dist/doc/example/IEEEtran.cls\n', 'installed'])
    monkeypatch.setattr(deps, '_run_tlmgr', run)
    result = deps.install_article_dependencies('article_0123456789ab')
    assert result['installed_packages'] == ['ieeetran']
    assert result['preflight']['status'] == 'ready'
    assert run.call_args_list[1].args[0] == ['/tools/tlmgr', 'install', 'ieeetran']


def test_missing_transitive_package_is_detected_but_paths_are_not_commands(monkeypatch):
    monkeypatch.setattr(deps.article_build_profile, 'article_build_preflight', lambda _: profile('bad/../evil.sty'))
    monkeypatch.setattr(deps.article_build_results, 'load_article_build_report', lambda _: {
        'status': 'error', 'log': "! LaTeX Error: File `totpages.sty' not found.\nFile `../../evil.sty' not found."})
    monkeypatch.setattr(deps.article_toolchain, '_tex_which', lambda _: '/tools/tlmgr')
    monkeypatch.setattr(deps.article_toolchain, '_kpsewhich_file', lambda _: '')
    assert deps.dependency_preflight('a')['installation']['missing_files'] == ['totpages.sty']


def test_unknown_or_ambiguous_package_is_not_installed():
    with pytest.raises(RuntimeError, match='无法确定'):
        deps._packages_for_files('fake:\n texmf-dist/doc/unknown.sty', ['unknown.sty'])
    with pytest.raises(RuntimeError, match='无法确定'):
        deps._packages_for_files('a:\n texmf-dist/tex/a/x.sty\nb:\n texmf-dist/tex/b/x.sty', ['x.sty'])


def test_install_failure_releases_lock_and_retry_rechecks(monkeypatch):
    monkeypatch.setattr(deps, 'dependency_preflight', lambda _: {'installation': {'missing_files': ['x.sty']}})
    monkeypatch.setattr(deps.article_toolchain, '_tex_which', lambda _: '/tools/retry-tlmgr')
    run = Mock(side_effect=['x:\n texmf-dist/tex/x.sty', RuntimeError('network failed'), 'x:\n texmf-dist/tex/x.sty', 'ok'])
    monkeypatch.setattr(deps, '_run_tlmgr', run)
    with pytest.raises(RuntimeError, match='network failed'):
        deps.install_article_dependencies('a')
    assert deps.install_article_dependencies('a')['installed_packages'] == ['x']


def test_install_contention_does_not_start_process(monkeypatch):
    monkeypatch.setattr(deps.article_toolchain, '_tex_which', lambda _: '/tools/locked-tlmgr')
    monkeypatch.setattr(deps, 'try_lock_descriptor', lambda _: False)
    run = Mock()
    monkeypatch.setattr(deps, '_run_tlmgr', run)
    with pytest.raises(RuntimeError, match='其他文稿'):
        deps.install_article_dependencies('a')
    run.assert_not_called()


def test_bibliography_style_is_checked_in_preflight(tmp_path, monkeypatch):
    (tmp_path / 'main.tex').write_text('\\documentclass{article}\n\\bibliographystyle{IEEEtran}\n\\begin{document}x\\end{document}')
    monkeypatch.setattr(deps.article_toolchain, '_tex_which', lambda _: '/tool')
    monkeypatch.setattr(deps.article_toolchain, 'latex_environment', lambda: {})
    monkeypatch.setattr(deps.article_toolchain, '_kpsewhich_file', lambda name: '' if name.endswith('.bst') else '/exists')
    monkeypatch.setattr(article_build_profile.repo_backend, 'project_ids_for_path', lambda _: [])
    result = article_build_profile.inspect_article_build_profile(tmp_path, primary_file='main.tex')
    assert any(i['dependency'] == 'IEEEtran' for i in result['issues'])


@pytest.mark.parametrize('denied,payload,status', [(False, {}, 200), (True, {}, 403), (False, {'packages': ['bad']}, 400)])
def test_http_install_keeps_actor_and_rejects_client_package_input(monkeypatch, denied, payload, status):
    from management_portal.http_articles import ArticlesHttp
    require = Mock(side_effect=PermissionError('denied') if denied else None)
    install = Mock(return_value={'installed_packages': []})
    monkeypatch.setattr(deps, 'install_article_dependencies', install)
    http = ArticlesHttp(respond=lambda req, code, value: (code, value), exception_response=Mock(),
        read_json=lambda _: payload, read_multipart=None, multipart_limit=None, body_too_large=None,
        session_actor=lambda _: 'alice', require_resource=require, notify_index=None,
        texlab_backend=None, resource_service=None, serve_media=None, serve_download=None, media_content_type=None)
    result = http.action(SimpleNamespace(), urlparse('/'), 'ignored',
                         {'article_id': 'article_0123456789ab', 'action': 'install-dependencies'})
    assert result[0] == status
    require.assert_called_once_with('article_0123456789ab', 'alice')
    assert install.called == (status == 200)
