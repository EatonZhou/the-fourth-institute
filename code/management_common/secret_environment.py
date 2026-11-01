"""Read instance-owned private environment files without shell evaluation."""
import shlex
from .instance_manifest import InstanceManifest


def secret_environment(instance: InstanceManifest, name: str) -> dict[str, str]:
    """Read a fixed private env file without shell evaluation or diagnostics containing values."""
    path = instance.directory('secrets') / name
    if path.is_symlink():
        raise ValueError('Secret environment must not be a symbolic link')
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        key, separator, value = line.partition('=')
        if not separator or not key or not all(c.isalnum() or c == '_' for c in key):
            raise ValueError('Invalid secret environment entry')
        try:
            parts = shlex.split(value, comments=True)
        except ValueError:
            raise ValueError('Invalid secret environment quoting') from None
        if len(parts) > 1:
            raise ValueError('Secret environment values must be quoted')
        values[key] = parts[0] if parts else ''
    return values
