"""Reader tests against synthetic Soundmonitor-format images."""

import pytest

from pysidtracker import EmulatorUnavailable, SidImage

import helpers
from pysoundmonitor import SidParseError, parse, read
from pysoundmonitor import constants as c
from pysoundmonitor import reader
from pysoundmonitor.reader import (
    _recover_base,
    _run_init,
    decode_note_freq,
    find_fingerprint,
    locate_note_freq,
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


def test_read_from_bytes_and_path(tmp_path):
    data, _ = helpers.build_sid()
    assert read(data).base == 0x1000
    path = tmp_path / "tune.sid"
    path.write_bytes(data)
    assert read(str(path)).base == 0x1000


def test_parse_no_fingerprint_raises():
    with pytest.raises(SidParseError):
        parse(helpers.build_no_fingerprint())


def test_recover_base_fallback_without_operands():
    data, anchor = helpers.build_sid(with_operands=False)
    image = SidImage.from_bytes(data)
    assert find_fingerprint(image) == anchor
    assert _recover_base(image) == 0x1000  # image.load fallback


def test_recover_base_from_operands():
    data, _ = helpers.build_sid(with_operands=True)
    image = SidImage.from_bytes(data)
    assert _recover_base(image) == 0x1000
    # still decodes with the operand-recovered base
    assert len(parse(data).sections) == 1


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


def test_run_init_no_header_is_noop():
    # A bare .prg has no init address, so init cannot run.
    _, _ = helpers.build_prg()
    image = SidImage.from_prg(bytes((0x00, 0x10)) + bytes(0x40))
    assert _run_init(image) is False


def test_run_init_with_header_runs():
    data, _ = helpers.build_sid()
    image = SidImage.from_bytes(data)
    assert _run_init(image) is True
    assert image.end == 0x10000


def test_run_init_emulator_unavailable_is_noop(monkeypatch):
    # py65 is a required dependency, but if the base emulator is unavailable
    # run_init raises the BASE EmulatorUnavailable; _run_init must catch it and
    # fall back to decoding the directly loaded image.
    data, _ = helpers.build_sid()
    image = SidImage.from_bytes(data)

    def _boom(_image):
        raise EmulatorUnavailable("py65 missing")

    monkeypatch.setattr(reader, "run_init", _boom)
    assert _run_init(image) is False


def test_decode_note_freq_direct():
    data, _ = helpers.build_sid(with_freq=True)
    image = SidImage.from_bytes(data)
    table = locate_note_freq(image)
    assert table is not None
    direct = decode_note_freq(image, table.addr)
    assert direct.hi == table.hi and direct.lo == table.lo
