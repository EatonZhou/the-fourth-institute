"""Published snapshots, personal installations and their ISA authorization."""
import re

import pytest

from tests.cases.isa_test_support import ok, run
from tests.cases.permission_test_support import permissions as permissions  # noqa: F401
from tests.cases.test_isa_lineage_lifecycle import lineage as lineage, future_creation_store as future_creation_store  # noqa: F401


def create(actor='store-author', **changes):
    return ok('CreateTemplate', {'template': {'kind':'prompt','title':'Review','body':{'message':'Review carefully'}, **changes}}, actor=actor)['template']


@pytest.fixture
def users(permissions):
    # The shipped default grants everyone public admin. Exercise the narrower
    # contributor policy too, without changing the site's existing scheme.
    from copy import deepcopy
    from tests.cases.permission_test_support import ACTIVE_COPY
    rules = deepcopy(permissions.implementation.schemes.get(ACTIVE_COPY)['rules'])
    next(row for row in rules['entries'] if row.get('id') == 'everyone_manages_public')['a'] = '@contributor'
    permissions.implementation.manage('UpdatePermissionScheme', {'scheme_id':ACTIVE_COPY,'rules':rules}, actor='portal', enforce=False, collect_facts=lambda: [])
    permissions.users('store-author','store-reader','store-admin')
    permissions.administrator('store-admin')


def test_snapshot_install_update_conflicts_and_unpublish(lineage, users):
    source = create()
    first = ok('PublishTemplate', {'template_id':source['template_id'],'release_note':'First'}, actor='store-author')['item']
    identity = first['store_id']
    assert re.fullmatch(r'store-[0-9a-f]{12}',identity)
    assert first['instructions_used'] == ['DeliverMessage']
    found = ok('ListTemplateStore', {'search':'store-author','group':'task'}, actor='store-reader')['items']
    assert found[0]['store_id'] == identity
    assert ok('GetTemplateStoreItem', {'store_id':identity}, actor='store-reader')['item'] == found[0]
    installed = ok('InstallStoreTemplate', {'store_id':identity}, actor='store-reader')['template']
    local_id = installed['template_id']
    assert installed['created_by'] == 'store-reader' and not installed['store_status']['locally_modified']
    assert ok('InstallStoreTemplate', {'store_id':identity}, actor='store-reader')['template']['template_id'] == local_id
    ok('UpdateTemplate', {'template_id':source['template_id'],'title':'Review v2'}, actor='store-author')
    assert ok('ListTemplateStore', {}, actor='store-reader')['items'][0]['title'] == 'Review'
    second = ok('PublishTemplate', {'template_id':source['template_id']}, actor='store-author')['item']
    assert second['store_id'] == identity and second['version'] == 2
    assert len(second['versions']) == 2
    state = ok('GetTemplate', {'template_id':local_id}, actor='store-reader')['template']['store_status']
    assert state['has_update'] and not state['locally_modified']
    synced = ok('UpdateInstalledTemplate', {'store_id':identity,'target_template_id':local_id}, actor='store-reader')['template']
    assert synced['title'] == 'Review v2' and synced['template_id'] == local_id
    ok('UpdateTemplate', {'template_id':local_id,'title':'My edit'}, actor='store-reader')
    ok('UpdateTemplate', {'template_id':source['template_id'],'title':'Review v3'}, actor='store-author')
    ok('PublishTemplate', {'template_id':source['template_id']}, actor='store-author')
    assert ok('GetTemplate', {'template_id':local_id}, actor='store-reader')['template']['store_status']['locally_modified']
    repeated = ok('InstallStoreTemplate', {'store_id':identity}, actor='store-reader')['template']
    assert repeated['template_id'] == local_id and repeated['title'] == 'My edit'
    copied = ok('InstallStoreTemplateAsNew', {'store_id':identity,'target_template_id':local_id}, actor='store-reader')
    assert copied['detached_template_id'] == local_id and copied['template']['template_id'] != local_id
    retained = ok('GetTemplate', {'template_id':local_id}, actor='store-reader')['template']
    assert retained['title'] == 'My edit' and retained['store_status'] is None
    new_id = copied['template']['template_id']
    ok('UpdateTemplate', {'template_id':new_id,'description':'personal'}, actor='store-reader')
    overwritten = ok('UpdateInstalledTemplate', {'store_id':identity,'target_template_id':new_id}, actor='store-reader')['template']
    assert not overwritten['store_status']['locally_modified']
    assert run('UnpublishTemplate', {'store_id':identity}, actor='store-reader').status == 'failed'
    ok('UnpublishTemplate', {'store_id':identity}, actor='store-admin')
    assert not ok('ListTemplateStore', {}, actor='store-reader')['items']
    assert run('GetTemplateStoreItem', {'store_id':identity}, actor='store-reader').status == 'failed'
    assert not ok('GetTemplate', {'template_id':new_id}, actor='store-reader')['template']['store_status']['has_update']
    assert run('InstallStoreTemplate', {'store_id':identity}, actor='store-reader').status == 'failed'
    restored = ok('PublishTemplate', {'template_id':source['template_id']}, actor='store-author')['item']
    assert restored['store_id'] == identity and restored['version'] == 4
    ok('UnpublishTemplate', {'store_id':identity}, actor='store-author')


