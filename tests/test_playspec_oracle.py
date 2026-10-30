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
PROOF_TUNE = "MUSICIANS/J/JAP/Only_3.sid"
TEMPO_COUNTER = 0xCD80 - 0xCBFA
TEMPO_RELOAD = 0xCD81 - 0xCBFA


@pytest.mark.oracle
def test_spec_memory_equals_the_6502_for_every_oracle_tune(tune_path):  # noqa: F811
    data = tune_path.read_bytes()
    memory_equals_the_6502(data, song_frames(data, None))


@pytest.mark.oracle
@pytest.mark.parametrize(
    "routine, pc, registers",
    [
        ("mixer.step", 0xCA2B, None),
        ("mixer.regs", 0xC8FD, None),
        ("sweeper.step", 0xC25F, None),
        ("sweeper.start", 0xC2EB, None),
        ("glider[0].slide", 0xC098, {"X": 0, "Y": 0}),
        ("glider[1].slide", 0xC098, {"X": 7, "Y": 1}),
        ("glider[2].slide", 0xC098, {"X": 14, "Y": 2}),
        ("arpeggiator[0].start", 0xCB57, {"X": 0}),
        ("arpeggiator[0].run", 0xCB8A, {"X": 0, "Y": 0}),
        ("arpeggiator[1].run", 0xCB8A, {"X": 1, "Y": 7}),
        ("arpeggiator[2].run", 0xCB8A, {"X": 2, "Y": 14}),
        ("reader[0].load", 0xCAA6, {"A": 0, "Y": 0x0F}),
    ],
)
def test_routines_are_proven_against_the_6502(tune, routine, pc, registers):
    """The volume ramp, the filter sweep and start, each voice's glide and arpeggio
    step, the arpeggio start and the instrument loader equal their 6502
    subroutines for all initial memory."""
    player = SoundMonitorPlayer(tune(PROOF_TUNE).read_bytes())
    view = player.view()
    found = playspec.binding(view, player.entry)
    result = validate(playspec.SPEC, found, routine, view, pc, registers=registers)
    assert result.proven, result.counterexample and {
        k: v for k, v in result.counterexample.items() if k != "memory"
    }


@pytest.mark.oracle
def test_bounded_checking_holds_from_post_init_memory(tune):
    player = SoundMonitorPlayer(tune(PROOF_TUNE).read_bytes())
    found = playspec.binding(player.view(), player.entry)
    mem = smt.concrete_memory(
        player.view(),
        symbolic=(found["rec"] + TEMPO_COUNTER, found["rec"] + TEMPO_RELOAD),
    )
    assert smt.bmc(player.program(), 2, mem).holds
