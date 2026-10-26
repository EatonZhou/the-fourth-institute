"""A personal AMA belongs to its owner, administrators included (sample_admin 2026-10-12, decision_bd5217d08c09).

That is a rule of personal AMAs, kept by Society (``personal_ama``), not a
permission: it holds with the permission system on or off, asks nothing of
the permission port and reports what it keeps as not found.  With the
system on, the scheme judges what the rule lets through, like anything
else.  The owner and the owner's AMA Residents reach the AMA; nobody else
does, except to deliver it a message (weihao 2026-10-26).
"""
from __future__ import annotations

import json

import pytest

from management_portal import permission_system
from management_portal.society_service import personal_ama
from tests.cases import permission_test_support
from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401 - fixture
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401 - fixture

AMA_LINEAGE = 'lineage_aaaaaaaaaaaa'
AMA = 'resident_aaaaaaaaaaaa'
AMA_WORKSPACE = 'workspace_aaaaaaaaaaaa'
TEAM = 'lineage_bbbbbbbbbbbb'
WORKER = 'resident_bbbbbbbbbbbb'
SIBLING = 'resident_cccccccccccc'
SHARED = 'workspace_bbbbbbbbbbbb'


def test_the_rule_answers_a_personal_ama_by_its_owner_without_the_permission_port(monkeypatch):
    """个人 AMA 只对所有者开放是 Society 的规定：开关开或关都生效，不经过权限端口

    所有者、所有者的 AMA Resident 和 Portal 通过，其他人（包括管理员）都不通过；没有所有者的对象不受这条规定影响。
    规定拒绝的对象不交给权限系统判定，权限端口也不再接受 owners。
    """
    from management_portal.society_service import lineage_access_policy

    representative = 'resident_dddddddddddd'
    monkeypatch.setattr(personal_ama, 'owner_of', lambda principal: 'owner' if principal == representative else '')
    asked = []
    monkeypatch.setattr(permission_system, 'permitted_ids',
                        lambda actor, instruction, parameter, ids: asked.append(set(ids)) or set(ids))
    for actor, expected in (('owner', True), ('OWNER', True), (representative, True), ('admin', False), ('someone', False),
                            ('portal', True)):
        assert personal_ama.reaches(actor, 'owner') is expected, actor
        assert (personal_ama.hidden(actor, {AMA: 'owner', TEAM: ''}) == set()) is expected, actor
        changeable = lineage_access_policy.changeable_ids(actor, {AMA_LINEAGE: 'owner', TEAM: ''}, 'DeleteLineage', 'lineage_id')
        assert changeable == ({AMA_LINEAGE, TEAM} if expected else {TEAM}), actor
        # What the rule keeps is never asked of the permission system.
        assert asked.pop() == changeable, actor
    with pytest.raises(TypeError):
        permission_system.permitted('owner', 'DeleteLineage', 'lineage_id', [AMA_LINEAGE], owners={AMA_LINEAGE: 'owner'})
    assert not hasattr(permission_system, 'hidden_private') and not hasattr(permission_system, 'private_reaches')


