"""Model decode tests (rows, instrument records, filter tail, note freq)."""

from pysoundmonitor import FilterTail, Instrument, NoteFreqTable, Row
from pysoundmonitor import constants as c


def test_row_rest():
    row = Row.decode(c.NOTE_REST, 0x02)
    assert row.is_rest and not row.is_tie and not row.trigger


def test_row_tie():
    row = Row.decode(c.NOTE_TIE, 0x00)
    assert row.is_tie and not row.trigger and row.freq_index == 0


def test_row_new_note_flags():
    row = Row.decode(0x80 | 0x25, 0x40 | 0x20 | 0x10 | 0x03)
    assert row.trigger
    assert row.freq_index == 0x25
    assert row.instrument == 0x03
    assert row.arp_enable and row.absolute and row.portamento
    assert row.instrument_base  # ctrl bit7 clear => per-section base added


def test_row_absolute_instrument_no_base():
    row = Row.decode(0x80 | 0x10, c.CTRL_INSTR_BASE | 0x05)
    assert row.trigger and row.instrument == 0x05
    assert not row.instrument_base  # ctrl bit7 set => absolute instrument index


def test_row_legato_uses_instrument_base():
    row = Row.decode(0x10, 0x02)
    assert not row.trigger  # bit7 clear => no key-on
    assert row.instrument_base  # ctrl bit7 clear => per-section base added
    assert row.instrument == 0x02


def test_instrument_with_filter_tail():
    record = bytes(range(1, 17)) + bytes(
        (0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47)
    )
    inst = Instrument.from_record(1, record)
    assert inst.index == 1
    assert inst.ctrl_gate == 1 and inst.detune == 16
    assert inst.pw_lo == (5 & 0x0F) << 4 and inst.pw_hi == 5 >> 4
    assert isinstance(inst.filter, FilterTail)
    assert inst.filter.vol_mode == 0x40 and inst.filter.flags == 0x47


def test_instrument_no_filter_tail():
    record = bytes(range(1, 17)) + bytes((c.NO_FILTER_TAIL,)) + bytes(7)
    inst = Instrument.from_record(2, record)
    assert inst.filter is None
    assert len(inst.raw) == c.INSTRUMENT_RECORD_LEN


def test_instrument_short_record_has_no_tail():
    inst = Instrument.from_record(3, bytes(range(16)))
    assert inst.filter is None


def test_note_freq_table():
    table = NoteFreqTable(hi=[0x01, 0x02], lo=[0x16, 0x27])
    assert table.freq(0) == 0x0116
    assert table.freq(1) == 0x0227
