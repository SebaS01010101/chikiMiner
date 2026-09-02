"""Pure GH-AW detector."""

from __future__ import annotations

from collections.abc import Iterable


def find_ghaw_pair(filenames: Iterable[str]) -> str | None:
    """Return the first matching base name, or ``None``.

    The comparison is case-sensitive and only considers exact file suffixes:
    ``<base>.md`` paired with ``<base>.lock.yml``.  Directory entries are
    expected to be filtered by the API adapter; names such as ``foo.md/`` do
    not match either suffix and therefore cannot produce a false positive.
    """

    markdown_bases: set[str] = set()
    lock_bases: set[str] = set()

    for filename in filenames:
        if filename.endswith(".lock.yml"):
            base = filename[: -len(".lock.yml")]
            if base and base in markdown_bases:
                return base
            if base:
                lock_bases.add(base)
        elif filename.endswith(".md"):
            base = filename[: -len(".md")]
            if base and base in lock_bases:
                return base
            if base:
                markdown_bases.add(base)

    return None


def uses_ghaw(filenames: Iterable[str]) -> bool:
    """Return whether *filenames* contain a matching GH-AW file pair."""

    return find_ghaw_pair(filenames) is not None