def test_with_the_switch_on_the_rule_holds_and_the_scheme_judges_the_rest(monkeypatch):
    """权限系统开启时，规定照样独立生效，规定放行的再由方案判定

    默认方案把个人 AMA 放在所有者名下：所有者和 AMA 里的 Resident 通过，管理员和其他 User 都不通过；Project 里的 Lineage 管理员照常通过。
    即使方案给了管理员这个 AMA 的权限，规定仍让它看不到。
    """
    from tests.cases.permission_test_support import Fact

    monkeypatch.setattr(personal_ama, 'owner_of', lambda principal: 'owner' if principal == AMA else '')
    permissions = permission_test_support.enable(monkeypatch)
    try:
        permissions.users('owner', 'someone', 'admin')
        permissions.administrator('admin')
        permissions.place(Fact('lineage', AMA_LINEAGE, {'personal_space': 'personal_space:owner', 'user': 'owner'}),
                          Fact('resident', AMA, {'lineage': AMA_LINEAGE}),
                          Fact('project', 'Team'), Fact('lineage', TEAM, {'project': 'Team'}))
        for actor, expected in (('owner', True), (AMA, True), ('admin', False), ('someone', False), ('portal', True)):
            assert dict(permission_system.permitted(actor, 'DeleteLineage', 'lineage_id', [AMA_LINEAGE])) \
                == {AMA_LINEAGE: expected}, actor
        assert dict(permission_system.permitted('admin', 'DeleteLineage', 'lineage_id', [TEAM, AMA_LINEAGE])) \
            == {AMA_LINEAGE: False, TEAM: True}
        from management_portal.society_service import lineage_access_policy, resident_access

        monkeypatch.setattr(resident_access, 'actor_can_manage_residents', lambda actor: True)
        monkeypatch.setattr(lineage_access_policy, 'actor_can_manage_lineages', lambda actor: True)
        ama = {'resident_id': AMA, 'personal_owner_id': 'owner'}
        lineage = {'lineage_id': AMA_LINEAGE, 'lineage_membership': 'personal', 'created_by': 'owner'}
        for actor, expected in (('owner', True), ('admin', False), ('someone', False), ('portal', True)):
            assert lineage_access_policy.actor_can_manage_lineage(lineage, actor, operation='read') is expected, actor
            assert resident_access.resident_visible_to_actor(ama, actor) is expected, actor
        # A row giving the administrator the AMA does not get past the rule.
        permissions.grant('admin', AMA_LINEAGE, '@admin')
        permissions.grant('admin', AMA, '@admin')
        assert permission_system.permitted_ids('admin', 'GetLineage', 'lineage_id', [AMA_LINEAGE]) == {AMA_LINEAGE}
        assert not lineage_access_policy.actor_can_manage_lineage(lineage, 'admin', operation='read')
        assert not resident_access.resident_visible_to_actor(ama, 'admin')
        assert lineage_access_policy.changeable_ids('admin', {AMA_LINEAGE: 'owner'}, 'GetLineage', 'lineage_id') == set()
        # And the scheme still judges the owner: a deny row on the owner refuses the AMA to it too.
        permissions.deny('owner', AMA_LINEAGE, '@viewer')
        assert not lineage_access_policy.actor_can_manage_lineage(lineage, 'owner', operation='read')
    finally:
        permission_system.forget_implementation()


def test_an_ama_resident_is_judged_as_its_owner_under_any_scheme(monkeypatch):
    """个人 AMA 的 Resident 在权限判定中等同于其主人：主人能做的它都能做，与方案怎样放置它无关

    主人的表项、分类和临时凭证都对它生效；主人的 deny 同样约束它。方案没有把它放在主人名下时也是如此。
    """
    from tests.cases.permission_test_support import Fact

    monkeypatch.setattr(personal_ama, 'owner_of', lambda principal: 'owner' if principal == AMA else '')
    permissions = permission_test_support.enable(monkeypatch)
    try:
        permissions.users('owner', 'someone', category='intern')
        # A scheme that places the AMA's Lineage in a Project: its P path does not pass through the owner.
        permissions.place(Fact('project', 'Team'), Fact('project', 'Other'),
                          Fact('lineage', AMA_LINEAGE, {'project': 'Team'}), Fact('resident', AMA, {'lineage': AMA_LINEAGE}),
                          Fact('lineage', TEAM, {'project': 'Other'}), Fact('resident', SIBLING, {'lineage': AMA_LINEAGE}))
        assert not permissions.holds(AMA, '@admin', TEAM)
        permissions.grant('owner', TEAM, '@admin')
        assert permissions.holds('owner', '@admin', TEAM) and permissions.holds(AMA, '@admin', TEAM)
        # Another Resident in the same place is not the owner's.
        assert not permissions.holds(SIBLING, '@admin', TEAM)
        permissions.deny('owner', TEAM, '@viewer')
        assert not permissions.holds(AMA, '@viewer', TEAM)
        effective = permission_system.manage('GetEffectivePermissions', {'principal_id': AMA}, actor='portal')
        assert effective['represents'] == 'owner' and 'owner' in effective['path']
    finally:
        permission_system.forget_implementation()


