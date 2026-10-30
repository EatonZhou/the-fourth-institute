"""Prove disconnected Windows service SQL before waking a maintenance lock wait.

No business work is cancelled. The write barrier and complete waiting-work proof
are prerequisites. Unknown/live/new backends keep the strict zero-client guard.
"""
from contextlib import contextmanager
import ctypes
import ipaddress
import json
import socket
import struct
import sys
import time


class ProcessAbsentError(OSError):
    """Only OpenProcess ERROR_INVALID_PARAMETER for a validated service PID."""


class WindowsProcesses:
    """Pin process objects through shutdown; never infer identity from a PID alone."""
    def __init__(self):
        from ctypes import wintypes as w
        self.w = w
        self.handles = {}
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'CloseHandle': ([w.HANDLE], w.BOOL),
            'WaitForSingleObject': ([w.HANDLE, w.DWORD], w.DWORD),
            'GetProcessTimes': ([w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4, w.BOOL),
            'CreateToolhelp32Snapshot': ([w.DWORD, w.DWORD], w.HANDLE),
        }
        for name, (args, result) in signatures.items():
            method = getattr(self.api, name)
            method.argtypes, method.restype = args, result

    def close(self):
        for handle, _ in self.handles.values():
            self.api.CloseHandle(handle)
        self.handles.clear()

    def pin(self, pid, *, allow_exited=False):
        if pid not in self.handles:
            handle = self.api.OpenProcess(0x1000 | 0x100000, False, pid)
            if not handle:
                error = ctypes.get_last_error()
                if error == 87:
                    raise ProcessAbsentError('Process is unavailable to OpenProcess')
                raise ctypes.WinError(error)
            try:
                times = [self.w.FILETIME() for _ in range(4)]
                if not self.api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handles[pid] = (handle, (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
            except BaseException:
                self.api.CloseHandle(handle)
                raise
        if self.exited(pid) and not allow_exited:
            raise ValueError('Client process exited during ownership capture')
        return self.handles[pid][1]

    def exited(self, pid):
        status = self.api.WaitForSingleObject(self.handles[pid][0], 0)
        if status not in (0, 258):
            raise ctypes.WinError(ctypes.get_last_error())
        return status == 0

    def parents(self):
        w = self.w
        class Entry(ctypes.Structure):
            _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD),
                ('heap', ctypes.c_size_t), ('module', w.DWORD), ('threads', w.DWORD),
                ('parent', w.DWORD), ('priority', w.LONG), ('flags', w.DWORD), ('name', w.WCHAR * 260)]
        for name in ('Process32FirstW', 'Process32NextW'):
            method = getattr(self.api, name)
            method.argtypes, method.restype = [w.HANDLE, ctypes.POINTER(Entry)], w.BOOL
        handle = self.api.CreateToolhelp32Snapshot(2, 0)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            result = {}
            item = Entry(); item.size = ctypes.sizeof(item)
            more = self.api.Process32FirstW(handle, ctypes.byref(item))
            while more:
                result[item.pid] = item.parent
                more = self.api.Process32NextW(handle, ctypes.byref(item))
            if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise ctypes.WinError(ctypes.get_last_error())
            return result
        finally:
            self.api.CloseHandle(handle)

    def tcp(self):
        # Enumerate both families, including every closing state. Cleanup itself
        # accepts only locally proven IPv4 backends; no missing family is "gone".
        # https://learn.microsoft.com/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable
        w = self.w
        method = ctypes.WinDLL('iphlpapi', use_last_error=True).GetExtendedTcpTable
        method.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.DWORD), w.BOOL, w.ULONG, ctypes.c_int, w.ULONG]
        method.restype = w.DWORD
        rows = []
        for family in (socket.AF_INET, socket.AF_INET6):
            size = w.DWORD(0)
            buffer = None
            for _ in range(4):
                result = method(buffer, ctypes.byref(size), False, family, 5, 0)
                if result == 0:
                    rows.extend(decode_tcp(buffer.raw[:size.value], family))
                    break
                if result != 122:
                    raise ctypes.WinError(result)
                buffer = ctypes.create_string_buffer(size.value)
            else:
                raise RuntimeError('TCP ownership table changed repeatedly')
        return rows


