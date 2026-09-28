"""Portable link/reparse-point detection for Android release tools.

``Path.is_symlink()`` alone is **not** sufficient on Windows: a directory
junction (``IO_REPARSE_TAG_MOUNT_POINT``) is a reparse point that
``is_symlink()`` reports as ``False`` (verified on CPython 3.11), so a
junctioned parent directory, staging root, or ``android`` child would slip
past a symlink-only check — a fail-open gap. Python 3.11 has no
``Path.is_junction`` (added in 3.13), so this module re-derives the fact
portably:

- POSIX: ``lstat`` + ``S_ISLNK``;
- Windows: ``lstat`` + ``FILE_ATTRIBUTE_REPARSE_POINT`` — this covers
  symlinks, directory junctions/mount points, and any other reparse tag,
  which is the conservative (fail-closed) set: no reparse-point-mediated
  path component may take part in release material or staging validation.

The check never follows the link (``lstat`` only). A path that cannot be
``lstat``-ed (absent, permission denied) is reported as *not* a link;
presence, regular-file shape, and containment are validated separately by
the callers. Detection results are booleans only — no path values, no
reparse tags, no targets are ever exposed.
"""

from __future__ import annotations

import os
import stat
from typing import Union

PathLike = Union[str, os.PathLike]


def is_link_or_reparse(path: PathLike) -> bool:
    """True when ``path`` itself is a symlink or a Windows reparse point.

    Never follows the link; absent paths are ``False`` (not a link).
    """
    try:
        info = os.lstat(path)
    except OSError:
        return False
    except ValueError:  # NUL bytes and similar malformed paths: fail closed
        return True
    if stat.S_ISLNK(info.st_mode):
        return True
    if os.name == "nt":
        attributes = getattr(info, "st_file_attributes", 0)
        if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return True
    return False
