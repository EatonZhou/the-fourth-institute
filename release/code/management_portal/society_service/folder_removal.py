"""Remove folders that Agents may have left read-only."""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

_OWNER_ALL = stat.S_IRWXU


def _owner_may_enter_and_change(path: str) -> None:
    # Windows access is controlled by the native ACL. POSIX owner bits cannot
    # authorize widening it, and Windows has no effective Unix user id.
    if os.name == "nt":
        return
    status = os.lstat(path)
    if (
        stat.S_ISDIR(status.st_mode)
        and status.st_uid == os.geteuid()
        and status.st_mode & _OWNER_ALL != _OWNER_ALL
    ):
        os.chmod(path, stat.S_IMODE(status.st_mode) | _OWNER_ALL)


def remove_tree(root: Path | str) -> None:
    """Remove ``root`` with everything in it.

    An Agent may seal its results by dropping write permission on a folder
    (``chmod -R a-w``); the Portal's own user still owns that folder, so it
    restores its owner permissions before removing it.  Folders owned by other
    users stay as they are, and their removal fails as before.
    """

    top = str(root)
    _owner_may_enter_and_change(top)
    for current, directories, _files in os.walk(top):
        for name in directories:
            _owner_may_enter_and_change(os.path.join(current, name))
    shutil.rmtree(top)