def decode_tcp(data, family=socket.AF_INET):
    count, = struct.unpack_from('<I', data)
    width = 24 if family == socket.AF_INET else 56
    if len(data) < 4 + count * width:
        raise ValueError('Incomplete TCP ownership table')
    result = []
    for offset in range(4, 4 + count * width, width):
        if family == socket.AF_INET:
            state, local, lport, remote, rport, pid = struct.unpack_from('<6I', data, offset)
            local, remote = (socket.inet_ntoa(struct.pack('<I', value)) for value in (local, remote))
        else:
            local, lscope, lport, remote, rscope, rport, state, pid = struct.unpack_from('<16sII16sIIII', data, offset)
            def address(value, scope):
                ip = ipaddress.IPv6Address(value)
                return str(ip.ipv4_mapped or ip) + (f'%{scope}' if scope else '')
            local, remote = address(local, lscope), address(remote, rscope)
        result.append((local, socket.ntohs(lport & 0xffff), remote, socket.ntohs(rport & 0xffff), pid, state))
    return result


FIELDS = ('pid', 'backend_start', 'datid', 'usesysid', 'client_addr', 'client_port',
          'state', 'wait_event_type', 'wait_event', 'blockers', 'query_start', 'xact_start')
IDENTITY = FIELDS[:6]


def clients(connection):
    connection.execute('SELECT pg_stat_clear_snapshot()')
    rows = connection.execute("""SELECT pid,backend_start::text,datid,usesysid,
        host(client_addr),client_port,state,wait_event_type,wait_event,pg_blocking_pids(pid),
        query_start::text,xact_start::text
        FROM pg_stat_activity WHERE datname=current_database()
        AND backend_type='client backend' AND pid<>pg_backend_pid() ORDER BY pid""").fetchall()
    return [dict(zip(FIELDS, row, strict=True)) for row in rows]


def ancestry(processes, pid, roots):
    parents = processes.parents()
    chain = []
    while pid and pid not in {p for p, _ in chain} and len(chain) < 32:
        stamp = processes.pin(pid)
        if chain and stamp > chain[-1][1]:
            raise ValueError('Process parent identity was recycled')
        chain.append((pid, stamp))
        if pid in roots:
            expected, supervisor, supervisor_stamp = roots[pid]
            if (stamp != expected or parents.get(pid) != supervisor
                    or processes.pin(supervisor) != supervisor_stamp or supervisor_stamp > stamp):
                raise ValueError('Service process identity changed')
            chain.append((supervisor, supervisor_stamp))
            current = processes.parents()
            if any(current.get(child) != parent for (child, _), (parent, _) in zip(chain, chain[1:])):
                raise ValueError('Process ancestry changed during ownership capture')
            return chain
        pid = parents.get(pid)
    raise ValueError('Database client is not a proven owned service descendant')


