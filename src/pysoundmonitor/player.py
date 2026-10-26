"""Byte-exact Soundmonitor replay as a :class:`~pysidtracker.TunePlayer`.

Soundmonitor's replay is relocatable and drives its cadence from inside the tune
(the CIA-timed cohort latches ``$DC04``/``$DC05``; the fixed-cadence cohort runs
off the PAL video frame), so a static transcription would have to re-derive that
per build. :class:`SoundMonitorPlayer` instead runs the tune's *own* 6502
``init`` and ``play`` routines through pysidtracker's
:class:`~pysidtracker.TunePlayer` (jennings, or the interrupt host for a tune
with play address 0). The per-frame grid is validated byte-exact against the
``sidtrace`` oracle (see the oracle test). Reading ``.sid``/``.prg`` containers
is routed through :class:`~pysidtracker.SidImage`; :class:`SoundMonitorSidParser`
gives the same image the family ``parse``/``detect`` surface.
"""

from __future__ import annotations

from typing import Any

from pysidtracker import BaseSidParser, SidImage, TunePlayer
from pysidtracker.audio import frames_to_seconds, render_player_wav

from .errors import SidParseError
from .model import Song
from .reader import find_fingerprint, locate_song, parse


class SoundMonitorPlayer(TunePlayer):
    """Play a Soundmonitor tune one frame at a time, byte-exact to the oracle.

    Constructed from raw ``.sid`` bytes (or a :class:`~pysidtracker.SidImage`);
    ``subtune`` selects the sub-song (passed to ``init`` in the accumulator).
    :meth:`render_grid` yields the 25-register, forward-filled per-frame grid;
    :meth:`register_writes` and :meth:`render_wav` reuse the shared reglog/audio
    surfaces off the same replay.
    """

    error_class = SidParseError

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
