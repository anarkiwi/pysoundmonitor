"""The ``scripts/oracle_hvsc.py`` survey renderer and its render contract."""

import json
import re

import numpy as np
import pytest

from pysidtracker import SidParseError as BaseSidParseError
from pysidtracker import cadence as cadence_mod
from pysidtracker.cadence import Cadence
from pysidtracker.registers import PAL_CLOCK_HZ, PAL_CYCLES_PER_FRAME
from pysidtracker.sidid import sidid_signatures

import helpers
import oracle_hvsc
from pysoundmonitor import SidParseError, SoundMonitorPlayer


def _native(data, subtune, nframes):
    return SoundMonitorPlayer(data, subtune).render_grid(nframes)


@pytest.mark.parametrize(
    "relpath, info",
    [
        (
            "MUSICIANS/J/JAP/Only_3.sid",
            {
                "engine": "cia_loader",
                "stage": "direct",
                "rsid": False,
                "trigger": "cia_timer",
            },
        ),
        (
            "MUSICIANS/V/Vulgarik/Fi.sid",
            {
                "engine": "note_freq",
                "stage": "direct",
                "rsid": False,
                "trigger": "pal_video",
            },
        ),
        (
            "DEMOS/UNKNOWN/Lavender.sid",
            {"engine": "cia_loader", "rsid": True, "trigger": "pal_video"},
        ),
    ],
)
def test_render_matches_player_and_reports_build(relpath, info, tune):
    data = tune(relpath).read_bytes()
    grid, got = oracle_hvsc.render(data, 1, 60)
    assert grid.dtype == np.int16 and grid.shape == (60, 25)
    np.testing.assert_array_equal(grid, _native(data, 0, 60))
    assert json.loads(json.dumps(got)) == got
    assert {k: got[k] for k in info} == info
    assert (got["speed"], got["dynamic"]) == (1, False)


def test_play_reports_unknown_trigger(monkeypatch):
    data = helpers.build_playable_sid()
    unknown = Cadence(PAL_CYCLES_PER_FRAME, None, PAL_CLOCK_HZ)
    monkeypatch.setattr(cadence_mod, "playroutine_cadence", lambda *_, **__: unknown)
    grid, info = oracle_hvsc._play(data, 1, 20)
    np.testing.assert_array_equal(grid, _native(data, 0, 20))
    assert (info["trigger"], info["speed"]) == (None, 1)


def test_render_plays_an_installed_interrupt():
    grid, info = oracle_hvsc._play(helpers.build_irq_sid("cia", bank=0x35), 1, 10)
    assert grid[:, 7].tolist() == list(range(1, 11))
    assert (info["rsid"], info["trigger"], info["speed"]) == (True, "cia_timer", 1)


def test_render_selects_one_based_subtune(tune):
    data = tune("DEMOS/M-R/Rape_1_end.sid").read_bytes()
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


def test_unrecognised_tune_is_not_decompiled():
    with pytest.raises(SidParseError):
        oracle_hvsc.render(helpers.build_playable_sid(), 1, 10)


def test_render_reports_materialisation_stage(tune):
    data = tune("DEMOS/A-F/Big_Boing.sid").read_bytes()
    info = oracle_hvsc.render(data, 1, 10)[1]
    assert (info["stage"], info["engine"]) == ("relocated", "note_freq")


def test_parse_error_is_survey_not_decompiled():
    assert issubclass(SidParseError, BaseSidParseError)


def test_player_regex_selects_soundmonitor_signature():
    names = [name for name, _ in sidid_signatures()]
    assert [n for n in names if re.search(oracle_hvsc.PLAYER, n, re.I)] == [
        "Soundmonitor"
    ]