def capture(connection, processes, roots):
    assert_no_foreign_clients(connection)
    server, port, barrier_pid = connection.execute(
        'SELECT host(inet_server_addr()),inet_server_port(),pg_backend_pid()').fetchone()
    if not server or not ipaddress.ip_address(server).is_loopback or ':' in server:
        raise ValueError('Disconnected-client cleanup requires local IPv4 PostgreSQL')
    captured = {}
    deadline = time.monotonic() + 5
    sampling = {'rounds': 0, 'departed': 0, 'deferred': 0}

    def identities():
        return {r['pid']: {k: r[k] for k in IDENTITY} for r in clients(connection)}

    def require_budget():
        # OS calls cannot be interrupted here, but late results never authorize
        # shutdown. The same budget covers every round and every client.
        if time.monotonic() >= deadline:
            raise ValueError('Database client ownership did not converge during bounded capture')

    # SHARE locks freeze writes, not connection creation/closure. Accumulate
    # individually proven identities instead of requiring one immutable online
    # client set throughout all OS reads. This never authorizes an unproven row.
    for _ in range(64):
        require_budget()
        sampling['rounds'] += 1
        before = identities()
        captured = {pid: e for pid, e in captured.items() if before.get(pid) == e['identity']}
        tcp = processes.tcp()
        candidates = {}
        for pid, identity in before.items():
            require_budget()
            if pid in captured:
                continue
            endpoint = (identity['client_addr'], identity['client_port'], server, port)
            try:
                if not identity['client_addr'] or not ipaddress.ip_address(identity['client_addr']).is_loopback:
                    raise ValueError('Database client is not local')
                matches = [r for r in tcp if r[:4] == endpoint and r[5] == 5]
                if not matches:
                    # A closing socket can precede removal from pg_stat_activity.
                    # Wait for fresh evidence or actual disappearance, never infer ownership.
                    sampling['deferred'] += 1
                    continue
                if len(matches) != 1:
                    raise ValueError('Database client TCP ownership is ambiguous')
                tcp_row = matches[0]
                chain = ancestry(processes, tcp_row[4], roots)
                processes.pin(pid)  # Pin the PG process as well as its client chain.
                candidates[pid] = ({'identity': identity, 'endpoint': endpoint, 'chain': chain}, tcp_row)
            except (OSError, ValueError):
                # An error for a still-present identity remains a hard refusal.
                # A departed identity grants no cancellation authority.
                if identities().get(pid) == identity:
                    raise
                sampling['departed'] += 1
        tcp_after = processes.tcp()
        assert_no_foreign_clients(connection)
        after = identities()
        for pid, (evidence, tcp_row) in candidates.items():
            if after.get(pid) != evidence['identity']:
                sampling['departed'] += 1
                continue
            matches = [r for r in tcp_after if r[:4] == evidence['endpoint'] and r[5] == 5]
            if len(matches) > 1:
                raise ValueError('Database client TCP ownership is ambiguous')
            if (matches == [tcp_row] and not processes.exited(pid)
                    and not any(processes.exited(p) for p, _ in evidence['chain'])):
                captured[pid] = evidence
            else:
                sampling['deferred'] += 1
        captured = {pid: e for pid, e in captured.items() if after.get(pid) == e['identity']}
        require_budget()
        if after == {pid: e['identity'] for pid, e in captured.items()}:
            return {'barrier_pid': barrier_pid, 'clients': captured, 'sampling': sampling}
        time.sleep(.05)
    raise ValueError('Database client ownership did not converge during bounded capture')


def assert_no_foreign_clients(connection):
    # Stopping this PostgreSQL instance stops its entire cluster. Clients in
    # another database are never candidates for maintenance SQL cancellation.
    deadline = time.monotonic() + 2
    while True:
        connection.execute('SELECT pg_stat_clear_snapshot()')
        count = connection.execute("""SELECT count(*) FROM pg_stat_activity
            WHERE backend_type='client backend' AND datname IS DISTINCT FROM current_database()
            AND pid<>pg_backend_pid()""").fetchone()[0]
        if not count:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Other database has client sessions; cluster shutdown refused')
        time.sleep(.05)


def lock_requests(connection, pid, blocker):
    rows = connection.execute("""SELECT w.database,w.relation,w.mode,
        EXISTS(SELECT 1 FROM pg_locks b WHERE b.pid=%s AND b.granted
            AND b.locktype='relation' AND b.mode='ShareLock'
            AND b.database=w.database AND b.relation=w.relation)
        FROM pg_locks w WHERE w.pid=%s AND NOT w.granted
        ORDER BY w.database,w.relation,w.mode""", (blocker, pid)).fetchall()
    conflicts = {'RowExclusiveLock', 'ShareUpdateExclusiveLock', 'ShareRowExclusiveLock',
                 'ExclusiveLock', 'AccessExclusiveLock'}
    if not rows or any(not db or not relation or mode not in conflicts or not held
                       for db, relation, mode, held in rows):
        raise RuntimeError('Residual SQL does not wait on the held application relation barrier')
    return [f'{db}:{relation}:{mode}' for db, relation, mode, _ in rows]


class ClientProofError(RuntimeError):
    """A non-sensitive, per-predicate refusal receipt; never an authorization."""
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        reasons = sorted({reason for item in diagnostic.get('clients', []) + diagnostic.get('service_roots', [])
                          for reason in item['reasons']})
        message = ('Owned service root could not be classified; ' if diagnostic.get('stage') == 'service_roots'
                   else 'Residual database client is not a proven disconnected service wait; ')
        super().__init__(message
                         + ','.join(reasons))


