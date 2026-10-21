"""A personal AMA belongs to its owner, administrators included (sample_admin 2026-10-12, decision_bd5217d08c09).

With the permission system on, the scheme says so by where it places a
personal AMA (decision_b820faf42218 §5.5): the default scheme puts it under
its owner, and the engine decides like for anything else.  With the system
off, the permission port keeps this one rule itself: a personal AMA's
resources answer to their registered owner.  Either way its owner and the
Residents representing the owner reach it; nobody else does.
"""
from __future__ import annotations

import pytest

from management_portal import permission_system
from tests.cases import permission_test_support

AMA_LINEAGE = 'lineage_aaaaaaaaaaaa'
AMA = 'resident_aaaaaaaaaaaa'
AMA_WORKSPACE = 'workspace_aaaaaaaaaaaa'
TEAM = 'lineage_bbbbbbbbbbbb'
WORKER = 'resident_bbbbbbbbbbbb'
SIBLING = 'resident_cccccccccccc'
SHARED = 'workspace_bbbbbbbbbbbb'


def test_the_port_answers_a_personal_ama_by_its_owner_while_off(monkeypatch):
    """权限系统关闭时，权限端口按登记所有者回答个人 AMA，管理员也不例外

    所有者和代表所有者的 Resident 通过任何指令，其他人（包括管理员）都不通过；其他资源对已登录主体放行。
    """
    owners = {AMA: 'owner', AMA_LINEAGE: 'owner'}
    representative = 'resident_dddddddddddd'
    monkeypatch.setattr(permission_system, '_represented_owner', lambda principal: 'owner' if principal == representative else '')
    for actor, expected in (('owner', True), (representative, True), ('admin', False), ('someone', False), ('portal', True)):
        assert dict(permission_system.permitted(actor, 'DeleteLineage', 'lineage_id', [AMA, AMA_LINEAGE], owners=owners)) \
            == {AMA: expected, AMA_LINEAGE: expected}
    shared = dict(permission_system.permitted('admin', 'DeleteLineage', 'lineage_id', [TEAM, AMA], owners=owners))
    assert shared == {AMA: False, TEAM: True}


def test_the_scheme_keeps_a_personal_ama_with_its_owner_while_on(monkeypatch):
    """权限系统开启时，个人 AMA 由默认方案放在所有者名下，引擎像判定其他资源一样判定它

    端口不再拦截：owners 参数不起作用。所有者和 AMA 里的 Resident 通过，管理员和其他 User 都不通过；Project 里的 Lineage 管理员照常通过。
    """
    from tests.cases.permission_test_support import Fact

    permissions = permission_test_support.enable(monkeypatch)
    try:
        permissions.users('owner', 'someone', 'admin')
        permissions.administrator('admin')
        permissions.place(Fact('lineage', AMA_LINEAGE, {'personal_space': 'personal_space:owner', 'user': 'owner'}),
                          Fact('resident', AMA, {'lineage': AMA_LINEAGE}),
                          Fact('project', 'Team'), Fact('lineage', TEAM, {'project': 'Team'}))
        owners = {AMA: 'someone', AMA_LINEAGE: 'someone'}
        for actor, expected in (('owner', True), (AMA, True), ('admin', False), ('someone', False), ('portal', True)):
            assert dict(permission_system.permitted(actor, 'DeleteLineage', 'lineage_id', [AMA_LINEAGE], owners=owners)) \
                == {AMA_LINEAGE: expected}, actor
        assert dict(permission_system.permitted('admin', 'DeleteLineage', 'lineage_id', [TEAM, AMA_LINEAGE])) \
            == {AMA_LINEAGE: False, TEAM: True}
        assert permission_system.hidden_private('admin', {AMA_LINEAGE: 'owner'}) == {AMA_LINEAGE}
        assert permission_system.hidden_private('owner', {AMA_LINEAGE: 'owner'}) == set()
        # Lists filter by the domain's read rule, which asks the exact read: an administrator's lists leave others' AMAs out.
        from management_portal.society_service import lineage_access_policy, resident_access

        monkeypatch.setattr(resident_access, 'actor_can_manage_residents', lambda actor: True)
        monkeypatch.setattr(lineage_access_policy, 'actor_can_manage_lineages', lambda actor: True)
        ama = {'resident_id': AMA, 'personal_owner_id': 'owner'}
        lineage = {'lineage_id': AMA_LINEAGE, 'lineage_membership': 'personal', 'created_by': 'owner'}
        for actor, expected in (('owner', True), ('admin', False), ('someone', False), ('portal', True)):
            assert lineage_access_policy.actor_can_manage_lineage(lineage, actor, operation='read') is expected, actor
            assert resident_access.resident_visible_to_actor(ama, actor) is expected, actor
    finally:
        permission_system.forget_implementation()


