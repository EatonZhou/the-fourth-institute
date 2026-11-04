"""Allocator counters only: no live-object references or allocator mutation.

CPython 3.12's own small-block statistics are collected under the GIL through
its FILE* API. They never share or redirect the service's stderr. Native heap
counters overlap Python raw allocations and must not be summed with them.
"""
from __future__ import annotations

import ctypes
import os
import re
import sys
import tempfile
from functools import lru_cache


class _Mallinfo2(ctypes.Structure):
    _fields_ = [(name, ctypes.c_size_t) for name in (
        'arena', 'ordblks', 'smblks', 'hblks', 'hblkhd', 'usmblks',
        'fsmblks', 'uordblks', 'fordblks', 'keepcost')]


class _HeapSummary(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_uint32)] + [(name, ctypes.c_size_t) for name in (
        'allocated', 'committed', 'reserved', 'max_reserve')]


@lru_cache(maxsize=1)
def _stdio():
    # PyDLL retains the GIL when invoking CPython's allocator inspection.
    lib = ctypes.PyDLL('ucrtbase.dll') if sys.platform == 'win32' else ctypes.PyDLL(None)
    fdopen = lib._fdopen if sys.platform == 'win32' else lib.fdopen
    fdopen.argtypes = [ctypes.c_int, ctypes.c_char_p]
    fdopen.restype = ctypes.c_void_p
    lib.fclose.argtypes = [ctypes.c_void_p]
    lib.fclose.restype = ctypes.c_int
    return lib, fdopen


def _parse_pymalloc(text: str) -> dict:
    labels = {
        '# arenas allocated total': 'arenas_allocated_total',
        '# arenas reclaimed': 'arenas_reclaimed',
        '# arenas highwater mark': 'arenas_highwater',
        '# arenas allocated current': 'arenas_current',
        '# bytes in allocated blocks': 'allocated_block_bytes',
        '# bytes in available blocks': 'available_block_bytes',
        '# bytes lost to pool headers': 'pool_header_bytes',
        '# bytes lost to quantization': 'quantization_bytes',
        '# bytes lost to arena alignment': 'alignment_bytes',
    }
    result = {}
    for line in text.splitlines():
        left, separator, right = line.partition('=')
        if not separator:
            continue
        value = right.strip().replace(',', '')
        if not value.isdecimal():
            continue
        label = left.strip()
        if label in labels:
            result[labels[label]] = int(value)
        elif re.fullmatch(r'\d+ arenas \* \d+ bytes/arena', label):
            result['arena_capacity_bytes'] = int(value)
        elif re.fullmatch(r'\d+ unused pools \* \d+ bytes', label):
            result['unused_pool_bytes'] = int(value)
    return {'available': bool(result), **result}


def pymalloc_summary() -> dict:
    if sys.implementation.name != 'cpython' or sys.version_info[:2] != (3, 12):
        return {'available': False, 'reason': 'unsupported_runtime'}
    function = getattr(ctypes.pythonapi, '_PyObject_DebugMallocStats', None)
    if function is None:
        return {'available': False, 'reason': 'missing_counter_api'}
    function.argtypes = [ctypes.c_void_p]
    function.restype = ctypes.c_int
    lib, fdopen = _stdio()
    with tempfile.TemporaryFile(mode='w+b') as target:
        duplicate = os.dup(target.fileno())
        stream = fdopen(duplicate, b'w')
        if not stream:
            os.close(duplicate)
            raise OSError('allocator counter stream unavailable')
        try:
            function(stream)
        finally:
            lib.fclose(stream)
        target.seek(0)
        # The native API emits a fixed size-class table, independent of heap
        # object count. Only the known numeric fields leave this function.
        return _parse_pymalloc(target.read(32768).decode('ascii', errors='replace'))


def native_heap_summary() -> dict:
    if sys.platform == 'linux':
        lib = ctypes.CDLL(None)
        function = getattr(lib, 'mallinfo2', None)
        if function is None:
            return {'available': False, 'reason': 'missing_mallinfo2'}
        function.argtypes = []
        function.restype = _Mallinfo2
        value = function()
        return {'available': True, 'allocator': 'glibc',
                'arena_capacity_bytes': value.arena,
                'in_use_arena_bytes': value.uordblks,
                'free_arena_bytes': value.fordblks,
                'mmap_bytes': value.hblkhd, 'mmap_count': value.hblks,
                'releasable_top_bytes': value.keepcost,
                'free_bytes_exclude_thread_tcache': True}
    if sys.platform == 'win32':
        lib = ctypes.WinDLL('kernel32', use_last_error=True)
        lib.GetProcessHeap.restype = ctypes.c_void_p
        function = getattr(lib, 'HeapSummary', None)
        if function is None:
            return {'available': False, 'reason': 'missing_heap_summary'}
        function.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_HeapSummary)]
        function.restype = ctypes.c_int
        value = _HeapSummary()
        value.cb = ctypes.sizeof(value)
        if not function(lib.GetProcessHeap(), 0, ctypes.byref(value)):
            return {'available': False, 'reason': 'heap_summary_failed',
                    'error_code': ctypes.get_last_error()}
        return {'available': True, 'allocator': 'windows_process_default_heap',
                **{name + '_bytes': getattr(value, name) for name in (
                    'allocated', 'committed', 'reserved', 'max_reserve')}}
    return {'available': False, 'reason': 'unsupported_platform'}


def summary() -> dict:
    result = {}
    for key, function in [('pymalloc', pymalloc_summary), ('native_heap', native_heap_summary)]:
        try:
            result[key] = function()
        except Exception as error:
            result[key] = {'available': False, 'error_type': type(error).__name__}
    return result