def residual_checks(row, processes, known, barrier_pid, tcp):
    """No PIDs, addresses, names, SQL text, account values or timestamp values."""
    checks = {'captured': known is not None,
        'identity_matches': bool(known and all(row[k] == known['identity'][k] for k in IDENTITY)),
        'backend_alive': None, 'client_chain_exited': None, 'tcp_absent': None,
        'active': row['state'] == 'active', 'lock_wait': row['wait_event_type'] == 'Lock',
        'relation_wait': row['wait_event'] == 'relation',
        'query_timestamp_present': bool(row['query_start']), 'transaction_timestamp_present': bool(row['xact_start']),
        'sole_barrier': row['blockers'] == [barrier_pid]}
    observations = {name: ('not_checked' if value is None else 'observed')
                    for name, value in checks.items()}
    if known and checks['identity_matches']:
        try:
            checks['backend_alive'] = not processes.exited(row['pid'])
            observations['backend_alive'] = 'observed'
        except (OSError, ValueError, KeyError):
            observations['backend_alive'] = 'read_error'
        try:
            checks['client_chain_exited'] = all(processes.exited(pid) for pid, _ in known['chain'])
            observations['client_chain_exited'] = 'observed'
        except (OSError, ValueError, KeyError):
            observations['client_chain_exited'] = 'read_error'
        if tcp is not None:
            checks['tcp_absent'] = not any(r[:4] == known['endpoint'] for r in tcp)
            observations['tcp_absent'] = 'observed'
        else:
            observations['tcp_absent'] = 'read_error'
    return checks, observations


def plan_disconnected(connection, processes, evidence):
    """Validate all candidates without issuing any signal."""
    assert_no_foreign_clients(connection)
    residual = clients(connection)
    locks = {}
    try:
        tcp = processes.tcp() if residual else []
    except (OSError, ValueError):
        tcp = None
    diagnostics = []
    for ordinal, row in enumerate(residual, 1):
        known = evidence['clients'].get(row['pid'])
        checks, observations = residual_checks(row, processes, known, evidence['barrier_pid'], tcp)
        observations['barrier_relation_locks'] = 'not_checked'
        if all(value is True for value in checks.values()):
            try:
                locks[row['pid']] = lock_requests(connection, row['pid'], evidence['barrier_pid'])
                checks['barrier_relation_locks'] = True
                observations['barrier_relation_locks'] = 'observed'
            except RuntimeError:
                checks['barrier_relation_locks'] = False
                observations['barrier_relation_locks'] = 'observed'
            except Exception:
                checks['barrier_relation_locks'] = None
                observations['barrier_relation_locks'] = 'read_error'
        diagnostics.append({'ordinal': ordinal, 'checks': checks, 'observations': observations,
                            'reasons': [name for name, passed in checks.items() if passed is not True]})
    if any(item['reasons'] for item in diagnostics):
        # Observe disappearance separately; it never turns a failed check into
        # cancellation authority. The receipt describes the refusing snapshot.
        try:
            fresh = {r['pid']: {k: r[k] for k in IDENTITY} for r in clients(connection)}
        except Exception:
            fresh = None
        for row, item in zip(residual, diagnostics, strict=True):
            item['fresh_identity_present'] = (None if fresh is None else
                fresh.get(row['pid']) == {k: row[k] for k in IDENTITY})
            item['fresh_observation'] = 'read_error' if fresh is None else 'observed_after_refusal'
        raise ClientProofError({'schema': 'tfi.application-client-proof.v1', 'stage': 'plan_disconnected',
            'captured_count': len(evidence['clients']), 'observed_count': len(residual), 'clients': diagnostics})
    return residual, locks


