"""Byte-exact Soundmonitor replay: ``specs/soundmonitor.play`` run on a tune's init.

The tune's own init runs in the 6502 emulator as the PSID driver boots it
(power-on RAM, PAL/NTSC flag, banking; selecting the subtune and placing any
relocated data); each play call is then the playspec of
:mod:`pysoundmonitor.playspec`, run on the memory the init left, at the addresses
the build's own operands name. The tune's play routine (banking, the CIA-timed
cohort's wrapper) runs on the CPU and each call into the modelled entry runs as
the spec. A tune with play address 0 drives the replay from the interrupts its
init installs: the :class:`~pysidtracker.playspec.SpecPlayer` hosts them on
pysidtracker's interrupt host, runs each handler's call into the modelled entry
as the spec, and frames the grid at the play source's raises as the oracle is.
The per-frame grid is validated byte-exact against the ``sidtrace`` oracle (see
the oracle test). Reading ``.sid``/``.prg`` containers is routed through
:class:`~pysidtracker.SidImage`; :class:`SoundMonitorSidParser` gives the same
image the family ``parse``/``detect`` surface.
"""

from __future__ import annotations

from typing import Any, Iterator, List, Optional, Union

from pysidtracker import BaseSidParser, SidImage
from pysidtracker import registers as reg
from pysidtracker.audio import frames_to_seconds, render_player_wav
from pysidtracker.cadence import Cadence, playroutine_cadence
from pysidtracker.errors import reraise_as
from pysidtracker.playspec import SpecPlayer
from pysidtracker.reglog import RegWrite, register_writes_from_player

from . import playspec
from .errors import SidParseError
from .model import Song
from .reader import find_fingerprint, locate_song, parse


class SoundMonitorPlayer(SpecPlayer):
    """Play a Soundmonitor tune one frame at a time, byte-exact to the oracle.

    Constructed from raw ``.sid`` bytes (or a :class:`~pysidtracker.SidImage`);
    ``subtune`` selects the sub-song (passed to ``init`` in the accumulator).
    :meth:`render_grid` yields the 25-register, forward-filled per-frame grid;
    :meth:`register_writes` and :meth:`render_wav` reuse the shared reglog/audio
    surfaces off the same replay. With ``validate`` the streams the spec opens
    are parsed under the song grammar (see ``validator``); ``bind`` returns the
    spec variant and layout of the instance, and ``spec`` is the base it is given.
    """

    error_class = SidParseError

    def __init__(
        self,
        data: Union[bytes, bytearray, SidImage],
        subtune: int = 0,
        *,
        max_cycles: int = 8_000_000,
        validate: bool = False,
        bind=playspec.bind,
        spec=playspec.SPEC,
    ):
        image = data if isinstance(data, SidImage) else SidImage.from_bytes(bytes(data))
        header = image.header
        if header is None:
            raise self.error_class("cannot play a bare .prg: image has no SID header")
        init = header.init_address or header.real_load_address
        self._tune = image.container + image.image
        self._subtune = subtune
        self._cadence: Optional[Cadence] = None
        with reraise_as(self.error_class):
            super().__init__(
                spec,
                image.image,
                image.load,
                init,
                header.play_address,
                subtune,
                bind=bind,
                entries=playspec.entries,
                validate=validate,
                sid=self._tune,
                max_cycles=max_cycles,
                sid_model=reg.sid_model_for_flags(header.flags),
                ntsc=header.is_ntsc,
            )

    @property
    def cadence(self) -> Cadence:
        """The tune's play-routine :class:`~pysidtracker.cadence.Cadence` (cached)."""
        if self._cadence is None:
            self._cadence = playroutine_cadence(self._tune, subtune=self._subtune)
        return self._cadence

    @property
    def cycles_per_frame(self) -> int:
        """CPU cycles between play calls (the period a WAV render spaces frames by)."""
        return self.cadence.cycles_per_call

    def render_grid(self, nframes: int) -> List[List[int]]:
        """``nframes`` rows; a hosted tune's are framed at its play source's raises.

        A hosted tune whose CPU never entered an interrupt vector (an ``*_entry``
        row) is not playing: it raises ``error_class``.
        """
        with reraise_as(self.error_class):
            grid = super().render_grid(nframes)
        if self._hosted is not None and not any(
            r[1] is None and r[3].endswith("_entry") for r in self._hosted.host.raw
        ):
            raise self.error_class("no interrupt was seen to play the tune")
        return grid

    def register_writes(self, nframes: int) -> Iterator[RegWrite]:
        """The post-init baseline at clock 0, then ``nframes`` frames a period apart."""
        return register_writes_from_player(self, nframes, self.cycles_per_frame)

    def render_wav(self, dst, nframes: int, *, model: str = "8580", **kwargs):
        """Render ``nframes`` through an emulated SID to a WAV file at ``dst``."""
        clock = self.cadence.clock_hz
        return render_player_wav(
            self,
            dst,
            seconds=frames_to_seconds(nframes, self.cycles_per_frame, clock),
            model=model,
            clock_frequency=clock,
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
        return anchor if anchor is not None and locate_song(image).ok else None

    def player(self, data: bytes, subtune: int = 0) -> SoundMonitorPlayer:
        """Build a :class:`SoundMonitorPlayer` for ``data``."""
        return SoundMonitorPlayer(data, subtune)