def test_an_administrators_ama_passes_the_administrator_guarantee_but_never_on_its_owner(monkeypatch):
    """管理员的 AMA 同样享有 Portal 管理员保底（开关、启用方案、管理员名单），但不能取消、停用或删除主人自己

    「不能对自己做」把 AMA 当作主人本人；名单里仍只有 User。
    """
    from management_portal.permission_system import administrators

    monkeypatch.setattr(personal_ama, 'owner_of', lambda principal: {AMA: 'chief', WORKER: 'member'}.get(principal, ''))
    accounts = {'chief': {'configured': True, 'status': 'active', 'category': 'portal_admin'},
                'deputy': {'configured': True, 'status': 'active', 'category': 'portal_admin'},
                'member': {'configured': True, 'status': 'active', 'category': 'member'}}
    monkeypatch.setattr(administrators, '_account', lambda username: accounts.get(username, {}))
    assert administrators.is_administrator(AMA) and not administrators.is_administrator(WORKER)
    assert administrators.passes(AMA, 'UpdateExecutionSettings', {'permission_system_enabled': True})
    assert administrators.passes(AMA, 'ActivatePermissionScheme', {'scheme_id': 'default'})
    assert not administrators.passes(SIBLING, 'ActivatePermissionScheme', {'scheme_id': 'default'})
    assert administrators.check_change(AMA, 'deputy', category='member') is True
    for change in ({'category': 'member'}, {'status': 'disabled'}, {'delete': True}):
        with pytest.raises(ValueError, match='自己'):
            administrators.check_change(AMA, 'chief', **change)
    with pytest.raises(PermissionError):
        administrators.check_change(WORKER, 'member', category='portal_admin')


def test_temporary_credentials_count_an_ama_and_its_owner_as_one_party(monkeypatch):
    """临时凭证把 AMA 与主人算作同一方

    两者之间不能互相签发；AMA 签发时记录 AMA 自己为签发者；主人签发的凭证 AMA 可以续期、收回，主人收到的凭证对 AMA 也生效。
    """
    from management_portal.permission_system import temporary
    from tests.cases.permission_test_support import Fact

    monkeypatch.setattr(personal_ama, 'owner_of', lambda principal: 'owner' if principal == AMA else '')
    permissions = permission_test_support.enable(monkeypatch)
    try:
        permissions.users('owner', 'lender', 'someone', category='intern')
        permissions.place(Fact('project', 'Team'), Fact('lineage', TEAM, {'project': 'Team'}),
                          Fact('lineage', AMA_LINEAGE, {'personal_space': 'personal_space:owner', 'user': 'owner'}),
                          Fact('resident', AMA, {'lineage': AMA_LINEAGE}))
        permissions.grant('owner', TEAM, '@admin')
        permissions.grant('lender', 'Team', '@admin')
        scheme, engine, _instance = permissions.implementation.current()
        for issuer, recipient in (('owner', AMA), (AMA, 'owner')):
            with pytest.raises(ValueError, match='同一身份'):
                temporary.issue(engine, scheme, issuer=issuer, recipient=recipient, a='@admin', resource=TEAM)
        lent = temporary.issue(engine, scheme, issuer=AMA, recipient='someone', a='@admin', resource=TEAM)
        assert lent['issued_by'] == AMA
        assert temporary.renew(engine.store, lent['credential_id'], actor='owner', seconds=600)['credential_id'] == lent['credential_id']
        to_owner = temporary.issue(engine, scheme, issuer='lender', recipient='owner', a='@admin', resource='Team')
        assert permissions.holds(AMA, '@admin', 'Team')
        assert to_owner['credential_id'] in {item['credential_id'] for item in temporary.listing(engine.store, AMA)}
        with pytest.raises(PermissionError):
            temporary.revoke(engine.store, to_owner['credential_id'], actor='someone')
        temporary.revoke(engine.store, to_owner['credential_id'], actor=AMA)
        assert not permissions.holds(AMA, '@admin', 'Team')
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
    # Of someone else's AMA Resident only sending a message is on; its owner's buttons are all on.
    others = resident_access.resident_allows(ama, 'admin')
    assert others.pop('DeliverMessage') is True and not any(others.values())
    assert all(resident_access.resident_allows(ama, 'owner').values())
    # Forking from the message menu asks ForkResident like every other button.
    assert {'DeliverMessage', 'ForkResident'} <= set(resident_access.resident_allows(ama, 'owner'))
    # The rule's refusal is not a permission problem: someone else's AMA is not found.
    for refusal in (lineage_access_policy.lineage_refusal(lineage), resident_access.manage_refusal(ama)):
        assert isinstance(refusal, KeyError) and not isinstance(refusal, PermissionError)
    assert isinstance(resident_access.manage_refusal({'resident_id': WORKER}), PermissionError)


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


