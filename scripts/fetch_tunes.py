#!/usr/bin/env python3
"""Download Soundmonitor ``.sid`` test tunes into a gitignored cache.

Thin wrapper over :mod:`pysidtracker.testing`: the shared "resolve from a local
HVSC tree, else a gitignored cache, else download from a mirror" core is reused
here. Soundmonitor tunes are HVSC copyright works and are **never** committed
(see ``.gitignore``); they are fetched on demand into ``tests/.tunecache/``
(gitignored), honouring a local HVSC tree via ``$HVSC`` first. The offline suite
uses synthetic fixtures; the real-tune test skips cleanly when a tune cannot be
fetched.

Usage::

    python scripts/fetch_tunes.py            # fetch every test tune
    python scripts/fetch_tunes.py --list     # print id -> HVSC path

Programmatic::

    from fetch_tunes import fetch, TUNES
    sid_path = fetch(TUNES["only3"])
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from pysidtracker.testing import (
    DEFAULT_MIRROR,
    TuneFetchError,
    fetch_tune,
    resolve_tune,
)

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(
    os.environ.get("SOUNDMONITOR_TUNECACHE", str(REPO / "tests" / ".tunecache"))
)

# Public HVSC mirror. Override with ``$HVSC_MIRROR`` (honoured inside the shared
# fetch core); kept here for back-compat.
MIRROR = os.environ.get("HVSC_MIRROR", DEFAULT_MIRROR).rstrip("/")

# id -> HVSC relative path (the Soundmonitor reference tune).
TUNES = {
    "only3": "MUSICIANS/J/JAP/Only_3.sid",
}

# Network fetch retries (a tune is skipped only if genuinely unreachable after
# these attempts). Overridable for tests.
RETRIES = int(os.environ.get("HVSC_FETCH_RETRIES", "4"))


def fetch(relpath: str, *, force: bool = False) -> Path:
    """Fetch ``relpath`` from the HVSC mirror into the cache; return its path.

    Honours a local HVSC tree via ``$HVSC`` and the gitignored cache before
    hitting the network. Raises :class:`TuneFetchError` if the tune is genuinely
    unreachable.
    """
    if force:
        return fetch_tune(
            relpath, cache_dir=CACHE, mirror=MIRROR, retries=RETRIES, force=True
        )
    path = resolve_tune(relpath, cache_dir=CACHE)
    if path is None:
        raise TuneFetchError("%s: unreachable after %d attempts" % (relpath, RETRIES))
    return path


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", help="only this tune id")
    parser.add_argument("--force", action="store_true", help="re-download")
    parser.add_argument("--list", action="store_true", help="print id -> path")
    args = parser.parse_args(argv)
    if args.list:
        for tid, rel in TUNES.items():
            print("%s\t%s" % (tid, rel))
        return 0
    for tid in [args.id] if args.id else list(TUNES):
        print("%s: %s -> %s" % (tid, TUNES[tid], fetch(TUNES[tid], force=args.force)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
