"""Retained-data Windows application upgrades through the release installer.

Run from the immutable unpacked engineering, outside the application being
updated. Every registered instance participates in one stopped code/data pair.
Portable and macOS upgrades retain their existing owners and guards.
"""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
import tempfile

from management_common.file_permissions import ensure_private_directory, atomic_write_private_bytes
from management_common.instance_config import read_installation
from scripts import tfi_application_snapshot as snapshot
from scripts import tfi_release_provider_services as providers
from scripts import tfi_application_freeze as barrier
from scripts import tfi_application_clients as clients
from scripts.tfi_instance_layout import database_root


TERMINAL = {"complete", "restored", "prebackup_failed"}


def require_desktop():
    if sys.platform == 'win32':
        import ctypes
        session = ctypes.c_ulong()
        if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)) or session.value == 0:
            raise ValueError('Run application maintenance in the logged-in desktop session, not SSH session 0')


def control_root(application):
    return application.parent / ('.' + application.name + '-maintenance')


def pending(application):
    path = control_root(Path(application)) / 'upgrade.json'
    return path.is_file() and json.loads(path.read_text(encoding='utf-8')).get('phase') not in TERMINAL


def states(application):
    base = application / 'instance'
    result = []
    for root in sorted(base.iterdir()):
        if snapshot.is_directory_link(root):
            raise ValueError('Instance directory must not be a link')
        if not root.is_dir() or not (root / 'installation.json').is_file():
            raise ValueError('Unregistered entry in application instance directory')
        state = read_installation(root)
        target = state['target']
        if (Path(target['install_root']) != root
                or Path(target.get('shared_code_root', '')) != application / 'code'):
            raise ValueError('Instance does not belong to this application')
        for value in target['data_roots'].values():
            path = Path(value)
            if not path.resolve().is_relative_to(application) or path.resolve() != path:
                raise ValueError('External or redirected data root requires a separate migration')
        if database_root(target).resolve() != database_root(target):
            raise ValueError('Database root is redirected')
        result.append(state)
    if not result or len({s['target']['service_prefix'] for s in result}) != len(result):
        raise ValueError('Application has no instances or has duplicate service identities')
    return result


