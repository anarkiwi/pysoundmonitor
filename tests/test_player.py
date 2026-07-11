"""Offline player tests: run a minimal real 6502 init/play through the player.

The synthetic tune (``helpers.build_playable_sid``) carries functional 6502 code
(no engine bytes), so :class:`~pysoundmonitor.SoundMonitorPlayer` exercises its
full run-init / per-frame / snapshot / reglog surface without the network or the
Docker oracle. Byte-exact validation against real HVSC tunes lives in the
``oracle``-marked ``test_oracle_hvsc``.
"""

import pytest

from pysidtracker import MemPlayer, RegWrite, SidImage

import helpers
from pysoundmonitor import SidParseError, SoundMonitorPlayer, SoundMonitorSidParser


def test_player_is_memplayer():
    assert issubclass(SoundMonitorPlayer, MemPlayer)


def test_render_grid_shape_and_progress():
    player = SoundMonitorPlayer(helpers.build_playable_sid())
    grid = player.render_grid(6)
    assert len(grid) == 6
    assert all(len(row) == MemPlayer.REG_COUNT for row in grid)
    # The play routine bumps voice-1/voice-2 freq-lo each frame, so the grid moves.
    assert grid[0][0] != grid[5][0]
    assert grid[0][0] == 1 and grid[5][0] == 6


def test_init_seeds_volume():
    player = SoundMonitorPlayer(helpers.build_playable_sid())
    assert player.regs[0x18] == 0x0F  # $D418 volume set by init


def test_snapshot_masks_pw_hi():
    # Player masks the pulse-width-high nibbles to match the oracle semantics.
    player = SoundMonitorPlayer(helpers.build_playable_sid())
    player._mem[player.SID_BASE + 3] = 0xF5  # pylint: disable=protected-access
    assert player.snapshot()[3] == 0x05


def test_accepts_sidimage():
    image = SidImage.from_bytes(helpers.build_playable_sid())
    grid = SoundMonitorPlayer(image).render_grid(3)
    assert len(grid) == 3


def test_bare_prg_rejected():
    prg, _ = helpers.build_prg()
    with pytest.raises(SidParseError):
        SoundMonitorPlayer(prg)


def test_cadence_video_timed():
    cadence = SoundMonitorPlayer(helpers.build_playable_sid()).cadence
    assert cadence.cycles_per_call > 0
    assert cadence.clock_hz > 0


def test_register_writes_log():
    player = SoundMonitorPlayer(helpers.build_playable_sid())
    writes = list(player.register_writes(4))
    assert writes and all(isinstance(w, RegWrite) for w in writes)
    # Baseline (25 regs) is emitted at clock 0, later frames advance in time.
    assert writes[0].clock == 0
    assert writes[-1].clock > 0
    assert all(0 <= w.reg <= 0x18 for w in writes)


def test_parser_builds_player():
    parser = SoundMonitorSidParser()
    player = parser.player(helpers.build_playable_sid())
    assert isinstance(player, SoundMonitorPlayer)


class _FakeSid:
    """Minimal SID device double (write_register/clock/sampling_frequency)."""

    sampling_frequency = 44100.0

    def write_register(self, reg, val):
        pass

    def clock(self, _delta):
        return [0]


def test_render_wav(tmp_path):
    out = tmp_path / "out.wav"
    player = SoundMonitorPlayer(helpers.build_playable_sid())
    path = player.render_wav(out, 4, device=_FakeSid())
    assert path == out and out.exists() and out.stat().st_size > 0
