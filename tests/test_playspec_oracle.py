"""The playspec against the tune's own 6502 over every oracle tune, whole song.

Marked ``oracle``: the tunes come from HVSC and the full-length runs are slow.
"""

import pytest

from pysidtracker.playspec import smt
from pysidtracker.playspec.tv import validate
from pysidtracker.testing import make_tune_fixtures, song_frames

from pysoundmonitor import SoundMonitorPlayer, playspec
from test_oracle_hvsc import TUNES
from test_playspec import memory_equals_the_6502

tune_id, tune_path = make_tune_fixtures(TUNES)
ROW_READER_TUNE = "MUSICIANS/J/JAP/Only_3.sid"
TEMPO_COUNTER = 0xCD80 - 0xCBFA
TEMPO_RELOAD = 0xCD81 - 0xCBFA


@pytest.mark.oracle
def test_spec_memory_equals_the_6502_for_every_oracle_tune(tune_path):  # noqa: F811
    data = tune_path.read_bytes()
    memory_equals_the_6502(data, song_frames(data, None))


@pytest.mark.oracle
def test_row_reader_is_proven_against_the_6502(tune):
    """``reader[0].parse`` equals the 6502 row reader for all initial memory."""
    player = SoundMonitorPlayer(tune(ROW_READER_TUNE).read_bytes())
    view = player.view()
    found = playspec.binding(view, player.entry)
    result = validate(
        playspec.SPEC, found, "reader[0].parse", view, playspec.row_reader(view)
    )
    assert result.proven, result.counterexample and {
        k: v for k, v in result.counterexample.items() if k != "memory"
    }


@pytest.mark.oracle
@pytest.mark.parametrize(
    "routine, pc", [("mixer.step", 0xCA2B), ("sweeper.step", 0xC25F)]
)
def test_effect_routines_are_proven_against_the_6502(tune, routine, pc):
    """The volume ramp and the filter sweep equal their 6502 subroutines."""
    player = SoundMonitorPlayer(tune(ROW_READER_TUNE).read_bytes())
    view = player.view()
    found = playspec.binding(view, player.entry)
    result = validate(playspec.SPEC, found, routine, view, pc)
    assert result.proven, result.counterexample and {
        k: v for k, v in result.counterexample.items() if k != "memory"
    }


@pytest.mark.oracle
def test_bounded_checking_holds_from_post_init_memory(tune):
    player = SoundMonitorPlayer(tune(ROW_READER_TUNE).read_bytes())
    found = playspec.binding(player.view(), player.entry)
    mem = smt.concrete_memory(
        player.view(),
        symbolic=(found["rec"] + TEMPO_COUNTER, found["rec"] + TEMPO_RELOAD),
    )
    assert smt.bmc(player.program(), 2, mem).holds
