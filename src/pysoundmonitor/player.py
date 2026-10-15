"""Byte-exact Soundmonitor replay as a :class:`~pysidtracker.EmuPlayer`.

Soundmonitor's replay is relocatable and drives its cadence from inside the tune
(the CIA-timed cohort latches ``$DC04``/``$DC05``; the fixed-cadence cohort runs
off the PAL video frame), so a static transcription would have to re-derive that
per build. :class:`SoundMonitorPlayer` instead runs the tune's *own* 6502
``init`` and ``play`` routines on pysidtracker's jennings host, one ``play``
call per frame, with the raster and osc3/env3 reads the replay samples; a tune
with play address 0 runs under :class:`~pysidtracker.irqhost.InterruptPlayer`. The
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
    SidParseError as BaseSidParseError,
    playroutine_cadence,
    register_writes_from_player,
    render_wav,
)
from pysidtracker import registers as reg
from pysidtracker.irqhost import InterruptPlayer

from .errors import SidParseError
from .model import Song
from .reader import _materialized, find_fingerprint, parse

_MAX_CYCLES = 8_000_000


def _interrupted(raw) -> bool:
    """True if the CPU serviced a raise (entered an interrupt vector).

    The machine still *raises* a masked source, but the handler runs only if the
    CPU enters the vector -- reported as a ``<source>_entry`` event. A playing
    interrupt-driven tune has such an entry; a masked (unplayable) one has none.
    """
    return any(
        len(r) == 4 and r[1] is None and str(r[3]).endswith("_entry") for r in raw
    )


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
        self._hosted: Optional[InterruptPlayer] = None
        if not header.play_address:
            self._hosted = InterruptPlayer(image, subtune)
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
        """Run ``init``; a tune with play address 0 boots in its interrupt host."""
        if self._hosted is None:
            super()._init(subtune)

    def _frame(self) -> None:
        if self._hosted is None:
            super()._frame()
        else:
            self._hosted.play_frame()

    def snapshot(self):
        if self._hosted is None:
            return super().snapshot()
        return self._hosted.snapshot()

    def _assert_playable(self) -> None:
        host = self._hosted.host if self._hosted else None
        if host is not None and not _interrupted(host.raw):
            raise SidParseError("no interrupt was seen to play the tune")

    def render_grid(self, nframes: int):
        """Render ``nframes``; a hosted tune is framed at its play source's raises."""
        if self._hosted is None:
            return super().render_grid(nframes)
        try:
            grid = self._hosted.render_grid(nframes)
            self._assert_playable()
        except BaseSidParseError as exc:
            raise SidParseError(str(exc)) from exc
        return grid

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
        """The fingerprint address if the image materialises into a song, else ``None``.

        A fingerprint embedded in data the reader cannot decode (an unexpanded
        packed stream) is not a recognised tune, so the anchor is only returned
        once the image decodes (directly, after init, or after unpack).
        """
        anchor = find_fingerprint(image)
        return (
            anchor if anchor is not None and _materialized(image) is not None else None
        )

    def player(self, data: bytes, subtune: int = 0) -> SoundMonitorPlayer:
        """Build a :class:`SoundMonitorPlayer` for ``data``."""
        return SoundMonitorPlayer(data, subtune)
