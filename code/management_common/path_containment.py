"""Whether one path lies inside another, compared as written."""
from __future__ import annotations

from pathlib import Path


def path_is_within(path: Path, root: Path) -> bool:
    """``path`` is ``root`` or below it. Neither is resolved: callers pass the paths they compare."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
