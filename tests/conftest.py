"""Test setup: ``scripts/`` (the survey renderer) is importable, and ``tune`` resolves HVSC tunes.

The offline suite is built entirely from synthetic Soundmonitor-format images
(``helpers``); it never needs the network. The real-tune corpus and the
``oracle``-marked byte-exact tests fetch + cache HVSC tunes on demand -- HVSC
``.sid`` tunes are copyright works and are never committed.
"""

import sys
from pathlib import Path

import pytest
from pysidtracker.testing import TuneFetchError

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))


@pytest.fixture
def tune(hvsc):
    """``tune(relpath)``: the tune's path via pysidtracker's ``hvsc`` fixture
    (``$HVSC``, else the ``.tunecache``, else the mirror); a test skips only a
    tune genuinely unreachable after retries."""

    def _tune(relpath):
        try:
            return hvsc(relpath)
        except TuneFetchError as exc:
            pytest.skip(f"{relpath} unreachable: {exc}")
            return None

    return _tune
