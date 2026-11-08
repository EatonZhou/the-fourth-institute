"""Native package acceptance isolates credential sources, not Harness names.

:module: operations.deployment
:importance: P1
"""
from copy import deepcopy

import pytest

from scripts.windows.native_smoke import LocalModelApi


@pytest.fixture
def fixture_registration():
    fixture = LocalModelApi()
    routes = [{'account_id': 'native_api_only' + suffix, 'provider': provider,
               'kind': 'api_profile', 'config': {'api_id': 'native_api_only'}}
              for suffix, provider in [('', 'provider_api'), ('-codex', 'codex'), ('-claude', 'claude')]]
    listed = [{'account_id': row['account_id'], 'provider': row['provider'],
               'registration': deepcopy(row)} for row in routes]
    roles = []

    def instruction(name, arguments):
        if name == 'CreateModelApi':
            return {'accounts': routes}
        if name == 'ListProviderAccounts':
            return {'accounts': listed}
        assert name == 'UpdateBackendRole'
        roles.append(arguments)

    try:
        yield fixture, instruction, listed, roles
    finally:
        fixture.close()


def test_api_only_acceptance_allows_compatible_native_harness_routes(fixture_registration):
    fixture, instruction, _, roles = fixture_registration
    fixture.register(instruction)
    assert roles == [{'backend_role': 'default', 'account_id': 'native_api_only'}]


@pytest.mark.parametrize('contamination', ['subscription', 'foreign_api', 'unregistered_account'])
def test_api_only_acceptance_rejects_external_credentials_before_routing(fixture_registration, contamination):
    fixture, instruction, listed, roles = fixture_registration
    if contamination == 'subscription':
        listed[1]['registration'].update(kind='subscription_login', config={})
    elif contamination == 'foreign_api':
        listed[1]['registration']['config']['api_id'] = 'external'
    else:
        listed.append({'account_id': 'host-codex', 'provider': 'codex'})
    with pytest.raises(RuntimeError, match='outside its disposable Model API'):
        fixture.register(instruction)
    assert roles == []
