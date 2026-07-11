"""Byte-exact Soundmonitor replay as a :class:`~pysidtracker.MemPlayer`.

Soundmonitor's replay is relocatable and drives its cadence from inside the tune
(the CIA-timed cohort latches ``$DC04``/``$DC05``; the fixed-cadence cohort runs
off the PAL video frame), so a static transcription would have to re-derive that
per build. :class:`SoundMonitorPlayer` instead reproduces the replay faithfully
by running the tune's *own* 6502 ``init`` and ``play`` routines over the shared
:class:`~pysidtracker.MemPlayer` 64 KiB image: ``_init`` mounts the image and
runs ``init`` once; ``_frame`` advances one ``play`` call, and the SID register
file it programs is read back by :meth:`MemPlayer.snapshot`. The per-frame grid
is validated byte-exact against the ``sidtrace`` oracle (see the oracle test).

All Soundmonitor-specific machinery -- the py65 CPU with the VIC-raster / SID
read-back observers the replay samples, the run-to-RTS driver, and the cadence
derivation -- is private to this class. Reading ``.sid``/``.prg`` containers is
routed through :class:`~pysidtracker.SidImage`; :class:`SoundMonitorSidParser`
gives the same image the family ``parse``/``detect`` surface.
"""

from __future__ import annotations

from typing import Any, Iterator, List, Optional, Union

from pysidtracker import (
    BaseSidParser,
    Cadence,
    MemPlayer,
    RegWrite,
    SidImage,
    playroutine_cadence,
    register_writes_from_player,
    render_wav,
)
from pysidtracker import registers as reg
from pysidtracker.trace import _run_to_rts

from .errors import SidParseError
from .model import Song
from .reader import find_fingerprint, parse

# The section loader reads $D41B/$D41C (voice-3 oscillator/envelope) for its
# pseudo-random source; the sidtrace oracle drives those from the elapsed cycle
# count, so mirror that here for a byte-exact sample.
_SID_READBACK = (0xD41B, 0xD41C)
_MAX_CYCLES = 8_000_000


class SoundMonitorPlayer(MemPlayer):
    """Play a Soundmonitor tune one frame at a time, byte-exact to the oracle.

    Constructed from raw ``.sid``/``.prg`` bytes (or a
    :class:`~pysidtracker.SidImage`). ``subtune`` selects the sub-song (passed to
    ``init`` in the accumulator). :meth:`render_grid` yields the 25-register,
    forward-filled per-frame grid; :meth:`register_writes` and :meth:`render_wav`
    reuse the shared reglog/audio surfaces off the same replay.
    """

    def __init__(
        self,
        data: Union[bytes, bytearray, SidImage],
        subtune: int = 0,
        *,
        max_cycles: int = _MAX_CYCLES,
    ):
        image = data if isinstance(data, SidImage) else SidImage.from_bytes(bytes(data))
        if image.header is None:
            raise SidParseError("cannot play a bare .prg: image has no SID header")
        header = image.header
        self._max_cycles = max_cycles
        self._init_address = header.init_address or header.real_load_address
        self._play_address = header.play_address or self._init_address
        # Pristine container bytes for the (mutating) cadence trace; the player
        # runs over its own copy, so the source image stays untouched.
        self._sid_bytes = image.container + image.image
        self._mpu = None
        self._obs = None
        self._cadence: Optional[Cadence] = None
        super().__init__(bytes(image.mem), 0, subtune)

    def _make_cpu(self):
        """Build the py65 CPU over the image with the replay's read observers."""
        from py65.devices.mpu6502 import MPU
        from py65.memory import ObservableMemory

        subject = self._mem
        obs = ObservableMemory(subject=subject)
        mpu = MPU(memory=obs)

        def _on_raster(addr):
            line = (mpu.processorCycles // 63) % 312
            if addr == reg.VIC_RASTER:
                return line & 0xFF
            return (subject[reg.VIC_CONTROL_1] & 0x7F) | (((line >> 8) & 1) << 7)

        def _on_sidread(addr):  # pylint: disable=unused-argument
            return (mpu.processorCycles >> 3) & 0xFF

        obs.subscribe_to_read([reg.VIC_CONTROL_1, reg.VIC_RASTER], _on_raster)
        obs.subscribe_to_read(list(_SID_READBACK), _on_sidread)
        return mpu, obs

    def _init(self, subtune: int) -> None:
        self._mpu, self._obs = self._make_cpu()
        _run_to_rts(self._mpu, self._obs, self._init_address, subtune, self._max_cycles)

    def _frame(self) -> None:
        _run_to_rts(self._mpu, self._obs, self._play_address, 0, self._max_cycles)

    def snapshot(self) -> List[int]:
        """SID register file with the pulse-width-high nibbles masked.

        The SID uses only the low nibble of ``$D403``/``$D40A``/``$D411``; the
        oracle records them masked, so mask here too for a byte-exact grid.
        """
        regs = super().snapshot()
        for pw_hi in reg.PW_HI_REGS:
            regs[pw_hi] &= 0x0F
        return regs

    @property
    def cadence(self) -> Cadence:
        """The tune's play-routine :class:`~pysidtracker.Cadence` (cached)."""
        if self._cadence is None:
            self._cadence = playroutine_cadence(self._sid_bytes)
        return self._cadence

    def register_writes(self, nframes: int) -> Iterator[RegWrite]:
        """Frame this replay into a :class:`~pysidtracker.RegWrite` log.

        Emits the post-init baseline at clock 0, then ``nframes`` frames spaced by
        the tune's derived cadence (``cadence.cycles_per_call``).
        """
        return register_writes_from_player(self, nframes, self.cadence.cycles_per_call)

    def render_wav(self, dst, nframes: int, *, model: str = "8580", **kwargs):
        """Render ``nframes`` through an emulated SID to a WAV file at ``dst``."""
        cadence = self.cadence
        return render_wav(
            (self.play_frame() for _ in range(nframes)),
            dst,
            model=model,
            cycles_per_frame=cadence.cycles_per_call,
            clock_frequency=cadence.clock_hz,
            **kwargs,
        )


class SoundMonitorSidParser(BaseSidParser):
    """Family ``parse``/``detect`` surface for Soundmonitor containers.

    ``recognize`` returns the relocation-invariant engine fingerprint address, so
    a directly loaded tune classifies as ``DIRECT`` and a packed/relocated one as
    ``RELOCATED``/``PACKED`` after the base emulates its init.
    """

    error_class: type = SidParseError

    def parse(self, data: bytes, **kwargs: Any) -> Song:
        """Decode ``data`` into a :class:`~pysoundmonitor.model.Song`."""
        return parse(data)

    def recognize(self, image: SidImage) -> object:
        """Return the fingerprint address if the engine is present, else ``None``."""
        return find_fingerprint(image)

    def player(self, data: bytes, subtune: int = 0) -> SoundMonitorPlayer:
        """Build a :class:`SoundMonitorPlayer` for ``data``."""
        return SoundMonitorPlayer(data, subtune)