@pytest.mark.parametrize('switch', ['off', 'on'])
def test_any_resident_delivers_to_someone_elses_ama_and_learns_nothing_of_it(delivery_resident, monkeypatch, switch):
    """任何 Resident 都能给其他用户的个人 AMA 发消息，开关开或关都一样，但读不到、搜不到它的对话

    投递回执只说自己那条消息的去向，不带 AMA 的状态、队列或对话；所有者的消息不会回显给发送方。
    只开放投递：GetConversation 和 GetResident 都当它不存在，SearchMessages 搜不到它的消息。

    :design: decision_bd5217d08c09, decision_b820faf42218
    """
    from management_portal.society_service import principal_hierarchy_domain, resident_conversation_stream, resident_protocol
    from tests.cases.isa_test_support import run
    from tests.cases.permission_test_support import Fact, Permissions
    from tests.cases.test_resident_delivery_receipts import REAL_DELIVER_RESIDENT_INPUT

    monkeypatch.setattr(resident_protocol, 'deliver_resident_input', REAL_DELIVER_RESIDENT_INPUT)
    Permissions.account('et')
    ama = principal_hierarchy_domain.ensure_personal_ama('et', 'et')
    target = ama['resident_id']
    resident_protocol.change_resident_state(target, 'et', state_domain='intake', transition='close')
    sender = delivery_resident['resident_id']
    secret = 'ET 交给 AMA 的私事'
    run('DeliverMessage', {'resident_id': target, 'message': {'content_parts': [{'type': 'text', 'text': secret}]}},
        actor='et', key='owner-first').raise_for_error()
    if switch == 'on':
        permissions = permission_test_support.enable(monkeypatch)
        permissions.users('et', 'sample_admin')
        permissions.place(Fact('lineage', ama['lineage_id'], {'personal_space': 'personal_space:et', 'user': 'et'}),
                          Fact('resident', target, {'lineage': ama['lineage_id']}),
                          Fact('project', 'project_test'),
                          Fact('lineage', delivery_resident['lineage_id'], {'project': 'project_test'}),
                          Fact('resident', sender, {'lineage': delivery_resident['lineage_id']}))
    try:
        receipt = run('DeliverMessage', {'resident_id': target, 'message': {
            'content_parts': [{'type': 'text', 'text': '任务做完了，结果在 workspace 里'}]}},
            actor=sender, key='report-back').raise_for_error()
        assert receipt['resident_id'] == target and receipt['message_id']
        assert not {'state', 'queue', 'cursor', 'controls'} & set(receipt)
        assert secret not in json.dumps(receipt, ensure_ascii=False)
        delivered = resident_conversation_stream.get_repository().message(target, receipt['message_id'])
        assert delivered['author_id'] == sender
        # Delivering opens nothing else of the AMA.
        for instruction, arguments in (('GetConversation', {'resident_id': target}),
                                       ('GetResident', {'resident_id': target})):
            result = run(instruction, arguments, actor=sender)
            assert result.error and result.error['code'] == 'instruction.not_found', (instruction, result.error)
            assert secret not in json.dumps(result.error, ensure_ascii=False)
        # Search skips Residents the caller cannot see; with the switch on the scheme refuses it as missing first.
        found = run('SearchMessages', {'resident_ids': [target], 'query': '私事'}, actor=sender)
        if switch == 'on':
            assert found.error and found.error['code'] == 'instruction.not_found', found.error
        else:
            assert found.error is None and found.result['results'] == [], found.error
        assert secret not in json.dumps([found.result, found.error], ensure_ascii=False)
        # The owner reads both messages.
        texts = json.dumps(run('GetConversation', {'resident_id': target}, actor='et').raise_for_error(), ensure_ascii=False)
        assert secret in texts and '任务做完了' in texts
    finally:
        if switch == 'on':
            permission_system.forget_implementation()
