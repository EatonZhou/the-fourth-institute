"""Bridge requirements are derived from instance bindings, not source accounts."""
from .router_store import RouterStore
from .weixin_store import WeixinStore
from .runtime import WEIXIN_COMPONENTS, COMPONENTS
from scripts import instance_services


def update(instance, phase, plan):
    # RouterStore owns additive schema evolution and rejects unsupported old
    # schemas; the updater cannot turn that rejection into an empty migration.
    router = RouterStore()
    router.init_schema()
    wanted = set(WEIXIN_COMPONENTS) if WeixinStore(router).active_actors() else set()
    from .contacts_store import database, contacts
    with database().read() as connection:
        if contacts(connection):
            wanted.update(COMPONENTS[:2])
    registered = set(instance_services.components(instance))
    control = instance_services.controller(instance)
    missing = sorted(wanted - registered)
    blocked = []
    prerequisites = ('nats', 'resource', 'execution', 'society')
    active = {c for c in prerequisites if c in registered and
              control.status(instance_services.unit_name(instance, c))['active'] == 'active'}
    start_services = missing if active else []
    if missing:
        # A partial registered bridge that the administrator stopped is an
        # explicit state. Do not infer consent to enable it from a saved token.
        for component in wanted & registered:
            state = control.status(instance_services.unit_name(instance, component))
            if active and state['active'] != 'active':
                blocked.append({'component': component, 'error': 'required_bridge_explicitly_stopped'})
        for component in prerequisites:
            if component not in registered or (active and component not in active):
                blocked.append({'component': component, 'error': 'bridge_prerequisite_not_running'})
    if phase == 'verify':
        for component in plan.get('start_services', []):
            if component not in registered or control.status(instance_services.unit_name(instance, component))['active'] != 'active':
                blocked.append({'component': component, 'error': 'bridge_not_running'})
    return {'services': missing, 'start_services': start_services, 'required': sorted(wanted), 'blockers': blocked,
            'external_message_roundtrip': 'not_run', 'schema': 'owner_prepared'}