class ApplicationUpgrade:
    def __init__(self, api, application):
        self.api = api
        self.application = Path(application).absolute()
        if self.application.resolve() != self.application or not self.application.is_dir():
            raise ValueError('Application must be an existing canonical directory')
        if Path(__file__).resolve().is_relative_to(self.application):
            raise ValueError('Use the new engineering installer outside the application')
        self.control = control_root(self.application)
        if self.control.resolve() != self.control:
            raise ValueError('Maintenance directory is redirected')
        self.journal_path = self.control / 'upgrade.json'

    def save(self, journal, phase, **values):
        journal.update(values, phase=phase)
        journal.setdefault('stages', []).append({'phase': phase, 'time': time.time()})
        # Admission must follow a flushed, atomically published journal. A
        # torn or merely buffered phase must never authorize stale recovery.
        atomic_write_private_bytes(self.journal_path,
            (json.dumps(journal, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))

    def schema(self, state, *, apply=False):
        # The new module executes against this target, without inheriting the
        # source Portal's DSN, namespace, account environment or PYTHONPATH.
        target = state['target']
        env = self.api.clean_environment(target)
        env.pop('PORTAL_DATABASE_NAMESPACE', None)
        code = Path(__file__).resolve().parents[1]
        env['PYTHONPATH'] = str(code)
        command = [self.api.runtime_python(target), '-m', 'scripts.tfi_release_schema']
        if apply:
            command.append('--apply')
        result = subprocess.run(command, env=env, cwd=code, text=True, encoding='utf-8',
                                capture_output=True, timeout=120)
        if result.returncode:
            # Full DB diagnostics stay in the private target log, never in
            # a release receipt or a command line with connection credentials.
            log = self.control / 'schema-error.log'
            ensure_private_directory(self.control)
            if log.exists():
                log.unlink()
            with snapshot.create_private_file(log) as stream:
                stream.write(result.stderr.encode('utf-8'))
            raise ValueError('Schema check/migration failed; inspect private schema-error.log')
        return json.loads(result.stdout)

    def database_check(self, state):
        import psycopg
        target = state['target']
        with psycopg.connect(self.api.clean_environment(target)['PORTAL_DATABASE_URL']) as connection:
            connection.execute('SET TRANSACTION READ ONLY')
            if int(connection.execute('SHOW server_version_num').fetchone()[0]) // 10000 != 16:
                raise ValueError('Database server is not PostgreSQL 16')
            directory = connection.execute('SHOW data_directory').fetchone()[0]
            if Path(directory).resolve() != database_root(target).resolve():
                raise ValueError('Connected database is not owned by this instance')
            if connection.execute("SELECT count(*) FROM pg_tablespace WHERE pg_tablespace_location(oid)<>''").fetchone()[0]:
                raise ValueError('External PostgreSQL tablespaces require a separate migration')
            for table, key, expected in (
                ('agent.agent_store_meta', 'schema', 'tfi.agent-data.v18'),
                ('execution_runtime.execution_store_meta', 'schema_version', '19'),
                ('resource_registry.registry_meta', 'schema', 'tfi.resource-registry.v2'),
                ('resource_release.resource_index_meta', 'schema', 'tfi.resource-index-store.v1'),
            ):
                row = connection.execute(f'SELECT value FROM {table} WHERE key=%s', (key,)).fetchone()
                if not row or str(row[0]) != expected:
                    raise ValueError('A data domain requires an undeclared migration: ' + table)

    def preflight(self, manifest_path):
        if sys.platform != 'win32':
            raise ValueError('Application upgrade currently requires native Windows')
        if pending(self.application):
            raise ValueError('Interrupted application upgrade requires application-recover')
        manifest_path = Path(manifest_path).resolve()
        if manifest_path.is_relative_to(self.application):
            raise ValueError('Upgrade engineering must be outside the application')
        records = states(self.application)
        manifest = self.api.read_json(manifest_path)
        declaration = manifest.get('upgrade', {}).get('application', {})
        if declaration.get('schema') != 'tfi.windows-application-upgrade.v1':
            raise ValueError('Engineering does not declare an application migration')
        if declaration.get('lineage_schema') != 'tfi.lineage-data.v65':
            raise ValueError('Unsupported application schema target')
        owner = 'code/scripts/tfi_release_schema.py'
        if (declaration.get('migration_owner') != owner
                or declaration.get('migration_sha256') != self.api.digest(manifest_path.parent / owner)):
            raise ValueError('Migration owner checksum does not match the declaration')
        origins = declaration.get('from_manifest_sha256', [])
        if not origins or any(not re.fullmatch('[0-9a-f]{64}', str(v)) for v in origins):
            raise ValueError('Application upgrade requires exact supported origins')
        for state in records:
            target = state['target']
            if state['manifest_sha256'] not in origins:
                raise ValueError('An instance manifest is not a declared upgrade origin')
            if target.get('windows_service_mode') != 'user':
                raise ValueError('This application migration supports user services; SCM requires its own validated transition')
            self.api.validate_engineering(manifest_path, {**target, 'run_id': manifest['run_id'],
                'supervisor_resident_id': manifest.get('supervisor_resident_id', '')})
            self.api.platform_preflight(manifest)
            self.api.check_available(target, existing=True)
            providers.owned_services(target, self.api.unit_directory())
            if (database_root(target) / 'PG_VERSION').read_text().strip() != '16':
                raise ValueError('This migration preserves PostgreSQL 16 only')
            if self.api.observed_status(state)['status'] != 'installed':
                raise ValueError('Every application instance must be healthy before upgrading')
        old = self.api.read_json(self.application / 'windows-package.json')
        new = declaration['package']
        for key in ('target', 'python', 'bundled_native_dependencies'):
            if not old.get(key) or old[key] != new.get(key):
                raise ValueError('Base Python/native dependency replacement is outside this migration')
        # Live Provider/SQLite files may be read-locked on Windows. Preflight
        # needs storage/link metadata only; byte-level verification belongs to
        # the stopped snapshot after every owned process has exited.
        observed = snapshot.inventory(self.application, content=False)
        for state in records:
            db = database_root(state['target']).relative_to(self.application).as_posix() + '/'
            if any('link' in row for name, row in observed.items() if name.startswith(db)):
                raise ValueError('Database storage contains links')
        needed = 3 * sum(row.get('size', 0) for row in observed.values()) + 1024**3
        if shutil.disk_usage(self.application).free < needed:
            raise ValueError('Insufficient free space for backup, failed generation and staging')
        from scripts import tfi_application_work as work
        checks = []
        waiting = []
        for state in records:
            waiting.append(work.inspect(self.api, state['target']))
            self.database_check(state)
            checks.append(self.schema(state))
        return records, manifest, {'schemas': checks, 'waiting_work_candidates': waiting,
            'offline_proof_required': True, 'required_free_bytes': needed,
            'observed_metadata_sha256': __import__('hashlib').sha256(
                json.dumps(observed, sort_keys=True).encode()).hexdigest()}

    def stop(self, records, *, keep_database=False):
        control = self.api.service_controller()
        for state in records:
            target = state['target']
            self.api.check_available(target, existing=True)
            plan = providers.owned_services(target, self.api.unit_directory())
            names = [self.api.unit_name(target, c) for c in reversed(self.api.COMPONENTS)
                     if not (keep_database and c == 'postgres')]
            for name in names:
                control.stop(name)
            providers.stop_services(plan, control)
            if any((s := control.status(n))['pid'] or s['active'] not in {'inactive', 'failed'} for n in names):
                raise RuntimeError('Owned services remain running; backup/restore refused')

    def start(self, records):
        from scripts.tfi_release_upgrade import start_and_check
        control = self.api.service_controller()
        for state in records:
            start_and_check(self.api, state)
            providers.start_services(providers.owned_services(state['target'], self.api.unit_directory()), control)

    def verify_work(self, records, expected):
        """Only PostgreSQL runs while the immutable waiting work is checked."""
        control = self.api.service_controller()
        for state in records:
            target = state['target']
            control.start(self.api.unit_name(target, 'postgres'))
            self.api.wait_database(target)
        self.verify_work_in_runtime(records, expected)

    def verify_work_in_runtime(self, records, expected):
        # The recovery parent deliberately stays in base Python: restoring a
        # venv while running its executable/DLLs would lock those files on
        # Windows. Resolve the runtime *after* restoration and keep all database
        # imports and locks in this short-lived child, before any admission.
        target = records[0]['target']
        code = Path(__file__).resolve().parents[1]
        env = self.api.clean_environment(target)
        env.pop('PORTAL_DATABASE_NAMESPACE', None)
        env['PYTHONPATH'] = str(code)
        result = subprocess.run(
            [self.api.runtime_python(target), '-B', '-m', 'scripts.tfi_application_freeze'],
            input=json.dumps({'records': records, 'expected': expected}),
            env=env, cwd=code, text=True, encoding='utf-8', capture_output=True, timeout=120)
        if result.returncode:
            ensure_private_directory(self.control)
            atomic_write_private_bytes(self.control / 'work-error.log', result.stderr.encode('utf-8'))
            raise ValueError('Retained work verification failed; inspect private work-error.log')
        if json.loads(result.stdout) != {'status': 'verified', 'instances': len(records)}:
            raise ValueError('Unexpected retained work verification result; service admission refused')

    def resume(self, journal, records, outcome):
        if journal.get('database_admission') and not journal.get('admission_restored'):
            self.restore_admission_in_runtime(records, journal['database_admission'])
            self.save(journal, journal['phase'], admission_restored=True)
        # Once a consumer can see retained inputs, a new business write is
        # possible. Never restore a stale cold database after this boundary.
        self.save(journal, 'resuming', resume_states=records, resume_outcome=outcome)
        self.start(records)
        if outcome == 'complete':
            # Dependency preparation persisted "prepared". Only successful
            # startup/health may finalize the new installation generation;
            # restoring an old pair must retain its original metadata.
            for state in records:
                self.api.save_stage(state, 'application_upgrade_health', 'installed',
                                    upgrade_id=journal['id'])
        self.save(journal, outcome)

    def restore_admission_in_runtime(self, records, entries):
        # A pre-backup crash can leave database admission closed. Recovery
        # still runs in base Python, so the unchanged target runtime performs
        # this catalog-only operation before any service admission.
        control = self.api.service_controller()
        for state in records:
            control.start(self.api.unit_name(state['target'], 'postgres'))
        target = records[0]['target']
        code = Path(__file__).resolve().parents[1]
        env = self.api.clean_environment(target)
        env.pop('PORTAL_DATABASE_NAMESPACE', None)
        env['PYTHONPATH'] = str(code)
        result = subprocess.run(
            [self.api.runtime_python(target), '-B', '-m', 'scripts.tfi_application_admission'],
            input=json.dumps({'records': records, 'entries': entries}), env=env, cwd=code,
            text=True, encoding='utf-8', capture_output=True, timeout=120)
        if result.returncode:
            ensure_private_directory(self.control)
            atomic_write_private_bytes(self.control / 'admission-error.log', result.stderr.encode('utf-8'))
            raise ValueError('Database admission recovery failed; inspect private admission-error.log')
        if json.loads(result.stdout) != {'status': 'restored', 'instances': len(records)}:
            raise ValueError('Unexpected database admission recovery result; service admission refused')

    def detach_codex(self, records):
        """Retire stopped account definitions before changing the runtime pin.

        Definitions live inside the cold application snapshot. An interrupted
        switch can therefore restore them without interpreting a stale Python
        path against the new installation state.
        """
        definitions = []
        for state in records:
            plan = providers.owned_services(state['target'], self.api.unit_directory())
            for name, control in (plan or {}).get('controllers', {}).items():
                value = control.definition(name)
                if not value or value.get('component') != 'codex':
                    raise ValueError('Unexpected account service definition')
                path = control.directory / (name + '.json')
                if not path.resolve().is_relative_to(self.application):
                    raise ValueError('Account definition is outside the application snapshot')
                status = control.status(name)
                if status['pid'] or status['active'] not in {'inactive', 'failed'}:
                    raise ValueError('Account service must stop before runtime replacement')
                definitions.append((path, value))
        for path, value in definitions:
            if path.is_symlink() or json.loads(path.read_text(encoding='utf-8')) != value:
                raise ValueError('Account definition changed while stopping')
            path.unlink()
        return definitions

    def attach_codex(self, definitions, state):
        from scripts.tfi_windows_user_services import validate_definition
        for path, original in definitions:
            if Path(original['root']) != Path(state['target']['install_root']):
                continue
            value = json.loads(json.dumps(original))
            value['python'] = self.api.runtime_python(state['target'])
            value['command'][0] = value['python']
            validate_definition(value, value['name'])
            if path.exists() or path.is_symlink():
                raise ValueError('Account definition appeared during runtime replacement')
            # Atomic JSON replacement: recovery never encounters half a service
            # definition if the external updater exits here.
            self.api.write_json(path, value)

    def locks(self, records):
        locks = ExitStack()
        try:
            from scripts.tfi_windows_launcher import launcher_lock
            locks.enter_context(launcher_lock(self.application))
            locks.enter_context(self.api.installation_lock(self.control))
            for state in records:
                locks.enter_context(self.api.installation_lock(Path(state['target']['install_root'])))
            return locks
        except BaseException:
            locks.close()
            raise

    def paths(self, journal):
        if journal.get('application') != str(self.application) or not re.fullmatch('upgrade-[0-9a-f]{32}', journal.get('id', '')):
            raise ValueError('Foreign application recovery journal')
        parent = self.control / journal['id']
        if parent.resolve() != parent:
            raise ValueError('Redirected backup path')
        return parent

    def restore(self, journal):
        parent = self.paths(journal)
        backup = parent / 'backup'
        expected = self.api.read_json(parent / 'inventory.json')
        snapshot.verify_backup(backup, expected)
        for state in journal['states']:
            relative = Path(state['target']['install_root']).relative_to(self.application)
            # Materialize only the canonical configuration pair for its own
            # loader. Large/deep business paths stay in the flat backup store.
            with tempfile.TemporaryDirectory(dir=parent) as temporary:
                root = Path(temporary)
                for name in ('installation.json', 'config/target.json'):
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with snapshot.create_private_file(path) as stream:
                        stream.write(snapshot.backup_bytes(backup, (relative / name).as_posix(), expected))
                original = read_installation(root)
            if original['target'] != state['target'] or original['manifest_sha256'] != state['manifest_sha256']:
                raise ValueError('Backup installation ownership mismatch')
        self.stop(journal['states'])
        failed = parent / ('failed-' + uuid.uuid4().hex)
        snapshot.create_backup(self.application, failed)
        self.save(journal, 'restoring', failed_generation=str(failed))
        snapshot.restore(backup, self.application, expected, packed=True)
        if 'waiting_work' in journal:
            self.verify_work(journal['states'], journal['waiting_work'])
        self.resume(journal, journal['states'], 'restored')
        return {'status': 'restored', 'backup': str(backup), 'failed_generation': str(failed)}

    def recover(self):
        require_desktop()
        journal = self.api.read_json(self.journal_path)
        parent = self.paths(journal)
        if journal['phase'] in TERMINAL:
            return {'status': journal['phase']}
        current = states(self.application)
        if [s['target'] for s in current] != [s['target'] for s in journal['states']]:
            raise ValueError('Installation ownership changed since upgrade')
        with self.locks(current):
            if journal['phase'] == 'resuming':
                for state, expected in zip(current, journal['resume_states'], strict=True):
                    if any(state.get(key) != expected.get(key) for key in
                           ('target', 'manifest_sha256', 'version_run_id')):
                        raise ValueError('Resuming installation version changed')
                self.resume(journal, journal['resume_states'], journal['resume_outcome'])
                return {'status': journal['resume_outcome']}
            if journal['phase'] in {'quiescing', 'backing_up'}:
                # Business rows/code are unchanged; a closed connection gate
                # must be restored from its journal before starting services.
                self.resume(journal, current, 'prebackup_failed')
                return {'status': 'prebackup_failed'}
            return self.restore(journal)

    def upgrade(self, manifest_path):
        records, manifest, checks = self.preflight(manifest_path)
        require_desktop()
        manifest_path = Path(manifest_path).resolve()
        bundle = manifest_path.parent
        ensure_private_directory(self.control)
        journal = {'id': 'upgrade-' + uuid.uuid4().hex, 'application': str(self.application),
            'states': records, 'manifest_sha256': self.api.digest(manifest_path),
            'run_id': manifest['run_id'], 'preflight': checks}
        parent = self.paths(journal)
        parent.mkdir(mode=0o700)
        with self.locks(records):
            if states(self.application) != records or pending(self.application):
                raise ValueError('Installation changed while acquiring upgrade locks')
            from scripts import tfi_application_work as work
            from scripts import tfi_application_admission as admission
            try:
                with barrier.freeze(self.api, records) as connections:
                    proofs = [work.inspect(self.api, state['target'], connection=connection)
                              for state, connection in zip(records, connections, strict=True)]
                    # Close connection admission before capturing ownership:
                    # serial service shutdown must not continually introduce
                    # new clients after the captured set. Startup races remain
                    # subject to the unchanged unknown-client guard.
                    entries = admission.prepare(self.api, records, connections)
                    self.save(journal, 'quiescing', waiting_work=proofs,
                              database_admission=entries, admission_restored=False)
                    admission.close(self.api, records, entries)
                    with clients.owned_clients(self.api, records, connections) as clear_disconnected:
                        self.stop(records, keep_database=True)
                        cleaned = clear_disconnected() if clear_disconnected else []
                        for state, proof, connection in zip(records, proofs, connections, strict=True):
                            barrier.assert_no_clients(connection)
                            if work.inspect(self.api, state['target'], connection=connection) != proof:
                                raise ValueError('Waiting work changed during service shutdown')
                        self.save(journal, 'quiescing', disconnected_sql_waits=cleaned)
                    admission.restore(self.api, records, entries)
                    # Maintenance connections have closed; all consumers are
                    # still stopped and every write barrier remains held.
                    for state, proof, connection in zip(records, proofs, connections, strict=True):
                        barrier.assert_no_clients(connection)
                        if work.inspect(self.api, state['target'], connection=connection) != proof:
                            raise ValueError('Waiting work changed while restoring database admission')
                    self.save(journal, 'quiescing', admission_restored=True)
                    self.stop(records)
                self.save(journal, 'backing_up')
                inventory = snapshot.create_backup(self.application, parent / 'backup')
                self.api.write_json(parent / 'inventory.json', inventory)
                self.save(journal, 'backup_ready')
            except Exception as exc:
                if journal.get('phase') in {'quiescing', 'backing_up'}:
                    if isinstance(exc, clients.ClientProofError):
                        # Preserve the refusing predicates before automatic
                        # recovery changes the processes and database sessions.
                        try:
                            self.save(journal, journal['phase'], client_failure=exc.diagnostic)
                        finally:
                            self.resume(journal, records, 'prebackup_failed')
                        raise
                    self.resume(journal, records, 'prebackup_failed')
                raise
            try:
                self.save(journal, 'switching')
                accounts = self.detach_codex(records)
                new_code = self.api.engineering_path(bundle, manifest['code']['path'])
                snapshot.copy_tree(new_code, parent / 'code')
                (self.application / 'code').rename(parent / 'previous-code')
                (parent / 'code').rename(self.application / 'code')
                declaration = manifest['upgrade']['application']
                wheels = self.api.engineering_path(bundle, declaration['wheelhouse'])
                destination = self.application / 'dependencies/python-wheels'
                snapshot.copy_tree(wheels, parent / 'python-wheels')
                destination.rename(parent / 'previous-python-wheels')
                (parent / 'python-wheels').rename(destination)
                updated = []
                for state in records:
                    state = json.loads(json.dumps(state))
                    state.update(manifest_sha256=journal['manifest_sha256'], version_run_id=manifest['run_id'])
                    self.api.write_json(Path(state['target']['install_root']) / self.api.STATE_FILE, state)
                    self.api.prepare_dependencies(state, manifest)
                    self.attach_codex(accounts, state)
                    self.api.write_json(Path(state['target']['install_root']) / 'runtime/engineering-manifest.json', manifest)
                    updated.append(state)
                self.save(journal, 'migrating')
                control = self.api.service_controller()
                for state in updated:
                    target = state['target']
                    control.start(self.api.unit_name(target, 'postgres'))
                    self.api.wait_database(target)
                    self.schema(state, apply=True)
                    control.stop(self.api.unit_name(target, 'postgres'))
                self.api.write_json(self.application / 'windows-package.json', declaration['package'])
                self.verify_work(updated, journal['waiting_work'])
                self.save(journal, 'starting')
                self.resume(journal, updated, 'complete')
                return {'status': 'installed', 'run_id': manifest['run_id'],
                    'manifest_sha256': journal['manifest_sha256'], 'backup': str(parent / 'backup')}
            except Exception:
                if journal.get('phase') in {'resuming', 'complete'}:
                    # Already admitted work may have progressed. Retry startup
                    # through application-recover, never roll back its data.
                    # save() sets the in-memory phase before publishing it, so
                    # a failed final journal write has the same boundary.
                    raise
                # The saved stopped pair, never a seed or reverse SQL, is the
                # authority for recovery. A failed generation is retained too.
                self.save(journal, 'recovery_required')
                self.restore(journal)
                raise


def run(api, command, root, manifest_path=None):
    upgrade = ApplicationUpgrade(api, root)
    if command == 'application-recover':
        return upgrade.recover()
    if not manifest_path:
        raise ValueError('An immutable engineering manifest is required')
    if command == 'application-check':
        records, manifest, checks = upgrade.preflight(manifest_path)
        return {'status': 'checked', 'read_only': True, 'instances': len(records),
                'run_id': manifest['run_id'], **checks}
    return upgrade.upgrade(manifest_path)
