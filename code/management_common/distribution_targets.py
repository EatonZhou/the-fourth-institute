"""Single release target catalog; intent, implementation and verification differ."""
from __future__ import annotations

import platform
import re

TARGETS = ('linux', 'windows', 'macos')
_BASE = ('service_registration', 'postgresql_dependencies', 'local_ipc', 'filesystem_paths',
         'file_lock', 'background_jobs', 'process_tree', 'terminal', 'native_dependencies')


def identify_host():
    system = platform.system().lower()
    name = {'linux': 'linux', 'windows': 'windows', 'darwin': 'macos'}.get(system, system)
    architecture = platform.machine().lower()
    architecture = {'amd64': 'x86_64', 'arm64': 'aarch64'}.get(architecture, architecture)
    return {'platform': name, 'os_version': platform.platform(), 'architecture': architecture}


def normalize_targets(value):
    if not isinstance(value, list) or not value or any(not isinstance(v, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', v) for v in value):
        raise ValueError('target_platforms must be a nonempty list of platform identifiers')
    return list(dict.fromkeys(value))


def target_catalog():
    return {'schema': 'tfi.distribution-targets.v1', 'default_targets': ['linux'],
            'targets': [{'id': name, 'label': {'linux':'Linux','windows':'Windows','macos':'macOS'}[name],
                         'implementation': 'implemented',
                         'verification': 'not_run',
                         'gaps': resolve_targets({'target_platforms': [name]})['capability_gaps']} for name in TARGETS],
            'capabilities': list(_BASE)}


def resolve_targets(snapshot, features=()):
    legacy = 'target_platforms' not in snapshot
    targets = ['linux'] if legacy else normalize_targets(snapshot['target_platforms'])
    rows = []
    for target in targets:
        for feature in sorted(set(features) | {'portal_core'}):
            capabilities = _BASE if feature == 'portal_core' else ('service_registration', 'postgresql_dependencies', 'local_ipc', 'filesystem_paths')
            for capability in capabilities:
                # Implementation records available adapters. Verification belongs
                # to a specific OS/architecture/build and is never inferred here.
                reason = '' if target in TARGETS else '此目标平台尚未登记实现'
                rows.append({'feature': feature, 'platform': target, 'capability': capability,
                             'implementation': ('unsupported' if target not in TARGETS else 'incomplete') if reason else 'implemented', 'verification': 'not_run',
                             'reason': reason, 'os_version': None, 'architecture': None,
                             'engineering_sha256': None, 'verified_at': None, 'evidence': []})
    gaps = [row for row in rows if row['implementation'] != 'implemented']
    return {'selected_targets': targets, 'target_resolution': 'legacy-effective-linux' if legacy else 'explicit',
            'support_matrix': rows, 'capability_gaps': gaps, 'installable': not gaps,
            'architecture_constraints': {target: ['x86_64', 'aarch64'] if target in TARGETS else [] for target in targets},
            'dependency_requirements': [{'platform': target, 'names': ['Python', 'PostgreSQL', 'NATS'],
                                        'verification': 'not_run', 'reason': '安装器按本次目标参数探测实际可执行文件'} for target in targets]}


def preflight_manifest(manifest, *, host=None):
    """Pure preflight, before filesystem/service/DB effects. No platform imports."""
    actual = dict(host or identify_host())
    resolution = resolve_targets({'target_platforms': manifest['selected_targets']} if 'selected_targets' in manifest else {}, manifest.get('features', []))
    reasons = []
    if manifest.get('installable') is False:
        reasons.append('此工程仍有未完成的目标平台能力')
    if actual['platform'] not in resolution['selected_targets']:
        reasons.append('实际平台不在此工程所选目标中')
    # Recheck current catalog as well as the artifact claims; a forged matrix
    # cannot promote an incomplete implementation to an installable target.
    gaps = [row for row in resolution['capability_gaps'] if row['platform'] == actual['platform']]
    gaps += [row for row in manifest.get('support_matrix', []) if row.get('platform') == actual['platform'] and row.get('implementation') != 'implemented']
    if gaps:
        reasons.append('此平台尚有未完成的安装能力')
    if actual.get('architecture') not in {'x86_64', 'aarch64'}:
        reasons.append('此架构尚无安装能力声明')
    constraints = manifest.get('architecture_constraints', resolution['architecture_constraints'])
    if not isinstance(constraints, dict) or any(not isinstance(constraints.get(target), list) or not constraints[target]
            or any(a not in {'x86_64','aarch64'} for a in constraints[target]) for target in resolution['selected_targets']):
        reasons.append('工程架构约束缺失或无效')
    elif actual.get('architecture') not in constraints.get(actual['platform'], []):
        reasons.append('实际架构不符合工程声明')
    requirements = manifest.get('dependency_requirements', resolution['dependency_requirements'])
    if not isinstance(requirements, list):
        reasons.append('工程依赖声明必须是列表')
        requirements = []
    for target in resolution['selected_targets']:
        declared = [r for r in requirements if isinstance(r, dict) and r.get('platform') == target]
        if len(declared) != 1 or not isinstance(declared[0].get('names'), list) or not all(isinstance(n, str) and n for n in declared[0]['names']):
            reasons.append(target + ' 工程依赖声明缺失或无效')
            continue
        if not {'Python','PostgreSQL','NATS'} <= set(declared[0]['names']):
            reasons.append(target + ' 工程依赖声明缺少基础依赖')
        if target == actual['platform'] and declared[0].get('implementation') in {'incomplete','unsupported'}:
            reasons.append(target + ' 工程声明的依赖实现尚未完成')
        architectures = declared[0].get('architectures')
        if target == actual['platform'] and architectures is not None and (not isinstance(architectures,list) or actual['architecture'] not in architectures):
            reasons.append(target + ' 依赖架构与实际主机不匹配')
    return {'ok': not reasons, 'host': actual, 'selected_targets': resolution['selected_targets'],
            'target_resolution': manifest.get('target_resolution', resolution['target_resolution']),
            'capability_gaps': gaps, 'architecture_constraints': constraints, 'dependency_requirements': requirements, 'reasons': reasons}


def require_installable(manifest, *, host=None):
    result = preflight_manifest(manifest, host=host)
    if not result['ok']:
        raise ValueError('; '.join(result['reasons']))
    return result
