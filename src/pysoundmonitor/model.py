"""Dataclass model of a decoded Soundmonitor song.

The song is a *section sequence*; each section carries a CIA play cadence, a
tempo, an arp table, and three per-voice ``[note][ctrl]`` pattern streams. New-
note rows reference a 16-byte instrument record (optionally trailed by an 8-byte
global-filter tail). See ``docs/format.md`` for the layout narrative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from pysidtracker import NoteFreqTable

from . import constants as c


@dataclass
class Row:
    """One decoded pattern row (``note`` byte + ``ctrl`` byte)."""

    note: int
    ctrl: int
    is_rest: bool
    is_tie: bool
    trigger: bool
    freq_index: int
    instrument: int
    portamento: bool
    absolute: bool
    arp_enable: bool
    instrument_base: bool

    @classmethod
    def decode(cls, note: int, ctrl: int) -> "Row":
        """Decode a raw ``(note, ctrl)`` pair per the documented row encoding."""
        is_rest = note == c.NOTE_REST
        is_tie = note == c.NOTE_TIE
        # A tie is note==$80: bit7 set with a zero index; it is a hold, not a
        # key-on. A rest is note==$00. Any other value is a real note.
        trigger = bool(note & c.NOTE_TRIGGER) and not is_tie
        return cls(
            note=note,
            ctrl=ctrl,
            is_rest=is_rest,
            is_tie=is_tie,
            trigger=trigger,
            freq_index=note & c.NOTE_INDEX_MASK,
            instrument=ctrl & c.CTRL_INSTR_MASK,
            portamento=bool(ctrl & c.CTRL_PORTAMENTO),
            absolute=bool(ctrl & c.CTRL_ABSOLUTE),
            arp_enable=bool(ctrl & c.CTRL_ARP_ENABLE),
            instrument_base=not ctrl & c.CTRL_INSTR_BASE,
        )


@dataclass
class FilterTail:
    """The optional 8-byte global-filter tail of an instrument record."""

    vol_mode: int
    res_filt: int
    cutoff_hi: int
    up_dwell: int
    down_dwell: int
    step: int
    bound: int
    flags: int
    raw: bytes

    @classmethod
    def from_bytes(cls, tail: bytes) -> "FilterTail":
        """Decode an 8-byte filter tail."""
        return cls(
            vol_mode=tail[c.FT_VOL_MODE],
            res_filt=tail[c.FT_RES_FILT],
            cutoff_hi=tail[c.FT_CUTOFF_HI],
            up_dwell=tail[c.FT_UP_DWELL],
            down_dwell=tail[c.FT_DOWN_DWELL],
            step=tail[c.FT_STEP],
            bound=tail[c.FT_BOUND],
            flags=tail[c.FT_FLAGS],
            raw=bytes(tail),
        )


@dataclass
class Instrument:
    """A decoded 16-byte instrument record (+ optional filter tail)."""

    index: int
    ctrl_gate: int
    ad: int
    sr: int
    glide_rate_lo: int
    pw: int
    pw_up_dwell: int
    pw_down_dwell: int
    pw_step: int
    sustain_ctrl: int
    mode: int
    glide_enable: int
    glide_rate_hi: int
    vib_depth: int
    vib_period: int
    vib_delay: int
    detune: int
    raw: bytes
    filter: Optional[FilterTail] = None

    @property
    def pw_lo(self) -> int:
        """Seeded pulse-width low byte (``(pw & $0F) << 4``)."""
        return (self.pw & 0x0F) << 4

    @property
    def pw_hi(self) -> int:
        """Seeded pulse-width high nibble (``pw >> 4``)."""
        return self.pw >> 4

    @classmethod
    def from_record(cls, index: int, record: bytes) -> "Instrument":
        """Decode a record of at least 16 bytes (24 with a filter tail).

        The tail is present when the marker byte ``record[16]`` is not ``$FF``
        and the buffer carries the 8 trailing bytes.
        """
        tail = None
        if (
            len(record) >= c.FILTER_MARKER + c.FILTER_TAIL_LEN
            and record[c.FILTER_MARKER] != c.NO_FILTER_TAIL
        ):
            tail = FilterTail.from_bytes(
                record[c.FILTER_MARKER : c.FILTER_MARKER + c.FILTER_TAIL_LEN]
            )
        return cls(
            index=index,
            ctrl_gate=record[c.INST_CTRL_GATE],
            ad=record[c.INST_AD],
            sr=record[c.INST_SR],
            glide_rate_lo=record[c.INST_GLIDE_RATE_LO],
            pw=record[c.INST_PW],
            pw_up_dwell=record[c.INST_PW_UP_DWELL],
            pw_down_dwell=record[c.INST_PW_DOWN_DWELL],
            pw_step=record[c.INST_PW_STEP],
            sustain_ctrl=record[c.INST_SUSTAIN_CTRL],
            mode=record[c.INST_MODE],
            glide_enable=record[c.INST_GLIDE_ENABLE],
            glide_rate_hi=record[c.INST_GLIDE_RATE_HI],
            vib_depth=record[c.INST_VIB_DEPTH],
            vib_period=record[c.INST_VIB_PERIOD],
            vib_delay=record[c.INST_VIB_DELAY],
            detune=record[c.INST_DETUNE],
            raw=bytes(record[: c.INSTRUMENT_RECORD_LEN]),
            filter=tail,
        )


@dataclass
class Section:
    """One song-step: cadence + tempo + arp table + three voice streams."""

    index: int
    header_addr: int
    cia_latch: int
    tempo: int
    pattern_length: int
    vol_ramp: int
    base_volume: int
    arp_table: Tuple[int, ...]
    transpose: Tuple[int, int, int]
    voices: List[List[Row]]

    @property
    def cadence(self) -> int:
        """Play period in CPU cycles per call (``CIA latch + 1``)."""
        return self.cia_latch + 1


@dataclass
class Song:
    """A decoded Soundmonitor tune."""

    base: int
    player_anchor: int
    header: object = None
    sections: List[Section] = field(default_factory=list)
    instruments: List[Instrument] = field(default_factory=list)
    note_freq: Optional[NoteFreqTable] = None

    @property
    def cia_latch(self) -> Optional[int]:
        """CIA latch of the first section (the initial play cadence)."""
        return self.sections[0].cia_latch if self.sections else None
