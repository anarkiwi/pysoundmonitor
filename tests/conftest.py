"""Shared fixtures: synthetic images (offline) + optional on-demand real tune.

The offline suite is built entirely from synthetic Soundmonitor-format images
(``helpers.build_sid``); it never needs the network. Soundmonitor ``.sid`` tunes
are HVSC copyright works and are never committed -- ``tune_path`` FETCHES + CACHES
the reference tune on demand and the dependent test SKIPS cleanly if it cannot be
obtained (offline / mirror down).
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import fetch_tunes  # noqa: E402  (after sys.path tweak)

from helpers import TUNES  # noqa: E402


@pytest.fixture
def only3_path():
    """Path to the reference tune, fetched on demand; skip if unavailable."""
    try:
        return str(fetch_tunes.fetch(TUNES["only3"]))
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"reference tune unavailable offline: {exc}")
        return None