def clear_disconnected(connection, processes, evidence, plan=None):
    """One query-cancel signal only for pinned, dead-client, own-barrier waits."""
    residual, locks = plan_disconnected(connection, processes, evidence)
    if plan is not None:
        original = {row['pid']: row for row in plan[0]}
        if any(original.get(row['pid']) != row or plan[1].get(row['pid']) != locks[row['pid']]
               for row in residual):
            raise RuntimeError('Disconnected SQL changed after all-instance validation')
    signals = 0
    for row in residual:
        known = evidence['clients'][row['pid']]
        connection.execute('SELECT pg_stat_clear_snapshot()')
        # Server PID remains pinned; identity and sole blocking lock are checked
        # again in the signalling statement. Never terminate a session or write
        # business tables. The caller must still prove zero clients afterwards.
        result = connection.execute("""SELECT pg_cancel_backend(pid) FROM pg_stat_activity s
            WHERE pid=%s AND backend_start::text=%s AND datid=%s AND usesysid=%s
            AND host(client_addr) IS NOT DISTINCT FROM %s AND client_port=%s AND datname=current_database()
            AND backend_type='client backend' AND state='active' AND wait_event_type='Lock'
            AND wait_event='relation' AND pg_blocking_pids(pid)=ARRAY[%s]::integer[]
            AND query_start::text=%s AND xact_start::text=%s
            AND ARRAY(SELECT w.database::text||':'||w.relation::text||':'||w.mode
                FROM pg_locks w WHERE w.pid=s.pid AND NOT w.granted
                ORDER BY w.database,w.relation,w.mode)=%s::text[]
            AND NOT EXISTS(SELECT 1 FROM pg_locks w WHERE w.pid=s.pid AND NOT w.granted
                AND NOT EXISTS(SELECT 1 FROM pg_locks b WHERE b.pid=%s AND b.granted
                    AND b.locktype='relation' AND b.mode='ShareLock'
                    AND b.database=w.database AND b.relation=w.relation))
            AND NOT EXISTS(SELECT 1 FROM pg_stat_activity other
                WHERE other.backend_type='client backend' AND other.pid<>pg_backend_pid()
                AND (other.datname IS DISTINCT FROM current_database()
                     OR other.pid<>ALL(%s::integer[])))""",
            (*[known['identity'][k] for k in IDENTITY], evidence['barrier_pid'],
             row['query_start'], row['xact_start'], locks[row['pid']], evidence['barrier_pid'],
             [item['pid'] for item in residual])).fetchall()
        if not result and not any(peer['pid'] == row['pid'] for peer in clients(connection)):
            continue  # The pinned original backend disappeared naturally.
        if result != [(True,)]:
            raise RuntimeError('Disconnected service SQL wait changed before cancellation')
        signals += 1
    return signals


def clear_all(connections, processes, evidence):
    # An unknown client in a later instance must refuse before any instance is
    # signalled. Each action then revalidates; final cluster-zero guards remain.
    plans = []
    for index, (c, e) in enumerate(zip(connections, evidence, strict=True)):
        try:
            plans.append(plan_disconnected(c, processes, e))
        except ClientProofError as exc:
            exc.diagnostic.update(stage='all_instance_plan', instance_ordinal=index + 1,
                instances=len(connections), validated_instances=index, signals_sent=0)
            raise
    results = []
    for index, (c, e, p) in enumerate(zip(connections, evidence, plans, strict=True)):
        try:
            results.append(clear_disconnected(c, processes, e, p))
        except ClientProofError as exc:
            # This error is raised by the pre-action revalidation, before any
            # signal in this instance. Earlier instances may have sent signals.
            exc.diagnostic.update(stage='action_revalidation', instance_ordinal=index + 1,
                instances=len(connections), signals_sent=sum(results))
            raise
    return results


