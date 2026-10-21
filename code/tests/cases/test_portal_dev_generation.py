from __future__ import annotations


from scripts import portal_dev_generation


def test_build_id_changes_with_runtime_source_contracts(tmp_path, subtests, isolated_portal_case):
    """Dev 构建 ID 随运行源码变化，激活时原子写入，并可只更新指定组件的代次"""
    with subtests.test(contract='test_build_id_changes_with_runtime_source'), isolated_portal_case() as case_path:
        tmp_path = case_path
        package = tmp_path / 'management_portal'
        package.mkdir()
        source = package / 'app.py'
        source.write_text('VERSION = 1\n', encoding='utf-8')
        first = portal_dev_generation.source_build_id(tmp_path)
        source.write_text('VERSION = 2\n', encoding='utf-8')
        second = portal_dev_generation.source_build_id(tmp_path)
        assert first.startswith('dev-')
        assert second.startswith('dev-')
        assert first != second
    with subtests.test(contract='test_activate_writes_readable_generation_atomically'), isolated_portal_case() as case_path:
        tmp_path = case_path
        package = tmp_path / 'management_portal'
        package.mkdir()
        (package / 'app.py').write_text('pass\n', encoding='utf-8')
        environment = tmp_path / 'runtime' / 'source-generation.env'
        build_id = portal_dev_generation.activate(tmp_path, environment)
        assert portal_dev_generation.read_build_id(environment) == build_id
        assert environment.read_text(encoding='utf-8') == f'PORTAL_BUILD_ID={build_id}\n'
        components = portal_dev_generation.read_component_build_ids(environment.with_name('component-generations.json'))
        assert components == {component: build_id for component in portal_dev_generation.COMPONENTS}
    with subtests.test(contract='test_activate_updates_only_selected_component_generation'), isolated_portal_case() as case_path:
        tmp_path = case_path
        package = tmp_path / 'management_portal'
        package.mkdir()
        source = package / 'app.py'
        source.write_text('VERSION = 1\n', encoding='utf-8')
        environment = tmp_path / 'runtime' / 'source-generation.env'
        first = portal_dev_generation.activate(tmp_path, environment)
        source.write_text('VERSION = 2\n', encoding='utf-8')
        second = portal_dev_generation.activate(tmp_path, environment, components=['society_service'])
        components = portal_dev_generation.read_component_build_ids(environment.with_name('component-generations.json'))
        assert second != first
        assert components['society_service'] == second
        assert components['execution_worker'] == first
