"""A fixture named test_* must not become a runnable catalog implementation."""
from management_portal.test_framework.catalog_source import parse_source
from management_portal.test_framework.local_runs import PytestGroup


def test_discovery_excludes_fixtures_and_keeps_async_tests(tmp_path):
    """目录发现排除同名前缀的 fixture 并保留异步用例"""
    source = tmp_path / 'test_sample.py'
    source.write_text('''import pytest
from pytest import fixture
@pytest.fixture
def test_api(): pass
@fixture(scope="module")
def test_environment(): pass
async def test_request(test_api): pass
class TestPage:
    @pytest.fixture()
    def test_client(self): pass
    def test_response(self, test_client): pass
''')
    tests = parse_source(source, 'test_sample.py').tests
    assert {test.entrypoint for test in tests} == {'test_sample.py::test_request', 'test_sample.py::TestPage::test_response'}


def test_local_runner_admits_only_catalog_test_declarations(tmp_path):
    """本地执行前排除 fixture 与非测试类，保留参数化与异步测试入口"""
    source = tmp_path / 'test_sample.py'
    source.write_text('''import pytest
@pytest.fixture
def test_environment(): pass
async def test_request(): pass
class Helper:
    def test_response(self): pass
class TestPage:
    def test_response(self): pass
''')
    runner = PytestGroup({'id': 'pytest-admission'}, [], root=tmp_path)
    for name in ('test_environment', 'Helper::test_response', 'test_missing'):
        assert runner.missing_declaration(0, {'implementation': {'entrypoint': f'test_sample.py::{name}'}})
    for name in ('test_request', 'TestPage::test_response[value]'):
        assert not runner.missing_declaration(0, {'implementation': {'entrypoint': f'test_sample.py::{name}'}})
