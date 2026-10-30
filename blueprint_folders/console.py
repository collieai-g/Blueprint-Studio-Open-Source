"""The command-line protocol uses UTF-8 independently of the Windows locale."""
from __future__ import annotations

import sys


def configure_utf8_streams():
    """Configure real text streams; leave caller-owned StringIO captures alone."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            reconfigure(encoding='utf-8')
