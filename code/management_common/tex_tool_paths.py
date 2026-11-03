"""Native private TeX locations shared by installation and Article discovery."""
from pathlib import Path
import os
import sys


def tool_home(home=None):
    return Path(home or os.environ.get('ARTICLE_TOOL_HOME') or Path.home())


def tinytex_root(home=None):
    home = tool_home(home)
    if sys.platform == 'win32':
        roaming = (Path(os.environ['APPDATA']) if home == Path.home() and os.environ.get('APPDATA')
                   else home / 'AppData' / 'Roaming')
        return roaming / 'TinyTeX'
    if sys.platform == 'darwin':
        return home / 'Library' / 'TinyTeX'
    return home / '.TinyTeX'


def tinytex_bin_dirs(home=None):
    return tuple(sorted(path for path in (tinytex_root(home) / 'bin').glob('*') if path.is_dir()))
