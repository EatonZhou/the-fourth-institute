"""Give owned PostgreSQL executables a UTF-8 process code page on Windows 11.

The vendor's narrow Win32 calls otherwise lose non-ACP characters in executable
and database paths. Preserve every existing manifest setting and other resource;
never change the system locale. Only the installer's managed copies are edited.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET

ASM = 'urn:schemas-microsoft-com:asm.v1'
APP = 'urn:schemas-microsoft-com:asm.v3'
SETTINGS = 'http://schemas.microsoft.com/SMI/2019/WindowsSettings'


def utf8_manifest(data: bytes | None) -> bytes:
    root = ET.fromstring(data) if data else ET.Element(f'{{{ASM}}}assembly', manifestVersion='1.0')
    setting = root.find(f'.//{{{SETTINGS}}}activeCodePage')
    if setting is not None and setting.text == 'UTF-8':
        return data
    if setting is None:
        app = root.find(f'{{{APP}}}application')
        if app is None:
            app = ET.SubElement(root, f'{{{APP}}}application')
        settings = app.find(f'{{{APP}}}windowsSettings')
        if settings is None:
            settings = ET.SubElement(app, f'{{{APP}}}windowsSettings')
        setting = ET.SubElement(settings, f'{{{SETTINGS}}}activeCodePage')
    setting.text = 'UTF-8'
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


class Resources:
    """Read/update PE resources without loading or executing the program."""
    def __init__(self):
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE,
                                          ctypes.c_void_p, ctypes.c_void_p,
                                          wintypes.WORD, ctypes.c_ssize_t)
        signatures = {
            'LoadLibraryExW': ([wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD], wintypes.HMODULE),
            'FreeLibrary': ([wintypes.HMODULE], wintypes.BOOL),
            'EnumResourceLanguagesW': ([wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p,
                                       self.callback, ctypes.c_ssize_t], wintypes.BOOL),
            'FindResourceExW': ([wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p,
                                wintypes.WORD], wintypes.HANDLE),
            'LoadResource': ([wintypes.HMODULE, wintypes.HANDLE], wintypes.HANDLE),
            'SizeofResource': ([wintypes.HMODULE, wintypes.HANDLE], wintypes.DWORD),
            'LockResource': ([wintypes.HANDLE], ctypes.c_void_p),
            'BeginUpdateResourceW': ([wintypes.LPCWSTR, wintypes.BOOL], wintypes.HANDLE),
            'UpdateResourceW': ([wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                 wintypes.WORD, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            'EndUpdateResourceW': ([wintypes.HANDLE, wintypes.BOOL], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result

    def read(self, path):
        module = self.api.LoadLibraryExW(str(path), None, 0x2 | 0x20)
        if not module:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            languages = []
            @self.callback
            def collect(_module, _type, _name, language, _context):
                languages.append(language)
                return True
            if not self.api.EnumResourceLanguagesW(module, 24, 1, collect, 0):
                error = ctypes.get_last_error()
                if error in (1812, 1813, 1814, 1815):  # No existing application manifest.
                    return {0: None}
                raise ctypes.WinError(error)
            result = {}
            for language in languages:
                resource = self.api.FindResourceExW(module, 24, 1, language)
                loaded = self.api.LoadResource(module, resource) if resource else None
                address = self.api.LockResource(loaded) if loaded else None
                size = self.api.SizeofResource(module, resource) if resource else 0
                if not address or not size:
                    raise ctypes.WinError(ctypes.get_last_error())
                result[language] = ctypes.string_at(address, size)
            return result
        finally:
            self.api.FreeLibrary(module)

    def write(self, path, manifests):
        handle = self.api.BeginUpdateResourceW(str(path), False)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            for language, data in manifests.items():
                buffer = ctypes.create_string_buffer(data)
                if not self.api.UpdateResourceW(handle, 24, 1, language, buffer, len(data)):
                    raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.api.EndUpdateResourceW(handle, True)
            raise
        if not self.api.EndUpdateResourceW(handle, False):
            raise ctypes.WinError(ctypes.get_last_error())


def prepare_postgres(root: Path) -> dict:
    if os.name != 'nt':
        raise RuntimeError('Windows PostgreSQL preparation requires Windows')
    resources = Resources()
    programs = sorted((root / 'bin').glob('*.exe'))
    if not {'initdb.exe', 'postgres.exe', 'pg_ctl.exe', 'psql.exe'} <= {p.name for p in programs}:
        raise ValueError('Incomplete PostgreSQL executable directory')
    for path in programs:
        if path.is_symlink() or path.resolve() != path.absolute():
            raise ValueError('Redirected PostgreSQL executable')
        original = resources.read(path)
        updated = {language: utf8_manifest(data) for language, data in original.items()}
        if updated == original:
            continue
        # A failed edit (including a running/locked executable) retains the old
        # file. Retry is idempotent; the managed directory is already locked.
        with tempfile.TemporaryDirectory(prefix='.utf8-', dir=path.parent) as temporary:
            copy = Path(temporary) / path.name
            shutil.copy2(path, copy)
            resources.write(copy, updated)
            if resources.read(copy) != updated:
                raise RuntimeError('PostgreSQL UTF-8 manifest update did not persist')
            copy.replace(path)
    return {'process_code_page': 'UTF-8', 'manifest_revision': 1,
            'programs': [path.name for path in programs]}
