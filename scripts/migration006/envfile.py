#!/usr/bin/env python3
"""envfile.py FILE NAME: set `export NAME=<value>` in FILE, taking the value from $AUDLI_NEW_VALUE (never argv, never printed)."""
import os
import pathlib
import shlex
import sys

path, name = pathlib.Path(sys.argv[1]), sys.argv[2]
kept = [line for line in (path.read_text().splitlines() if path.exists() else []) if not line.startswith(f'export {name}=')]
kept.append(f'export {name}={shlex.quote(os.environ["AUDLI_NEW_VALUE"])}')
path.write_text('\n'.join(kept) + '\n')
