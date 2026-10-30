"""Native filesystem spellings, separate from persisted logical paths.

Windows extended DOS paths work even with LongPathsEnabled=0. Convert only at
I/O boundaries; these helpers do not validate containment or grant access.
"""
from __future__ import annotations

import ntpath
import os
from pathlib import Path


def windows_extended_path(path) -> str:
    value = os.fspath(path)
    if value.startswith('\\\\?\\'):
        return value
    value = ntpath.abspath(value)
    if value.startswith('\\\\'):
        return '\\\\?\\UNC\\' + value[2:]
    return '\\\\?\\' + value


def filesystem_path(path) -> Path:
    return Path(windows_extended_path(path) if os.name == 'nt' else path)


def logical_path(path) -> Path:
    value = os.fspath(path)
    if os.name == 'nt':
        if value.startswith('\\\\?\\UNC\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
    return Path(value)
