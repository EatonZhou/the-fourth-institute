"""Load private service environment files before a native service execs its child."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re


def load_environment_files(files, environment):
    result = dict(environment)
    for filename in files:
        path = Path(filename)
        if not path.is_file():
            continue  # systemd's EnvironmentFile=- convention
        if path.is_symlink():
            raise ValueError(f"Service environment file must not be a symlink: {path}")
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, value = line.partition("=")
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(f"Invalid service environment assignment in {path}")
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError(f"Invalid quoted service environment value in {path}")
                value = value[1:-1]
            result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("service command is required")
    os.execvpe(command[0], command, load_environment_files(args.env_file, os.environ))


if __name__ == "__main__":
    main()