def service_root(processes, read):
    """A stale record supplies no ownership authority, including for descendants.

    Only positive DWORD PIDs absent in both complete snapshots AND OpenProcess
    ERROR_INVALID_PARAMETER, or pinned matching exited objects, count as gone.
    Permissions, reuse, mixed live/dead pairs and changed records refuse.
    """
    diagnostic = {'classification': 'unknown', 'supervisor': 'not_checked',
                  'child': 'not_checked', 'record_stable': None, 'reasons': []}
    def refuse(reason):
        diagnostic['reasons'].append(reason)
        raise ClientProofError({'stage': 'service_roots', 'signals_sent': 0,
                                'clients': [], 'service_roots': [diagnostic]})
    try:
        raw = read()
        if raw is None:
            diagnostic['classification'] = 'no_record'
            return None, diagnostic
        state = json.loads(raw)
        if (not isinstance(state, dict)
                or set(state) != {'pid', 'created', 'child_pid', 'child_created'}
                or any(type(state[k]) is not int or not 0 < state[k] <= 0xffffffff
                       for k in ('pid', 'child_pid'))
                or any(type(state[k]) is not int or state[k] <= 0
                       for k in ('created', 'child_created'))
                or state['pid'] == state['child_pid']):
            refuse('invalid_record')
        before = processes.parents()
        for label, pid_key, stamp_key in [('supervisor', 'pid', 'created'),
                                          ('child', 'child_pid', 'child_created')]:
            pid = state[pid_key]
            try:
                stamp = processes.pin(pid, allow_exited=True)
            except ProcessAbsentError:
                diagnostic[label] = 'read_error'
                if pid in before:
                    refuse('process_read_error')
                diagnostic[label] = 'absent_candidate'
                continue
            except OSError:
                diagnostic[label] = 'read_error'
                refuse('process_read_error')
            if stamp != state[stamp_key]:
                diagnostic[label] = 'identity_mismatch'
                refuse('process_identity_changed')
            diagnostic[label] = 'exited' if processes.exited(pid) else 'live'
        after = processes.parents()
        diagnostic['record_stable'] = read() == raw
        if not diagnostic['record_stable']:
            refuse('record_changed')
        for label, key in [('supervisor', 'pid'), ('child', 'child_pid')]:
            if diagnostic[label] == 'absent_candidate':
                if state[key] in after:
                    refuse('process_snapshot_changed')
                diagnostic[label] = 'absent'
        pair = [diagnostic['supervisor'], diagnostic['child']]
        if all(value in {'absent', 'exited'} for value in pair):
            diagnostic['classification'] = 'stale_record'
            return None, diagnostic
        if pair != ['live', 'live']:
            refuse('partially_live_record')
        if (before.get(state['child_pid']) != state['pid']
                or after.get(state['child_pid']) != state['pid']
                or processes.exited(state['pid']) or processes.exited(state['child_pid'])):
            refuse('service_ancestry_changed')
        diagnostic['classification'] = 'live_root'
        return (state['child_pid'], (state['child_created'], state['pid'], state['created'])), diagnostic
    except ClientProofError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        refuse('root_read_error')


@contextmanager
def owned_clients(api, records, connections):
    """Scope is Windows user services; other configurations retain strict refusal."""
    if sys.platform != 'win32' or any(r['target'].get('windows_service_mode') != 'user' for r in records):
        yield None
        return
    from scripts import tfi_windows_user_services as services
    from scripts import tfi_release_provider_services as providers
    processes = WindowsProcesses()
    try:
        control = api.service_controller()
        roots = {}
        ordinal = 0
        for record in records:
            target = record['target']
            api.check_available(target, existing=True)
            plan = providers.owned_services(target, api.unit_directory()) or {}
            names = [api.unit_name(target, c) for c in api.COMPONENTS if c != 'postgres'] + plan.get('names', [])
            for name in names:
                ordinal += 1
                controller = plan.get('controllers', {}).get(name, control)
                try:
                    definition = controller.definition(name)
                    if definition is None:
                        continue
                    def read():
                        try:
                            return services.read_record(services.state_path(definition))
                        except FileNotFoundError:
                            return None
                    root, _ = service_root(processes, read)
                    if root is not None:
                        roots[root[0]] = root[1]
                except (OSError, ValueError, KeyError, TypeError, ClientProofError) as exc:
                    if isinstance(exc, ClientProofError):
                        exc.diagnostic['service_ordinal'] = ordinal
                        raise
                    raise ClientProofError({'stage': 'service_roots', 'signals_sent': 0,
                        'service_ordinal': ordinal, 'clients': [], 'service_roots': [
                            {'classification': 'unknown', 'reasons': ['service_record_read_error']}]}) from None
        evidence = [capture(c, processes, roots) for c in connections]
        yield lambda: clear_all(connections, processes, evidence)
    finally:
        processes.close()