def test_listing_requests_names_the_residents(monkeypatch):
    """ListResidentRequests 必须给出 resident_ids，每个 Resident 单独判定

    没有“省略时列出调用者能回答的全部请求”的隐式范围；权限系统开启时，管理员对个人 AMA 中的 Resident 被拒绝，所有者通过。

    :design: decision_898b8911a2ff, decision_2fe6fd5367fb
    """
    from management_portal.hatg import catalog
    from management_portal.hatg.contracts import InstructionError
    from tests.cases.permission_test_support import Fact

    with pytest.raises(InstructionError):
        catalog.instruction_catalog()['ListResidentRequests'].validate_arguments({})
    permissions = permission_test_support.enable(monkeypatch)
    try:
        permissions.users('owner', 'admin')
        permissions.administrator('admin')
        permissions.place(Fact('lineage', AMA_LINEAGE, {'personal_space': 'personal_space:owner', 'user': 'owner'}),
                          Fact('resident', AMA, {'lineage': AMA_LINEAGE}),
                          Fact('project', 'Team'), Fact('lineage', TEAM, {'project': 'Team'}),
                          Fact('resident', WORKER, {'lineage': TEAM}))
        listed = lambda actor, rid: permission_system.allows(actor, 'ListResidentRequests', {'resident_ids': [rid]})  # noqa: E731
        assert listed('admin', WORKER) and not listed('admin', AMA)
        assert listed('owner', AMA)
    finally:
        permission_system.forget_implementation()


def test_scheduled_lineage_traffic_always_checks_current_permissions():
    """Lineage 入口的 DeliverMessage 和 ClearContext 总按调用者当前权限授权"""
    from management_portal.hatg.contracts import InstructionContext
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.hatg.kernel import InstructionKernel
    from management_portal.society_service import isa

    checked = []
    kernel = InstructionKernel(
        isa.binding(), store=None,
        authorize=lambda declaration, arguments, context: checked.append((declaration.name, context.caller)),
    )
    call = type("Call", (), {"arguments": {"resident_id": WORKER}})()
    for caller in ("portal", "sample_admin", SIBLING):
        for instruction in ("DeliverMessage", "ClearContext"):
            declaration = instruction_catalog()[instruction]
            context = InstructionContext.from_payload({
                "caller": caller, "entry": "lineage", "lineage_member": WORKER,
            })
            kernel._authorize_call(declaration, call, context)
            assert "lineage_member" not in context.to_payload()
    assert checked == [
        (instruction, caller)
        for caller in ("portal", "sample_admin", SIBLING)
        for instruction in ("DeliverMessage", "ClearContext")
    ]


def test_system_entry_skips_permission_checks_only_for_portal_itself():
    """system 入口只对 portal 自身跳过权限检查，其他真实发送者照常授权

    Chatroom delivery and Notice Board classification run in-process as their real sender."""
    from management_portal.hatg.contracts import InstructionContext
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.hatg.kernel import InstructionKernel
    from management_portal.society_service import isa

    checked = []
    kernel = InstructionKernel(
        isa.binding(), store=None,
        authorize=lambda declaration, arguments, context: checked.append(context.caller),
    )
    deliver = instruction_catalog()['DeliverMessage']
    call = type('Call', (), {'arguments': {'resident_id': WORKER}})()
    for caller in ('portal', 'sample_guest', SIBLING, 'service:portal'):
        kernel._authorize_call(deliver, call, InstructionContext(caller=caller, entry='system'))
    assert checked == ['sample_guest', SIBLING, 'service:portal']


