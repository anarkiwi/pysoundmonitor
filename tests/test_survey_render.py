"""The ``scripts/oracle_hvsc.py`` survey renderer and its render contract."""

import json
import re

import numpy as np
import pytest

from pysidtracker import SidParseError as BaseSidParseError
from pysidtracker.cadence import Cadence
from pysidtracker.registers import PAL_CLOCK_HZ, PAL_CYCLES_PER_FRAME
from pysidtracker.sidid import sidid_signatures

import fetch_tunes
import helpers
import oracle_hvsc
from pysoundmonitor import SidParseError, SoundMonitorPlayer
from pysoundmonitor import player as player_mod


def _tune(relpath):
    try:
        return fetch_tunes.fetch(relpath).read_bytes()
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"{relpath} unreachable: {exc}")
        return b""


def _native(data, subtune, nframes):
    return SoundMonitorPlayer(data, subtune).render_grid(nframes)


@pytest.mark.parametrize(
    "relpath, info",
    [
        (
            "MUSICIANS/J/JAP/Only_3.sid",
            {"engine": "cia_loader", "rsid": False, "trigger": "cia_timer"},
        ),
        (
            "MUSICIANS/V/Vulgarik/Fi.sid",
            {"engine": "note_freq", "rsid": False, "trigger": "pal_video"},
        ),
        (
            "DEMOS/UNKNOWN/Lavender.sid",
            {"engine": "cia_loader", "rsid": True, "trigger": "pal_video"},
        ),
    ],
)
def test_render_matches_player_and_reports_build(relpath, info):
    data = _tune(relpath)
    grid, got = oracle_hvsc.render(data, 1, 60)
    assert grid.dtype == np.int16 and grid.shape == (60, 25)
    np.testing.assert_array_equal(grid, _native(data, 0, 60))
    assert json.loads(json.dumps(got)) == got
    assert {k: got[k] for k in info} == info
    assert (got["speed"], got["dynamic"]) == (1, False)


def test_render_reports_unknown_trigger_and_engine(monkeypatch):
    data = helpers.build_playable_sid()
    unknown = Cadence(PAL_CYCLES_PER_FRAME, None, PAL_CLOCK_HZ)
    monkeypatch.setattr(player_mod, "playroutine_cadence", lambda *_, **__: unknown)
    grid, info = oracle_hvsc.render(data, 1, 20)
    np.testing.assert_array_equal(grid, _native(data, 0, 20))
    assert (info["trigger"], info["speed"], info["engine"]) == (None, 1, None)


def test_render_plays_an_installed_interrupt():
    grid, info = oracle_hvsc.render(helpers.build_irq_sid("cia", bank=0x35), 1, 10)
    assert grid[:, 7].tolist() == list(range(1, 11))
    assert (info["rsid"], info["trigger"], info["speed"]) == (True, "cia_timer", 1)


def test_render_selects_one_based_subtune():
    data = _tune("DEMOS/M-R/Rape_1_end.sid")
    second = oracle_hvsc.render(data, 2, 100)[0]
    np.testing.assert_array_equal(second, _native(data, 1, 100))
    assert (second != oracle_hvsc.render(data, 1, 100)[0]).any()


@pytest.mark.parametrize(
    "data",
    [
        b"\x00\x18junk",
        b"PSID",
        helpers.build_irq_sid(cli=False),
    ],
)
def test_unplayable_raises_sid_parse_error(data):
    with pytest.raises(BaseSidParseError):
        oracle_hvsc.render(data, 1, 10)


def test_parse_error_is_survey_not_decompiled():
    assert issubclass(SidParseError, BaseSidParseError)


def test_player_regex_selects_soundmonitor_signature():
    names = [name for name, _ in sidid_signatures()]
    assert [n for n in names if re.search(oracle_hvsc.PLAYER, n, re.I)] == [
        "Soundmonitor"
    ]
