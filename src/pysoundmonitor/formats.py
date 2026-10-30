"""The ``pysidtracker.formats`` entry point: Soundmonitor for the generic CLI.

Registers a :class:`~pysidtracker.formats.SidFormat` so ``pysidtracker info|reglog|
wav`` and ``format:`` surveys recognise Soundmonitor tunes, using the package's own
parser and :class:`~pysoundmonitor.player.SoundMonitorPlayer`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from pysidtracker import SidImage
from pysidtracker.formats import SidFormat

from .grammar import GRAMMAR, streams
from .model import Song
from .player import SoundMonitorPlayer, SoundMonitorSidParser
from .reader import parse


@dataclass
class SoundMonitorTune:
    """A decoded :class:`~pysoundmonitor.model.Song` with its container and subtune.

    Carries what the generic CLI needs beyond the song: the SID header metadata
    and the raw ``data``/``subtune`` the player is built from.
    """

    song: Song
    data: bytes
    subtune: int = 0
    name: str = ""
    author: str = ""
    released: str = ""
    load_addr: int = 0
    init_addr: int = 0
    play_addr: int = 0


class SoundMonitorFormatParser(SoundMonitorSidParser):
    """:class:`SoundMonitorSidParser` whose ``parse`` yields a :class:`SoundMonitorTune`."""

    def parse(self, data: bytes, **kwargs: Any) -> SoundMonitorTune:
        """Decode ``data``'s 0-based ``subtune`` (default 0) with its header metadata."""
        header = SidImage.from_bytes(data).header
        fields = {}
        if header is not None:
            fields = {
                "name": header.name,
                "author": header.author,
                "released": header.released,
                "load_addr": header.real_load_address,
                "init_addr": header.init_address,
                "play_addr": header.play_address,
            }
        return SoundMonitorTune(
            parse(data), bytes(data), int(kwargs.get("subtune", 0)), **fields
        )


def _player(tune: SoundMonitorTune) -> SoundMonitorPlayer:
    return SoundMonitorPlayer(tune.data, tune.subtune)


def _describe(tune: SoundMonitorTune) -> Iterable[str]:
    song = tune.song
    yield f"sections: {len(song.sections)}"
    yield f"instruments: {len(song.instruments)}"


def _streams(tune: SoundMonitorTune):
    return streams(tune.song)


def soundmonitor_format() -> SidFormat:
    """The Soundmonitor :class:`~pysidtracker.formats.SidFormat`."""
    return SidFormat(
        name="soundmonitor",
        parser=SoundMonitorFormatParser(),
        player=_player,
        describe=_describe,
        grammar=GRAMMAR,
        streams=_streams,
    )
