from __future__ import annotations


from management_portal.resources.providers import project as project_provider


def test_project_summary_reads_and_saves_inside_projects_root_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """项目摘要在项目根目录内读写且拒绝越界路径"""
    with subtests.test(contract='test_project_summary_reads_and_saves_inside_projects_root'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            project_root = tmp_path / 'demo'
            project_root.mkdir()
            summary_path = project_root / 'summary.md'
            summary_path.write_text('# Before\n', encoding='utf-8')
            monkeypatch.setattr(project_provider, 'PROJECTS_ROOT', tmp_path)
            monkeypatch.setattr(project_provider, 'load_projects_payload', lambda *_args: {'projects': [{'id': 'demo', 'path': str(project_root)}]})
            payload = project_provider.project_summary('demo')
            assert payload['summary_path'] == str(summary_path)
            assert payload['text'] == '# Before\n'
            payload = project_provider.save_project_summary('demo', '# After\n')
            assert payload['text'] == '# After\n'
            assert summary_path.read_text(encoding='utf-8') == '# After\n'
    with subtests.test(contract='test_project_summary_rejects_path_outside_projects_root'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            projects_root = tmp_path / 'projects'
            projects_root.mkdir()
            outside = tmp_path / 'outside'
            outside.mkdir()
            monkeypatch.setattr(project_provider, 'PROJECTS_ROOT', projects_root)
            monkeypatch.setattr(project_provider, 'load_projects_payload', lambda *_args: {'projects': [{'id': 'outside', 'path': str(outside)}]})
            try:
                project_provider.project_summary('outside')
            except PermissionError as exc:
                assert 'outside' in str(exc)
            else:
                raise AssertionError('outside Project path must be rejected')
