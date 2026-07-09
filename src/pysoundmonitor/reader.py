"""Decode a Soundmonitor ``.sid``/``.prg`` image into a :class:`Song`.

The player is relocatable and the SID-header addresses are not a reliable
locator, so the song data is found *relocation-tolerantly*: a small hardware-
register fingerprint identifies the CIA section loader, and the data-region base
page is recovered from the loader's own table-indexing operands (falling back to
the image load address). The documented structures are then decoded into the
model. This is a song-data reader, not a playback engine.
"""

from __future__ import annotations

from typing import List, Optional, Set

import numpy as np

from pysidtracker import SidImage, read_bytes

from . import constants as c
from .errors import SidParseError
from .model import Instrument, NoteFreqTable, Row, Section, Song


def find_fingerprint(image: SidImage) -> Optional[int]:
    """Address of the CIA-timer engine fingerprint, or ``None``.

    Matches the relocation-invariant ``STA $DC04 ... CMP #$06 ... STA $DC05``
    section-loader sequence (writes to fixed hardware registers), so it does not
    depend on where the player was relocated to.
    """
    span = len(c.STA_DC04) + c.FINGERPRINT_WINDOW
    for pos in image.find_all(c.STA_DC04):
        window = image.slice(pos, min(span, len(image.mem) - pos))
        hi = window.find(c.STA_DC05, len(c.STA_DC04))
        if hi < 0:
            continue
        guard = window.find(c.CMP_06, len(c.STA_DC04))
        if 0 <= guard < hi:
            return pos
    return None


def _recover_base(image: SidImage, anchor: int) -> int:
    """Recover the data-region base address from the loader's table operands.

    The section loader reads the split pointer tables with ``LDA $pp00,X``; the
    data region is the run of page-aligned tables, so the base page is the
    smallest ``$pp00`` operand whose table geometry (voice + section-header lo
    tables) is all present. Falls back to the image load address.
    """
    start = max(image.load, anchor - c.LOADER_SCAN_BACK)
    scan = image.slice(start, anchor - start)
    pages: Set[int] = set()
    idx = scan.find(bytes((c.LDA_ABSX, 0x00)))
    while idx >= 0:
        if idx + 2 < len(scan):
            pages.add(scan[idx + 2])
        idx = scan.find(bytes((c.LDA_ABSX, 0x00)), idx + 1)
    probe_pages = {off >> 8 for off in c.BASE_PROBE_OFFSETS}
    for page in sorted(pages):
        if all((page + rel) in pages for rel in probe_pages):
            return page << 8
    return image.load & 0xFF00


def _in_image(image: SidImage, addr: int) -> bool:
    return image.load <= addr < image.end


def _decode_stream(image: SidImage, addr: int, rows: int) -> List[Row]:
    out: List[Row] = []
    rows = min(rows, c.MAX_PATTERN_LEN)
    for row in range(rows):
        base = addr + row * c.ROW_LEN
        out.append(Row.decode(image.peek(base), image.peek(base + 1)))
    return out


def _decode_section(image: SidImage, base: int, index: int) -> Optional[Section]:
    header = image.ptr(
        base + c.SECTION_HEADER_PTR_LO, base + c.SECTION_HEADER_PTR_HI, index
    )
    if not _in_image(image, header):
        return None
    latch = image.peek(header + c.SH_LATCH_LO) | (
        image.peek(header + c.SH_LATCH_HI) << 8
    )
    patlen = image.peek(header + c.SH_PATLEN)
    arp = tuple(image.peek(header + c.SH_ARP_TABLE + i) for i in range(c.ARP_TABLE_LEN))
    voices: List[List[Row]] = []
    transpose = []
    for voice in range(3):
        stream = image.ptr(
            base + c.VOICE_PTR_LO[voice], base + c.VOICE_PTR_HI[voice], index
        )
        if not _in_image(image, stream):
            return None
        voices.append(_decode_stream(image, stream, patlen))
        transpose.append(image.peek(base + c.VOICE_XPOSE[voice] + index))
    return Section(
        index=index,
        header_addr=header,
        cia_latch=latch,
        tempo=image.peek(header + c.SH_TEMPO),
        pattern_length=patlen,
        vol_ramp=image.peek(header + c.SH_VOL_RAMP),
        base_volume=image.peek(header + c.SH_BASE_VOL),
        arp_table=arp,
        transpose=(transpose[0], transpose[1], transpose[2]),
        voices=voices,
    )


