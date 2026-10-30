"""The Soundmonitor song-stream grammar and the stream models it parses to.

``soundmonitor.lark`` is a :class:`~pysidtracker.bytegrammar.ByteGrammar`: each
start rule is one stream kind the player reads, parsed over a window of exactly
that stream's bytes, so a malformed stream is a :class:`SidParseError`.
"""

from __future__ import annotations

from typing import Iterable, List, NamedTuple, Tuple

from lark import Token, Transformer, v_args
from pysidtracker.bytegrammar import ByteGrammar, Parsed, Stream

from . import constants as c
from .errors import SidParseError

SECTION_HEADER_LEN = c.SH_ARP_TABLE + c.ARP_TABLE_LEN
PAGE_LEN = 0x100
PAGES_PER_VOICE = 4
TABLE_PAGES = 3 * PAGES_PER_VOICE + 2
TABLES_LEN = TABLE_PAGES * PAGE_LEN


class SectionHeader(NamedTuple):
    """A section header: latch, tempo, pattern length, volume config, arp table."""

    latch: int
    tempo: int
    patlen: int
    vol_ramp: int
    base_vol: int
    arp: Tuple[int, ...]

    def to_bytes(self) -> bytes:
        """The header bytes."""
        return bytes(
            (
                self.latch & 0xFF,
                self.latch >> 8,
                self.tempo,
                self.patlen,
                self.vol_ramp,
                self.base_vol,
                *self.arp,
            )
        )


class PatternStream(NamedTuple):
    """A voice's ``(note, ctrl)`` rows."""

    rows: Tuple[Tuple[int, int], ...]

    def to_bytes(self) -> bytes:
        """The row bytes."""
        return bytes(byte for row in self.rows for byte in row)


class InstrumentStream(NamedTuple):
    """An instrument record and its 8-byte tail (marker byte first)."""

    record: bytes
    tail: bytes

    def to_bytes(self) -> bytes:
        """The 24 record bytes."""
        return self.record + self.tail


class SoundBank(NamedTuple):
    """Consecutive instrument records."""

    instruments: Tuple[InstrumentStream, ...]

    def to_bytes(self) -> bytes:
        """The records' bytes."""
        return b"".join(inst.to_bytes() for inst in self.instruments)


class TrackTables(NamedTuple):
    """The table pages at the data base: per-voice and section-header tables."""

    pages: Tuple[bytes, ...]

    def to_bytes(self) -> bytes:
        """The table pages' bytes."""
        return b"".join(self.pages)

    def _page(self, number: int, index: int) -> int:
        return self.pages[number][index]

    def stream_ptr(self, voice: int, index: int) -> int:
        """Voice ``voice``'s pattern stream address for section ``index``."""
        first = voice * PAGES_PER_VOICE
        return self._page(first, index) | (self._page(first + 1, index) << 8)

    def transpose(self, voice: int, index: int) -> int:
        """Voice ``voice``'s transpose for section ``index``."""
        return self._page(voice * PAGES_PER_VOICE + 2, index)

    def instrument_base(self, voice: int, index: int) -> int:
        """Voice ``voice``'s instrument base for section ``index``."""
        return self._page(voice * PAGES_PER_VOICE + 3, index)

    def header_ptr(self, index: int) -> int:
        """The section-header address for section ``index``."""
        first = 3 * PAGES_PER_VOICE
        return self._page(first, index) | (self._page(first + 1, index) << 8)


class StepsOnly(NamedTuple):
    """STEPS ONLY save data: the track tables and the instrument records."""

    tables: TrackTables
    instruments: SoundBank

    def to_bytes(self) -> bytes:
        """The tables then the records."""
        return self.tables.to_bytes() + self.instruments.to_bytes()


def _bytes(tokens: Iterable[Token]) -> bytes:
    return "".join(tokens).encode("latin-1")


@v_args(inline=True)
class _Streams(Transformer):
    """Builds each start rule's :class:`Parsed` while parsing."""

    def section_header(self, *tokens):
        """A section header."""
        vals = list(_bytes(tokens))
        header = SectionHeader(
            vals[0] | (vals[1] << 8),
            vals[2],
            vals[3],
            vals[4],
            vals[5],
            tuple(vals[6:]),
        )
        return Parsed(header, SECTION_HEADER_LEN)

    def row(self, note, ctrl):
        """One ``(note, ctrl)`` row."""
        return ord(note), ord(ctrl)

    def pattern(self, *rows):
        """A pattern stream."""
        return Parsed(PatternStream(tuple(rows)), len(rows) * c.ROW_LEN)

    def record(self, *tokens):
        """An instrument record."""
        raw = _bytes(tokens)
        return InstrumentStream(
            raw[: c.INSTRUMENT_RECORD_LEN], raw[c.INSTRUMENT_RECORD_LEN :]
        )

    def instrument(self, record):
        """An instrument stream."""
        return Parsed(record, c.INSTRUMENT_STRIDE)

    def sound_bank(self, *instruments):
        """A sound bank."""
        return Parsed(SoundBank(instruments), len(instruments) * c.INSTRUMENT_STRIDE)

    def voice_pages(self, *pages):
        """A voice's table pages."""
        return _bytes(pages)

    def header_pages(self, *pages):
        """The section-header table pages."""
        return _bytes(pages)

    def tables(self, *groups):
        """The track tables, as one page per entry."""
        data = b"".join(groups)
        pages: List[bytes] = [
            data[at : at + PAGE_LEN] for at in range(0, len(data), PAGE_LEN)
        ]
        return TrackTables(tuple(pages))

    def track_tables(self, tables):
        """The track tables stream."""
        return Parsed(tables, TABLES_LEN)

    def steps_only(self, tables, *instruments):
        """STEPS ONLY save data."""
        bank = SoundBank(instruments)
        return Parsed(
            StepsOnly(tables, bank),
            TABLES_LEN + len(instruments) * c.INSTRUMENT_STRIDE,
        )


GRAMMAR = ByteGrammar(
    __package__,
    "soundmonitor.lark",
    (
        "section_header",
        "pattern",
        "instrument",
        "sound_bank",
        "track_tables",
        "steps_only",
    ),
    _Streams(),
    SidParseError,
)

SECTION_HEADER = "section_header"
PATTERN = "pattern"
INSTRUMENT = "instrument"
SOUND_BANK = "sound_bank"
TRACK_TABLES = "track_tables"
STEPS_ONLY = "steps_only"


def streams(song) -> Iterable[Stream]:
    """The ``(name, model, rule)`` streams a decoded ``song`` reads."""
    for section in song.sections:
        yield (
            f"section {section.index} header",
            SectionHeader(
                section.cia_latch,
                section.tempo,
                section.pattern_length,
                section.vol_ramp,
                section.base_volume,
                section.arp_table,
            ),
            SECTION_HEADER,
        )
        for voice, rows in enumerate(section.voices):
            yield (
                f"section {section.index} voice {voice}",
                PatternStream(tuple((row.note, row.ctrl) for row in rows)),
                PATTERN,
            )
    for inst in song.instruments:
        tail = (
            inst.filter.raw
            if inst.filter is not None
            else bytes((c.NO_FILTER_TAIL,)) + bytes(c.FILTER_TAIL_LEN - 1)
        )
        yield f"instrument {inst.index}", InstrumentStream(inst.raw, tail), INSTRUMENT