@pytest.mark.parametrize('kind,body,group,used',[
    ('structure', {'roles':[{'key':'worker','from':'new','settings':{}}]}, 'organization',['CreateResident','Functionalize']),
    ('familize', {}, 'organization',['Familize']),
    ('prompt', {'message':'hello'}, 'task',['DeliverMessage']),
    ('script', {'script':'ListTemplates\nGetTemplate template_id=template_0123456789ab\nListTemplates','endpoints':[]}, 'task',['ListTemplates','GetTemplate']),
    ('explore', {}, 'task',['ForkResident','ApplyLineageChanges','DeliverMessage']),
])
def test_publish_kinds_and_instruction_summary(lineage, users, kind, body, group, used):
    source = create(kind=kind,body=body)
    item = ok('PublishTemplate', {'template_id':source['template_id']}, actor='store-author')['item']
    assert item['group'] == group and item['instructions_used'] == used
    assert run('PublishTemplate', {'template_id':source['template_id']}, actor='store-reader').status == 'failed'


def test_builtin_ownership_and_save_as(lineage, users):
    builtins = ok('ListTemplates', {}, actor='store-author')['templates']
    for item in builtins:
        assert run('PublishTemplate', {'template_id':item['template_id']}, actor='store-author').status == 'failed'
    source = create()
    item = ok('PublishTemplate', {'template_id':source['template_id']}, actor='store-author')['item']
    installed = ok('InstallStoreTemplate', {'store_id':item['store_id']}, actor='store-reader')['template']
    assert run('UpdateInstalledTemplate', {'store_id':item['store_id'],'target_template_id':installed['template_id']}, actor='store-author').status == 'failed'
    copied = ok('CreateTemplate', {'template':{key:installed[key] for key in ('kind','category','title','description','targets','parameters','body')}}, actor='store-reader')['template']
    assert copied['store_status'] is None and copied['installed_from_store_id'] is None


def test_admin_publishes_resident_source_and_preserves_publisher(lineage, users):
    from management_portal.society_service import templates

    creator = 'resident_0123456789ab'
    source = templates.create({'kind':'prompt','title':'Resident source','body':{'message':'Review carefully'}}, creator)
    args = {'template_id':source['template_id']}
    assert run('PublishTemplate', args, actor='store-reader').status == 'failed'
    first = ok('PublishTemplate', args, actor='store-admin')['item']
    assert first['publisher'] == creator and first['version'] == 1
    installed = ok('InstallStoreTemplate', {'store_id':first['store_id']}, actor='store-reader')['template']
    assert installed['created_by'] == 'store-reader'
    assert installed['store_status']['publisher'] == creator
    ok('UpdateTemplate', {**args,'title':'Updated Resident source'}, actor='store-admin')
    second = ok('PublishTemplate', {**args,'release_note':'Updated'}, actor='store-admin')['item']
    assert second['publisher'] == creator
    assert second['store_id'] == first['store_id'] and second['version'] == 2
    assert second['title'] == 'Updated Resident source' and second['release_note'] == 'Updated'
    status = ok('GetTemplate', {'template_id':installed['template_id']}, actor='store-reader')['template']['store_status']
    assert status['has_update'] and status['publisher'] == creator


def test_admin_cannot_publish_human_personal_template(lineage, users):
    source = create()
    args = {'template_id':source['template_id']}
    assert run('PublishTemplate', args, actor='store-admin').status == 'failed'
    assert ok('GetTemplate', args, actor='store-author')['template']['published_store'] is None
    item = ok('PublishTemplate', args, actor='store-author')['item']
    assert item['publisher'] == 'store-author'
    assert run('PublishTemplate', args, actor='store-admin').status == 'failed'


def test_admin_cannot_publish_resident_installed_copy(lineage, users):
    from management_portal.society_service.templates import marketplace

    source = create()
    item = ok('PublishTemplate', {'template_id':source['template_id']}, actor='store-author')['item']
    installed = marketplace.install(item['store_id'], 'resident_0123456789ab')['template']
    assert run('PublishTemplate', {'template_id':installed['template_id']}, actor='store-admin').status == 'failed'


def test_structure_effect_and_rewrite_instruction_summary():
    from management_portal.society_service.templates.marketplace import instructions_used
    assert instructions_used({'kind':'structure','body':{
        'roles':[{'from':'fork'}], 'match':[{'kind':'route','op':'delete'},{'kind':'effect','op':'update'}],
        'components':[{'kind':'effect','config':{'script':'ListTemplates\nListTemplates'}}],
        'wires':[{}], 'function_block':True}}) == ['ForkResident','ApplyLineageChanges','Functionalize','ListTemplates']
