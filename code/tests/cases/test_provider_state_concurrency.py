"""Status reads stay responsive and cannot interrupt native preparation."""
import json
import os
from pathlib import Path
import tempfile
from threading import Event, Thread, current_thread
import unittest
from unittest.mock import patch

from management_portal.execution import provider_updates as updates


class ProviderStateConcurrency(unittest.TestCase):
    def test_status_does_not_wait_for_installation(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(updates, 'STATE_ROOT', Path(temporary)), \
                patch.object(updates, 'runtime_available', return_value=False):
            with updates._update_state() as (state, save):
                state['preparations'] = {'dsh': {'status': 'failed'}}
                save()
                observed = []
                reader = Thread(target=lambda: observed.append(updates.preparation_status('dsh')))
                reader.start()
                reader.join(3)
                self.assertFalse(reader.is_alive())
                self.assertEqual(observed[0]['status'], 'failed')

    @unittest.skipUnless(os.name == 'nt', 'Windows read handles deny rename')
    def test_open_status_reader_does_not_abort_publication(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(updates, 'STATE_ROOT', Path(temporary)), \
                patch.object(updates, 'runtime_available', return_value=False):
            with updates._update_state() as (state, save):
                state['preparations'] = {'dsh': {'status': 'failed'}}
                save()
            writer_ready, write_now, reader_open, close_reader, written = (Event() for _ in range(5))
            failures, observed = [], []
            original = Path.read_text

            def read(path, *args, **kwargs):
                if current_thread().name != 'held-status-reader':
                    return original(path, *args, **kwargs)
                with path.open('r') as stream:
                    text = stream.read()
                    reader_open.set()
                    if not close_reader.wait(5):
                        raise TimeoutError('Reader not released')
                    return text

            def publish():
                try:
                    with updates._update_state() as (state, save):
                        writer_ready.set()
                        if not write_now.wait(5):
                            raise TimeoutError('Writer not started')
                        state['preparations']['dsh']['status'] = 'pending'
                        save()
                        written.set()
                except BaseException as error:
                    failures.append(error)

            with patch.object(Path, 'read_text', read):
                writer = Thread(target=publish)
                writer.start()
                self.assertTrue(writer_ready.wait(5))
                reader = Thread(name='held-status-reader', target=lambda: observed.append(updates.preparation_status('dsh')))
                reader.start()
                try:
                    self.assertTrue(reader_open.wait(5))
                    write_now.set()
                    self.assertFalse(written.wait(.1))
                finally:
                    close_reader.set()
                    reader.join(5)
                    writer.join(5)
            self.assertFalse(failures, failures)
            self.assertTrue(written.is_set())
            self.assertEqual(observed[0]['status'], 'failed')
            self.assertEqual(updates.preparation_status('dsh')['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
