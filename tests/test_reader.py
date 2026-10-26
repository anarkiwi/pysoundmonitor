"""Reader tests against synthetic Soundmonitor-format images."""

import pytest

from pysidtracker import SidImage

import helpers
from pysoundmonitor import SidParseError, parse, read
from pysoundmonitor import constants as c
from pysoundmonitor.reader import (
    decode_note_freq,
    find_fingerprint,
    locate_note_freq,
    locate_song,
)


def test_parse_full_song():
    data, anchor = helpers.build_sid()
    song = parse(data)
    assert song.player_anchor == anchor
    assert song.base == 0x1000
    assert len(song.sections) == 1
    section = song.sections[0]
    assert section.cia_latch == 0x4C00
    assert section.cadence == 0x4C01
    assert section.tempo == 7
    assert section.pattern_length == 4
    assert len(section.arp_table) == c.ARP_TABLE_LEN
    assert len(section.voices) == 3
    assert len(section.voices[0]) == 4
    assert song.cia_latch == 0x4C00


def test_parse_decodes_referenced_instruments():
    data, _ = helpers.build_sid()
    song = parse(data)
    indices = {i.index for i in song.instruments}
    assert 1 in indices  # voice 0 triggers instrument 1
    inst1 = next(i for i in song.instruments if i.index == 1)
    assert inst1.filter is not None


def test_parse_locates_note_freq():
    data, _ = helpers.build_sid(with_freq=True)
    song = parse(data)
    assert song.note_freq is not None
    assert len(song.note_freq.hi) == c.NOTE_FREQ_LEN
    assert song.note_freq.freq(0) == (song.note_freq.hi[0] << 8) | song.note_freq.lo[0]


def test_parse_without_note_freq():
    data, _ = helpers.build_sid(with_freq=False)
    song = parse(data)
    assert song.note_freq is None


def test_parse_multiple_sections():
    sections = [helpers.make_section(latch=0x3000), helpers.make_section(latch=0x2000)]
    data, _ = helpers.build_sid(sections=sections)
    song = parse(data)
    assert len(song.sections) == 2
    assert song.sections[0].cia_latch == 0x3000
    assert song.sections[1].cia_latch == 0x2000


def test_parse_prg():
    data, anchor = helpers.build_prg()
    song = parse(data)
    assert song.player_anchor == anchor
    assert song.header is None
    assert len(song.sections) == 1


def test_parse_packed_prg():
    """A packed bare PRG (SYS-stub unpacker) must decode to the same song."""
    data, anchor = helpers.build_packed_prg()
    song = parse(data)
    assert song.player_anchor == anchor
    assert song.base == 0x1000
    assert song.header is None
    assert len(song.sections) == 1


def test_parse_fingerprint_nosections_raises():
    """A fingerprint with no decodable sections must raise, not yield an empty song."""
    payload = bytes(0x10) + c.STA_DC04 + c.CMP_06 + c.STA_DC05 + bytes(0x10)
    prg = bytes((0x00, 0x10)) + payload
    with pytest.raises(SidParseError):
        parse(prg)


def test_read_from_bytes_and_path(tmp_path):
    data, _ = helpers.build_sid()
    assert read(data).base == 0x1000
    path = tmp_path / "tune.sid"
    path.write_bytes(data)
    assert read(str(path)).base == 0x1000


def test_parse_no_fingerprint_raises():
    with pytest.raises(SidParseError):
        parse(helpers.build_no_fingerprint())


def test_base_fallback_without_operands():
    data, anchor = helpers.build_sid(with_operands=False)
    image = SidImage.from_bytes(data)
    assert find_fingerprint(image) == anchor
    variant = parse(data).variant
    assert variant["base_source"] == "load_page"  # image.load fallback
    assert variant["bounds_source"] == "default"


def test_base_from_operands():
    data, _ = helpers.build_sid(with_operands=True)
    song = parse(data)
    assert song.base == 0x1000
    assert song.variant["base_source"] == "operand"
    assert len(song.sections) == 1


def test_find_fingerprint_absent():
    image = SidImage.from_prg(bytes((0x00, 0x10)) + bytes(0x100))
    assert find_fingerprint(image) is None


def test_find_fingerprint_needs_guard_before_hi():
    # STA $DC04 then STA $DC05 with no CMP #$06 guard between => not a match.
    payload = c.STA_DC04 + c.STA_DC05 + bytes(0x20)
    image = SidImage.from_prg(bytes((0x00, 0x10)) + payload)
    assert find_fingerprint(image) is None


def test_find_fingerprint_needs_hi_store():
    # STA $DC04 + CMP #$06 but no STA $DC05 in the window => not a match.
    payload = c.STA_DC04 + c.CMP_06 + bytes(0x20)
    image = SidImage.from_prg(bytes((0x00, 0x10)) + payload)
    assert find_fingerprint(image) is None


def test_variant_records_stage_and_engine():
    data, anchor = helpers.build_sid()
    variant = parse(data).variant
    assert variant["materialisation"] == "static"
    assert variant["engine"] == "cia_loader"
    assert variant.source("engine").addr == anchor


def test_variant_packed_prg_is_sys_unpack():
    data, _ = helpers.build_packed_prg()
    assert parse(data).variant["materialisation"] == "sys-unpack"


def test_locate_failure_carries_rejection_log():
    located = locate_song(SidImage.from_prg(bytes((0x00, 0x10)) + bytes(0x100)))
    assert not located.ok
    assert "fingerprint not found" in located.describe()
    with pytest.raises(SidParseError, match="no layout found"):
        located.require(SidParseError)


def test_decode_note_freq_direct():
    data, _ = helpers.build_sid(with_freq=True)
    image = SidImage.from_bytes(data)
    table = locate_note_freq(image)
    assert table is not None
    direct = decode_note_freq(image, table.addr)
    assert direct.hi == table.hi and direct.lo == table.lo
