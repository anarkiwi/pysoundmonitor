"""Byte-exact Soundmonitor replay as a :class:`~pysidtracker.EmuPlayer`.

Soundmonitor's replay is relocatable and drives its cadence from inside the tune
(the CIA-timed cohort latches ``$DC04``/``$DC05``; the fixed-cadence cohort runs
off the PAL video frame), so a static transcription would have to re-derive that
per build. :class:`SoundMonitorPlayer` instead runs the tune's *own* 6502
``init`` and ``play`` routines on pysidtracker's jennings host, one ``play``
call per frame, with the raster and osc3/env3 reads the replay samples; a tune
with play address 0 runs under :class:`~pysoundmonitor.irq.InterruptHost`. The
per-frame grid is validated byte-exact against the ``sidtrace`` oracle (see the
oracle test). Reading ``.sid``/``.prg`` containers is routed through
:class:`~pysidtracker.SidImage`; :class:`SoundMonitorSidParser` gives the same
image the family ``parse``/``detect`` surface.
"""

from __future__ import annotations

from typing import Any, Iterator, Optional, Union

from pysidtracker import (
    BaseSidParser,
    Cadence,
    EmuPlayer,
    RegWrite,
    SidImage,
    playroutine_cadence,
    register_writes_from_player,
    render_wav,
)
from pysidtracker import registers as reg

from .errors import SidParseError
from .irq import InterruptHost
from .model import Song
from .reader import find_fingerprint, parse

_MAX_CYCLES = 8_000_000


class SoundMonitorPlayer(EmuPlayer):
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
        init = header.init_address or header.real_load_address
        self._sid_bytes = image.container + image.image
        self._subtune = subtune
        self._cadence: Optional[Cadence] = None
        self._interrupt = None
        if not header.play_address:
            if self.cadence.source is None:
                raise SidParseError(
                    "play address 0 and no interrupt was seen to play the tune"
                )
            self._interrupt = self.cadence
        self._host: Optional[InterruptHost] = None
        super().__init__(
            bytes(image.mem),
            0,
            init,
            header.play_address or init,
            subtune,
            max_cycles=max_cycles,
            sid_model=reg.sid_model_for_flags(header.flags),
        )

    @property
    def cadence(self) -> Cadence:
        """The tune's play-routine :class:`~pysidtracker.Cadence` (cached)."""
        if self._cadence is None:
            self._cadence = playroutine_cadence(self._sid_bytes, subtune=self._subtune)
        return self._cadence

    def _init(self, subtune: int) -> None:
        """Run ``init``, or for play address 0 hand it to an :class:`InterruptHost`."""
        if self._interrupt is None:
            super()._init(subtune)
            return
        self._host = InterruptHost(
            self._mem,
            self._init_addr,
            subtune,
            self._interrupt,
            sid_model=self._sid_model,
            max_cycles=self._max_cycles,
        )
        self._mpu, self._obs = self._host.mpu, self._host.mem

    def _frame(self) -> None:
        if self._host is None:
            super()._frame()
        else:
            self._host.frame()

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