def test_domain_access_of_a_personal_ama_follows_its_owner(monkeypatch):
    """领域层对个人 AMA 及其 Lineage 的读和管理只认所有者，共享对象对所有已登录主体开放"""
    from management_portal.society_service import lineage_access_policy, resident_access

    monkeypatch.setattr(resident_access, 'actor_can_manage_residents', lambda actor: True)
    monkeypatch.setattr(lineage_access_policy, 'actor_can_manage_lineages', lambda actor: True)
    ama = {'resident_id': AMA, 'personal_owner_id': 'owner'}
    lineage = {'lineage_id': AMA_LINEAGE, 'lineage_membership': 'personal', 'created_by': 'owner'}
    for actor, allowed in (('owner', True), ('admin', False), ('portal', True)):
        assert resident_access.resident_visible_to_actor(ama, actor) is allowed
        assert resident_access.can_manage_resident(ama, actor) is allowed
        assert lineage_access_policy.actor_can_manage_lineage(lineage, actor, operation='read') is allowed
        assert lineage_access_policy.actor_can_manage_lineage(lineage, actor) is allowed
    assert resident_access.can_manage_resident({'resident_id': WORKER}, 'admin')
    assert lineage_access_policy.actor_can_manage_lineage({'lineage_id': TEAM, 'lineage_membership': 'shared'}, 'admin')
    # The buttons of someone else's AMA Resident are all off; its owner's are on.
    assert not any(resident_access.resident_allows(ama, 'admin').values())
    assert all(resident_access.resident_allows(ama, 'owner').values())
    # Forking from the message menu asks ForkResident like every other button.
    assert {'DeliverMessage', 'ForkResident'} <= set(resident_access.resident_allows(ama, 'owner'))
    # The refusal names the AMA's owner, so a User signed in under another account sees why.
    assert lineage_access_policy.lineage_denial(lineage, '访问') == f'{AMA_LINEAGE} 是 owner 的个人 AMA，只有 owner 本人可以访问'


def test_resource_search_and_identity_reads_hide_a_personal_ama_like_its_domain(isolated_portal_case):
    """资源搜索和按 ID 读取与领域层一样，对所有者以外的主体隐藏个人 AMA

    SearchResources, ResolveResource and references read the index and descriptors, not the domain."""
    from unittest import mock

    from management_portal import resource_index_backend as backend
    from management_portal.resources import ResourceService
    from management_portal.resources.collaboration_registry import COLLABORATION_DESCRIPTOR_SCHEMA, CollaborationDescriptorStore
    from management_portal.resources.registration import ResourceRegistrationStore

    attributes = {AMA_LINEAGE: {'lineage_membership': 'personal', 'created_by': 'owner'}, AMA: {'personal_owner_id': 'owner'},
                  TEAM: {'lineage_membership': 'shared', 'created_by': 'owner'}, WORKER: {}}
    store = backend.IndexStore()
    store.publish_snapshot('test', [backend.resource_record(
        resource_id=resource_id, resource_type=resource_id.split('_')[0], title='Resource', summary='', adapter='test',
        path=f'/tmp/{resource_id}.json', modified_at='2026-10-12T00:00:00Z', facets=('collaboration',),
        attributes=values, search_terms=(), url=f'/{resource_id}',
    ) for resource_id, values in attributes.items()], {})
    found = lambda actor: {item['resource_id'] for item in store.query(actor=actor, q='Resource')['resources']}
    assert found('owner') == set(attributes)
    assert found('admin') == {TEAM, WORKER}
    for actor, readable in (('owner', True), ('admin', False)):
        for resource_id in (AMA, AMA_LINEAGE):
            if readable:
                assert store.get(resource_id, actor=actor)['resource_id'] == resource_id
            else:
                with pytest.raises(KeyError):
                    store.get(resource_id, actor=actor)
    with isolated_portal_case():
        descriptors = CollaborationDescriptorStore()
        descriptors.put({'schema': COLLABORATION_DESCRIPTOR_SCHEMA, 'resource_id': AMA, 'title': 'AMA', 'status': 'active',
                         'attributes': attributes[AMA]})
        service = ResourceService(index_provider=mock.Mock(), collaboration_store=descriptors,
                                  registration_store=ResourceRegistrationStore())
        assert service.get(AMA, actor='owner')['title'] == 'AMA'
        with pytest.raises(KeyError):
            service.get(AMA, actor='admin')


