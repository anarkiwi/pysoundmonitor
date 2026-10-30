"""Behaviour of the song-stream grammar and the reader built on it."""

import re
from pathlib import Path

import pytest

import helpers
from pysoundmonitor import SidParseError, parse
from pysoundmonitor import constants as c
from pysoundmonitor.formats import SoundMonitorTune, soundmonitor_format
from pysoundmonitor.grammar import (
    GRAMMAR,
    INSTRUMENT,
    PATTERN,
    SECTION_HEADER,
    SOUND_BANK,
    STEPS_ONLY,
    TABLES_LEN,
    TRACK_TABLES,
    streams,
)


def _header(patlen=4):
    return bytes((0x00, 0x4C, 7, patlen, 0xFF, 0x0F, 0, 1, 2, 3, 4, 5, 6, 7))


def test_section_header_fields_and_roundtrip():
    header = GRAMMAR.parse_exact(_header(), SECTION_HEADER)
    assert (header.latch, header.tempo, header.patlen) == (0x4C00, 7, 4)
    assert header.arp == tuple(range(8))
    assert header.to_bytes() == _header()


def test_section_header_rejects_zero_pattern_length():
    with pytest.raises(SidParseError, match="offset 3"):
        GRAMMAR.parse_exact(_header(patlen=0), SECTION_HEADER)


def test_section_header_must_be_14_bytes():
    with pytest.raises(SidParseError):
        GRAMMAR.parse_exact(_header()[:-1], SECTION_HEADER)
    with pytest.raises(SidParseError, match="offset 14"):
        GRAMMAR.parse_exact(_header() + b"\0", SECTION_HEADER)


def test_pattern_is_whole_rows():
    stream = GRAMMAR.parse_exact(bytes((0x81, 0x41, 0, 0, 0x80, 0)), PATTERN)
    assert stream.rows == ((0x81, 0x41), (0, 0), (0x80, 0))
    with pytest.raises(SidParseError):
        GRAMMAR.parse_exact(bytes((0x81, 0x41, 0)), PATTERN)
    with pytest.raises(SidParseError):
        GRAMMAR.parse_exact(b"", PATTERN)


def test_instrument_tail_marker():
    plain = bytes(range(16)) + bytes((0xFF,)) + bytes(7)
    filtered = bytes(range(16)) + bytes((0x10,)) + bytes(7)
    assert GRAMMAR.parse_exact(plain, INSTRUMENT).tail[0] == 0xFF
    assert GRAMMAR.parse_exact(filtered, INSTRUMENT).tail[0] == 0x10
    with pytest.raises(SidParseError):
        GRAMMAR.parse_exact(plain[:-1], INSTRUMENT)


def test_sound_bank_is_whole_records():
    record = bytes(range(16)) + bytes((0xFF,)) + bytes(7)
    bank = GRAMMAR.parse_exact(record * 3, SOUND_BANK)
    assert len(bank.instruments) == 3
    with pytest.raises(SidParseError):
        GRAMMAR.parse_exact(record * 3 + b"\0", SOUND_BANK)


def test_track_tables_resolve_pointers_and_transpose():
    mem = bytearray(TABLES_LEN)
    mem[c.V1_PTR_LO + 2], mem[c.V1_PTR_HI + 2] = 0x34, 0x12
    mem[c.V2_XPOSE + 2] = 0xFC
    mem[c.SECTION_HEADER_PTR_LO + 2], mem[c.SECTION_HEADER_PTR_HI + 2] = 0x78, 0x56
    tables = GRAMMAR.parse_exact(bytes(mem), TRACK_TABLES)
    assert tables.stream_ptr(1, 2) == 0x1234
    assert tables.transpose(2, 2) == 0xFC
    assert tables.header_ptr(2) == 0x5678
    assert tables.stream_ptr(0, 2) == 0
    assert tables.to_bytes() == bytes(mem)


def test_steps_only_is_tables_then_records():
    record = bytes(range(16)) + bytes((0xFF,)) + bytes(7)
    data = GRAMMAR.parse_exact(bytes(TABLES_LEN) + record * 2, STEPS_ONLY)
    assert len(data.instruments.instruments) == 2
    with pytest.raises(SidParseError):
        GRAMMAR.parse_exact(bytes(TABLES_LEN), STEPS_ONLY)


def test_reader_stops_at_a_section_with_zero_pattern_length():
    sections = [
        helpers.make_section(),
        helpers.make_section(patlen=0),
        helpers.make_section(),
    ]
    data, _ = helpers.build_sid(sections=sections)
    assert len(parse(data).sections) == 1


def test_reader_reads_transpose_from_the_track_tables():
    section = helpers.make_section(transpose=(1, 0xFE, 3))
    data, _ = helpers.build_sid(sections=[section])
    assert parse(data).sections[0].transpose == (1, 0xFE, 3)


def test_decoded_song_streams_read_back_through_the_grammar():
    data, _ = helpers.build_sid()
    song = parse(data)
    GRAMMAR.check(streams(song), SidParseError)
    kinds = {rule for _, _, rule in streams(song)}
    assert kinds == {SECTION_HEADER, PATTERN, INSTRUMENT}


def test_format_lists_the_grammar_streams():
    data, _ = helpers.build_sid()
    fmt = soundmonitor_format()
    tune = fmt.parser.parse(data)
    assert isinstance(tune, SoundMonitorTune)
    lines = list(fmt.listing(tune))
    assert lines[0] == "section 0 header:"
    assert any(line.lstrip().startswith("pattern") for line in lines)


DOCS = Path(__file__).resolve().parent.parent / "docs" / "format.md"

_ACCESSORS = {
    "stream pointer lo": lambda t, v: t.stream_ptr(v, 7) & 0xFF,
    "stream pointer hi": lambda t, v: t.stream_ptr(v, 7) >> 8,
    "transpose": lambda t, v: t.transpose(v, 7),
}


def _documented_pages():
    rows = re.findall(r"^\| `\+\$([0-9A-F]{3})` \| (.+?) \|$", DOCS.read_text(), re.M)
    return [(int(offset, 16), label) for offset, label in rows]


def test_documented_table_pages_are_where_the_grammar_reads_them():
    pages = [(off, label) for off, label in _documented_pages() if off < 0xE00]
    assert [off for off, _ in pages] == list(range(0, 0xE00, 0x100))
    for offset, label in pages:
        mem = bytearray(TABLES_LEN)
        mem[offset + 7] = 0xA5
        tables = GRAMMAR.parse_exact(bytes(mem), TRACK_TABLES)
        voice = re.match(r"voice (\d) (.+)", label)
        if voice:
            number, what = int(voice.group(1)), voice.group(2)
            if what == "spare":
                reads = [
                    accessor(tables, v)
                    for accessor in _ACCESSORS.values()
                    for v in range(3)
                ] + [tables.header_ptr(7)]
                assert not any(reads), label
            else:
                assert _ACCESSORS[what](tables, number) == 0xA5, label
        else:
            lo_hi = 0xA5 if label.endswith("lo") else 0xA500
            assert tables.header_ptr(7) == lo_hi, label


def test_documented_instrument_table_follows_the_track_tables():
    rows = dict(_documented_pages())
    assert rows[0xE00].startswith("instrument records")
    record = bytes(range(16)) + bytes((0xFF,)) + bytes(7)
    data = GRAMMAR.parse_exact(bytes(TABLES_LEN) + record, STEPS_ONLY)
    assert TABLES_LEN == 0xE00
    assert data.instruments.instruments[0].record == record[:16]
