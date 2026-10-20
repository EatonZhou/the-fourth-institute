"""Prepare a private, pinned TinyTeX distribution and the Article package set.

Distribution: https://github.com/rstudio/tinytex-releases/releases/tag/v2026.10
TeX packages are resolved by tlmgr; the resulting inventory is recorded locally.
No system package manager, global PATH edit or shared TeX mutation is performed.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

from management_common.process_runtime import run_process
from management_common.native_output import run_native_capture

VERSION = '2026.10'
ASSETS = {
    ('Linux', 'x86_64'): ('linux-x86_64', '4d519d6236ee6798e3ec0d8e2093d1fda01aeb267aa22c2a5586d76bcfce6566'),
    ('Linux', 'aarch64'): ('linux-arm64', '1bc5465c7a7c6f13e13c1b54a52147577eb3f389033af0444bc097698eb6653f'),
    ('Darwin', 'x86_64'): ('darwin', 'e40c294efae81026d8cc8d12c09d2dd456d268efe7fe092410478bc1973fd40f'),
    ('Darwin', 'aarch64'): ('darwin', 'e40c294efae81026d8cc8d12c09d2dd456d268efe7fe092410478bc1973fd40f'),
    ('Windows', 'x86_64'): ('tar', '79d52d94ec844a82ca50e18a8394bfc4ab2316ca5a406632aee38c72907e639c'),
}
PACKAGES = ('latexmk', 'synctex', 'ctex', 'fandol', 'geometry', 'amsmath', 'amsfonts',
            'amscls', 'mathtools', 'booktabs', 'tools', 'graphics', 'xcolor', 'pgf',
            'enumitem', 'fancyhdr', 'microtype', 'natbib', 'hyperref', 'caption', 'float',
            'setspace', 'placeins', 'biber', 'biblatex')
TOOLS = ('pdflatex', 'xelatex', 'lualatex', 'latexmk', 'biber', 'kpsewhich', 'synctex', 'texlab')
TEX_FILES = ('ctexbook.cls', 'geometry.sty', 'amsmath.sty', 'amssymb.sty', 'amsthm.sty',
             'mathtools.sty', 'booktabs.sty', 'longtable.sty', 'array.sty', 'tabularx.sty',
             'graphicx.sty', 'xcolor.sty', 'tikz.sty', 'enumitem.sty', 'fancyhdr.sty',
             'microtype.sty', 'natbib.sty', 'hyperref.sty', 'caption.sty', 'float.sty', 'setspace.sty', 'placeins.sty', 'biblatex.sty')


def tool_environment(home=None):
    home = Path(home or os.environ.get('ARTICLE_TOOL_HOME') or Path.home())
    bins = [home / '.local/bin', *sorted((home / '.TinyTeX/bin').glob('*'))]
    return {**os.environ, 'PATH': os.pathsep.join([*map(str, bins), os.environ.get('PATH', '')])}


def inventory():
    env = tool_environment()
    checks = []
    for tool in TOOLS:
        binary = shutil.which(tool, path=env['PATH'])
        detail = 'missing'
        if binary:
            try:
                result = run_native_capture([binary, 'help' if tool == 'synctex' else '--version'],
                                            env=env, timeout=90)
                detail = (result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else binary
                good = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired) as error:
                good, detail = False, str(error)
        else:
            good = False
        checks.append({'name': 'article: ' + tool, 'ok': good, 'detail': detail})
    kpsewhich = shutil.which('kpsewhich', path=env['PATH'])
    for filename in TEX_FILES:
        result = run_native_capture([kpsewhich, filename], env=env, timeout=90) if kpsewhich else None
        checks.append({'name': 'article: ' + filename, 'ok': bool(result and result.returncode == 0 and result.stdout.strip()),
                       'detail': result.stdout.strip() if result else 'missing kpsewhich'})
    return checks


def prepare_tex(home):
    home = Path(home)
    machine = {'amd64': 'x86_64', 'arm64': 'aarch64'}.get(platform.machine().lower(), platform.machine().lower())
    asset = ASSETS.get((platform.system(), machine))
    if not asset:
        raise RuntimeError('No native TinyTeX preparation for this platform/architecture; provide a verified native TeX installation')
    flavor, expected = asset
    extension = 'gz' if flavor == 'tar' else 'tar.xz'
    filename = f'TinyTeX-1-{flavor}-v{VERSION}.{extension}'
    url = f'https://github.com/rstudio/tinytex-releases/releases/download/v{VERSION}/{filename}'
    destination = home / '.TinyTeX'
    if not destination.exists():
        home.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.tex-prepare-', dir=home) as temporary:
            directory = Path(temporary)
            archive = directory / 'download'
            checksum, size = hashlib.sha256(), 0
            with urllib.request.urlopen(url, timeout=180) as response, archive.open('wb') as output:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > 256 * 1024 * 1024:
                        raise ValueError('TinyTeX download exceeds limit')
                    checksum.update(chunk)
                    output.write(chunk)
            if checksum.hexdigest() != expected:
                raise ValueError('TinyTeX archive checksum mismatch')
            unpacked = directory / 'unpacked'
            with tarfile.open(archive) as bundle:
                if sum(member.size for member in bundle.getmembers()) > 2 * 1024**3:
                    raise ValueError('TinyTeX extracted size exceeds limit')
                bundle.extractall(unpacked, filter='data')
            roots = list(unpacked.iterdir())
            if len(roots) != 1 or not (roots[0] / 'bin').is_dir():
                raise ValueError('Unexpected TinyTeX layout')
            roots[0].rename(destination)
    env = tool_environment(home)
    # Restrict mutation to our own distribution, even when PATH has system TeX.
    local_path = os.pathsep.join(str(p) for p in (destination / 'bin').glob('*'))
    tlmgr = shutil.which('tlmgr', path=local_path)
    if not tlmgr:
        raise RuntimeError('Private TinyTeX is incomplete; no native tlmgr')
    # Generate only formats supported by Article. tlmgr's aggregate actions
    # also build unrelated Japanese formats, which fail in Unicode paths.
    run_process([tlmgr, '--no-execute-actions', 'update', '--self', '--all'], env=env, check=True, timeout=5400)
    run_process([tlmgr, '--no-execute-actions', 'install', *PACKAGES], env=env, check=True, timeout=5400)
    run_process(['mktexlsr'], env=env, check=True, timeout=540)
    # The distribution's map catalog also lists optional fonts not installed
    # by this profile. Synchronize only this private tree before building maps.
    run_process(['updmap-sys', '--syncwithtrees'], input='y\n', text=True, env=env, check=True, timeout=540)
    for command in (['updmap-sys'],
                    *(['fmtutil-sys', '--byfmt', name] for name in ('pdflatex', 'xelatex', 'lualatex'))):
        run_process(command, env=env, check=True, timeout=540)
    installed = run_native_capture([tlmgr, 'info', '--only-installed', '--data', 'name,localrev'],
                                   env=env, check=True, timeout=270)
    (destination / 'tfi-dependencies.json').write_text(json.dumps({
        'distribution': url, 'sha256': expected, 'packages': installed.stdout.splitlines()}, indent=2))
    return env


def smoke_compile():
    env = tool_environment()
    with tempfile.TemporaryDirectory(prefix='tfi-article-check-') as directory:
        path = Path(directory)
        (path / 'main.tex').write_text('\\documentclass[fontset=fandol]{ctexbook}\n'
            '\\usepackage{amsmath,amssymb,amsthm,mathtools,booktabs,longtable,array,tabularx,graphicx,xcolor,tikz,enumitem,fancyhdr,microtype,natbib,hyperref,caption,float,setspace,placeins}\n'
            '\\begin{document}TFI Portal 中文 $x^2$\\FloatBarrier\\end{document}\n', encoding='utf-8')
        run_native_capture(['latexmk', '-xelatex', '-interaction=nonstopmode', '-halt-on-error',
                            '-no-shell-escape', 'main.tex'], cwd=path, env=env, check=True,
                           timeout=360)
        if not (path / 'main.pdf').is_file():
            raise RuntimeError('Article smoke compile produced no PDF')