def _decode_sections(image: SidImage, base: int) -> List[Section]:
    sections: List[Section] = []
    for index in range(c.MAX_SECTIONS):
        section = _decode_section(image, base, index)
        if section is None:
            break
        sections.append(section)
    return sections


def _instrument_indices(sections: List[Section]) -> List[int]:
    used: Set[int] = set()
    for section in sections:
        for voice in section.voices:
            for row in voice:
                if row.trigger:
                    used.add(row.instrument)
    return sorted(used)


def _decode_instruments(
    image: SidImage, base: int, indices: List[int]
) -> List[Instrument]:
    out: List[Instrument] = []
    table = base + c.INSTRUMENTS
    for index in indices:
        addr = table + index * c.INSTRUMENT_STRIDE
        if not _in_image(image, addr):
            continue
        record = image.slice(addr, c.INSTRUMENT_STRIDE)
        out.append(Instrument.from_record(index, record))
    return out


def decode_note_freq(image: SidImage, addr: int) -> NoteFreqTable:
    """Decode the parallel hi/lo note-frequency tables at ``addr``."""
    hi = list(image.slice(addr, c.NOTE_FREQ_LEN))
    lo = list(image.slice(addr + c.NOTE_FREQ_LEN, c.NOTE_FREQ_LEN))
    return NoteFreqTable(hi=hi, lo=lo, addr=addr)


def locate_note_freq(image: SidImage) -> Optional[NoteFreqTable]:
    """Locate the note-frequency tables by their octave-ramp content signature.

    The hi table is one entry per semitone climbing monotonically (doubling per
    octave); the lo table immediately follows it. Returns ``None`` if no region
    matches, in which case the model simply omits the tables.
    """
    mem = np.frombuffer(bytes(image.mem), dtype=np.uint8).astype(np.int16)
    length = c.NOTE_FREQ_LEN
    last = len(mem) - 2 * length
    if last < image.load:
        return None
    diffs = np.diff(mem)
    nondec = diffs >= 0  # True where mem is non-decreasing
    for addr in range(image.load, last):
        if mem[addr] == 0 or mem[addr] > c.NOTE_FREQ_HI_START_MAX:
            continue
        if mem[addr + length - 1] < c.NOTE_FREQ_HI_END_MIN:
            continue
        span = nondec[addr : addr + length - 1]
        if not span.all():
            continue
        if int((diffs[addr : addr + length - 1] > 0).sum()) < c.NOTE_FREQ_MIN_STEPS:
            continue
        return decode_note_freq(image, addr)
    return None


def parse(data: bytes) -> Song:
    """Decode raw ``.sid``/``.prg`` ``data`` into a :class:`Song`.

    Raises :class:`SidParseError` if the Soundmonitor engine fingerprint is not
    present in the loaded image (e.g. a packed tune whose player only appears
    after init; use :meth:`SoundMonitorSidParser.detect` for that case).
    """
    image = SidImage.from_bytes(data)
    anchor = find_fingerprint(image)
    if anchor is None:
        raise SidParseError("Soundmonitor engine fingerprint not found in image")
    base = _recover_base(image, anchor)
    sections = _decode_sections(image, base)
    instruments = _decode_instruments(image, base, _instrument_indices(sections))
    note_freq = locate_note_freq(image)
    return Song(
        base=base,
        player_anchor=anchor,
        header=image.header,
        sections=sections,
        instruments=instruments,
        note_freq=note_freq,
    )


def read(src) -> Song:
    """Read ``src`` (path, ``bytes``, or file-like) and :func:`parse` it."""
    return parse(read_bytes(src))
