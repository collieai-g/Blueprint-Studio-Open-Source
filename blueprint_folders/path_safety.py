"""Dependency-free checks for links, including Windows junctions.

Inspect the spelling supplied by the caller before resolving a path: resolving
a junction first would hide the very boundary the check is meant to protect.
These checks complement, rather than replace, the caller's ownership/lease and
containment checks.
"""
from __future__ import annotations

from pathlib import Path
import stat


def is_link(path) -> bool:
    """Return whether an entry is a symlink or any Windows reparse point."""
    try:
        info = Path(path).lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) &
        getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def has_link(path) -> bool:
    """Check an entry and its lexical ancestors without following links."""
    # Path.absolute() anchors relative paths without collapsing lexical '..'.
    original = Path(path).expanduser().absolute()
    return any(is_link(part) for part in (original, *original.parents))


# Descriptive alias for callers that already use that terminology.
has_link_ancestor = has_link
