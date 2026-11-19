"""Resource action discovery does not load the provider that executes it.

:module: isa.instructions
:design: decision_ad3e21094f14
"""
import subprocess
import sys
import textwrap


def test_knowledge_action_discovery_without_provider_runtime():
    result = subprocess.run([sys.executable, "-c", textwrap.dedent("""
        import importlib.abc
        import sys

        class NoKnowledgeRuntime(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'management_portal.resources.providers.knowledge':
                    raise AssertionError('Action discovery imported the Knowledge runtime')

        sys.meta_path.insert(0, NoKnowledgeRuntime())
        from management_portal.hatg.resource_actions import resource_action_catalog
        catalog = resource_action_catalog()
        create = catalog[('decision', 'knowledge.file.create')]
        assert create.mutates
        assert [p.name for p in create.parameters] == ['filename', 'text', 'expected_version']
        assert ('skill', 'knowledge.file.create') not in catalog
        assert ('experiment', 'knowledge.file.create') not in catalog
    """)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