def test_a_personal_ama_folder_answers_only_to_that_ama(monkeypatch, isolated_portal_case):
    """个人 AMA 文件夹中的 Workspace、文件和 README 只对所有者开放

    Workspaces registered inside the AMA's Lineage folder, its files by path, and its README as agent context."""
    from management_portal import agent_context_selection, resource_index_backend as backend
    from management_portal.society_service import resident_workspace_domain, workspace_domain
    from management_portal.society_service.state_store import get_lineage_data_store
    from management_portal.resources.workspace_store import get_workspace_store

    roamer = 'resident_dddddddddddd'
    home, node, team, above, roamer_own = (f'workspace_{letter * 12}' for letter in 'cdef9')
    with isolated_portal_case() as case_path:
        agents = case_path / 'Agents'
        folder = agents / 'PersonalAgents' / 'owner' / 'ama'
        (folder / 'node-workspaces' / 'w').mkdir(parents=True)
        (agents / 'team-agent').mkdir()
        note, out, readme = folder / 'README.md', folder / 'node-workspaces' / 'w' / 'out.md', agents / 'PersonalAgents' / 'readme.md'
        shared, chat = agents / 'team-agent' / 'README.md', agents / 'PersonalAgents' / 'people' / 'owner' / 'memory.md'
        chat.parent.mkdir(parents=True)
        for path in (note, out, readme, shared, chat):
            path.write_text('# Text\n', encoding='utf-8')
        # The canonical entry links to the folder, as under /share/Agents.
        (agents / AMA_LINEAGE).symlink_to(folder)
        monkeypatch.setenv('PORTAL_LINEAGE_FOLDER_ROOT', str(agents))
        monkeypatch.setattr(workspace_domain, 'SHARED_COLLABORATION_ROOTS', (agents,))
        monkeypatch.setattr(workspace_domain, 'PERSONAL_ROOTS', (agents / 'PersonalAgents',), raising=False)
        paths = {home: agents / AMA_LINEAGE, node: out.parent, team: agents / 'team-agent', above: readme.parent}

        def seed(connection):
            for workspace_id, path in (*paths.items(), (roamer_own, case_path / 'roamer')):
                get_workspace_store().ensure(workspace_id, str(path), connection=connection)
            for lineage_id, membership in ((AMA_LINEAGE, 'personal'), (TEAM, 'shared')):
                connection.execute("INSERT INTO lineages(lineage_id,lineage_membership,created_by,revision,layout_revision) "
                                   "VALUES (%s,%s,'owner',1,1)", (lineage_id, membership))
            for resident_id, lineage_id, owned in ((AMA, AMA_LINEAGE, node), (WORKER, TEAM, team), (roamer, TEAM, roamer_own)):
                connection.execute('INSERT INTO residents(resident_id, owned_workspace_id) VALUES (%s, %s)', (resident_id, owned))
                connection.execute("INSERT INTO lineage_nodes(node_id,lineage_id,node_type) VALUES (%s,%s,'resident')", (resident_id, lineage_id))
            # A team Resident bound to the directory holding AMA folders.
            connection.execute("INSERT INTO resident_workspace_bindings(resident_id, workspace_id, purpose) VALUES (%s, %s, '')", (roamer, above))
        get_lineage_data_store().database.write(seed)
        monkeypatch.setattr(resident_workspace_domain, 'resolve_resident_identity', lambda resident_id, _actor: {'resident_id': resident_id})

        store = backend.IndexStore()
        store.publish_snapshot('test', [backend.resource_record(
            resource_id=workspace_id, resource_type='workspace', title='Workspace', summary='', adapter='test',
            path=f'/tmp/{workspace_id}.json', modified_at='2026-10-12T00:00:00Z', facets=('collaboration',),
            attributes={'path': str(path)}, search_terms=(), url=f'/{workspace_id}',
        ) for workspace_id, path in paths.items()], {})
        found = lambda actor: {item['resource_id'] for item in store.query(actor=actor, q='Workspace')['resources']}
        assert found('owner') == set(paths)
        assert found('admin') == {team, above}

        link = lambda target, actor: workspace_domain.get_workspace_link_file(str(target), actor)['allows']['UpdateWorkspaceFile']
        # /share/Agents stays open to everyone outside the AMA's own folder.
        assert link(shared, 'admin') is True
        for target in (readme, chat):
            assert link(target, 'admin') is True
        for target in (note, out):
            with pytest.raises(PermissionError):
                link(target, 'admin')
            assert link(target, 'owner') is True

        monkeypatch.setattr(agent_context_selection, 'HOST_AGENTS_ROOT', agents)
        agent_context_selection._cached_options.cache_clear()
        assert [item['id'] for item in agent_context_selection.list_options()['agents']] == ['team-agent']
        agent_context_selection._cached_options.cache_clear()
        rendered = agent_context_selection.prompt_references({'agents': [{'id': AMA_LINEAGE}, {'id': 'team-agent'}]},
                                                             item_char_limit=100, total_char_limit=1000)
        assert AMA_LINEAGE not in rendered and 'id="team-agent"' in rendered
