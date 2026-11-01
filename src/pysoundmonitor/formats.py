"""The ``pysidtracker.formats`` entry point: Soundmonitor for the generic CLI.

Registers a :class:`~pysidtracker.formats.SidFormat` so ``pysidtracker info|reglog|
wav`` and ``format:`` surveys recognise Soundmonitor tunes, using the package's own
parser and :class:`~pysoundmonitor.player.SoundMonitorPlayer`, and exports the
playspec through the ``spec`` hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from pysidtracker import SidImage
from pysidtracker.formats import SidFormat, SpecBinding

from . import playspec
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
                "init_addr": header.init_address or header.real_load_address,
                "play_addr": header.play_address,
            }
        return SoundMonitorTune(
            parse(data), bytes(data), int(kwargs.get("subtune", 0)), **fields
        )


def _player(tune: SoundMonitorTune) -> SoundMonitorPlayer:
    return SoundMonitorPlayer(tune.data, tune.subtune)


def spec_binding(tune: SoundMonitorTune) -> SpecBinding:
    """The :class:`~pysidtracker.formats.SpecBinding` :class:`SoundMonitorPlayer` plays:
    the packaged ``soundmonitor.play`` (or its hook variant, chosen per instance by
    :func:`~pysoundmonitor.playspec.bind`) on the tune's own container."""
    image = SidImage.from_bytes(tune.data)
    return SpecBinding(
        playspec.SPEC,
        playspec.bind,
        image.image,
        entries=playspec.entries,
        subtune=tune.subtune,
        sid=image.container + image.image,
    )


def _describe(tune: SoundMonitorTune) -> Iterable[str]:
    song = tune.song
    yield f"sections: {len(song.sections)}"
    yield f"instruments: {len(song.instruments)}"


def _canonical(tune: SoundMonitorTune) -> str:
    """The song as text: equal for two tunes exactly when they play the same song."""
    lines = []
    for sec in tune.song.sections:
        lines.append(
            f"section {sec.index} latch={sec.cia_latch:04x} tempo={sec.tempo} "
            f"patlen={sec.pattern_length} ramp={sec.vol_ramp:02x} "
            f"volume={sec.base_volume:02x} arp={bytes(sec.arp_table).hex()} "
            f"transpose={bytes(sec.transpose).hex()}"
        )
        for voice, rows in enumerate(sec.voices):
            lines.append(
                f"  voice {voice} "
                + " ".join(f"{r.note:02x}{r.ctrl:02x}" for r in rows)
            )
    for inst in tune.song.instruments:
        tail = inst.filter.raw.hex() if inst.filter else "-"
        lines.append(f"instrument {inst.index} {inst.raw.hex()} {tail}")
    return "\n".join(lines) + "\n"


def _stream_bytes(tune: SoundMonitorTune) -> bytes:
    """The bytes of every stream the player reads, for the corpus snapshot."""
    return b"".join(model.to_bytes() for _, model, _ in streams(tune.song))


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
        canonical=_canonical,
        probes={"streams": _stream_bytes},
        spec=spec_binding,
    )
