"""A fresh process calling the real Paper HTTP/domain entry in a test instance."""
from contextlib import contextmanager
from email.message import Message
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch


def main():
    from management_portal import http_paper, paper_registration, paper_store
    from management_portal.resources import creation

    args = json.load(sys.stdin)
    store = paper_store.PaperStore(Path(args['root']))
    headers = Message()
    headers['Authorization'] = 'Bearer paper-entry-test-private-token'
    headers['Cookie'] = 'session=paper-entry-test-private-cookie'
    headers['X-Portal-Request-ID'] = 'transport-is-not-an-operation'
    if args['operation'] is not None:
        headers['Idempotency-Key'] = args['operation']
    request = SimpleNamespace(headers=headers)
    handler = SimpleNamespace(
        notify_index=lambda **kwargs: None,
        respond=lambda request, status, result: result,
    )
    publish = creation.Creation.publish
    registration = creation.Creation.registration

    def interrupted_publish(self, initialize):
        publish(self, initialize)
        raise RuntimeError('injected after publication')

    @contextmanager
    def interrupted_registration(self, connection):
        with registration(self, connection):
            yield
        raise RuntimeError('injected before commit')

    def detail(paper_id):
        path, manifest = store.manifest_for_paper_id(paper_id)
        return {'paper': store.manifest_to_paper(manifest, path)}

    with (
        patch.object(paper_registration, 'PAPER_STORE', store),
        patch.object(paper_store, 'ALLOWED_FILE_ROOTS', [store.root]),
        patch.object(paper_registration, 'get_paper_detail', detail),
        patch.object(paper_registration, '_start_summary_task'),
        patch.object(paper_registration, '_start_assets_task'),
        patch.object(creation.Creation, 'publish', interrupted_publish if args['failure'] == 'after_publish' else publish),
        patch.object(creation.Creation, 'registration', interrupted_registration if args['failure'] == 'before_commit' else registration),
    ):
        try:
            result = http_paper.PaperHttp.register(handler, request, args['payload'], {}, 'alice')
        except Exception as exc:
            result = {'error_type': type(exc).__name__, 'error': str(exc)}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
