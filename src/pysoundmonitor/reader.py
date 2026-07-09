"""Decode a Soundmonitor ``.sid``/``.prg`` image into a :class:`Song`.

The player is relocatable and the SID-header addresses are not a reliable
locator, so the song data is found *relocation- and build-tolerantly* from the
player's own code operands: the engine is recognised by its CIA section-loader
fingerprint or its note-frequency tables, the data-region base page comes from
the loader's table-indexing operands, the section bounds from the section-advance
``CPX``/``LDX`` operands, and the note-freq tables from the paired ``LDA`` reads.
A packed/relocating tune is unpacked by emulating its init first. The documented
structures are then decoded into the model; this is a song-data reader, not a
playback engine.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

import numpy as np

from pysidtracker import SidImage, read_bytes

from . import constants as c
from .errors import SidParseError
from .model import Instrument, NoteFreqTable, Row, Section, Song


def _operand_targets(image: SidImage, opcode: int) -> Dict[int, int]:
    """Map every absolute operand of ``opcode`` in the image to its first site.

    An absolute-addressed 6502 instruction is ``opcode lo hi``; this returns
    ``{lo | hi<<8: site_address}`` for every occurrence, letting a relocatable
    player's own table/global addresses be recovered from its code operands.
    """
    mem = np.frombuffer(bytes(image.mem), dtype=np.uint8)
    sites = np.nonzero(mem[:-2] == opcode)[0]
    targets: Dict[int, int] = {}
    for site in sites.tolist():
        target = int(mem[site + 1]) | (int(mem[site + 2]) << 8)
        targets.setdefault(target, site)
    return targets


def find_cia_fingerprint(image: SidImage) -> Optional[int]:
    """Address of the CIA-timer section-loader fingerprint, or ``None``.

    Matches the relocation-invariant ``STA $DC04 ... CMP #$06 ... STA $DC05``
    sequence (writes to fixed hardware registers). Present in the CIA-timed
    builds ($C000 Hulsbeck / $1000); absent in the fixed-cadence builds, which
    are recognised by their note-freq tables instead.
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


def find_fingerprint(image: SidImage) -> Optional[int]:
    """Address of the Soundmonitor engine, or ``None`` -- build-tolerant.

    Returns the CIA section-loader fingerprint when present (the CIA-timed
    builds), else the note-frequency hi-table address (the fixed-cadence
    builds carry the same octave-ramp tables, read via paired ``LDA`` operands).
    Either is a truthy relocation-tolerant anchor for :meth:`detect`.
    """
    cia = find_cia_fingerprint(image)
    if cia is not None:
        return cia
    freq = _find_note_freq_addr(image)
    if freq is not None:
        return freq
    return None


def _base_candidates(image: SidImage) -> List[int]:
    """Candidate data-region base addresses from the loader's table operands.

    The section loader reads the split pointer tables with ``LDA $pp00,X``; a
    base page is any ``$pp00`` operand whose voice/section-header lo tables are
    all present. A relocated image can present a run of consecutive candidate
    pages; the caller disambiguates by which one actually decodes sections.
    """
    pages: Set[int] = set()
    for target in _operand_targets(image, c.LDA_ABSX):
        if target & 0xFF == 0x00:
            pages.add(target >> 8)
    probe_pages = {off >> 8 for off in c.BASE_PROBE_OFFSETS}
    return [
        page << 8
        for page in sorted(pages)
        if all((page + rel) in pages for rel in probe_pages)
    ]


def _bounds_candidates(image: SidImage) -> List[tuple]:
    """Candidate ``(sec_start, sec_last)`` pairs from the section-advance code.

    ``SUB_c940`` does ``CPX sec_last`` then, on wrap, ``LDX sec_start``; the two
    globals are adjacent (``sec_last`` at ``A``, ``sec_start`` at ``A+1``), so a
    ``CPX $A`` whose neighbour ``$A+1`` is an ``LDX`` operand pins the bounds.
    ``sec_start`` may exceed ``sec_last`` (the section counter wraps mod 256).
    """
    cpx = _operand_targets(image, c.CPX_ABS)
    ldx = _operand_targets(image, c.LDX_ABS)
    out: List[tuple] = []
    for last_addr in sorted(cpx):
        if last_addr + 1 in ldx:
            out.append((image.peek(last_addr + 1), image.peek(last_addr)))
    return out


def _section_span(start: int, last: int) -> List[int]:
    """Section indices from ``start`` up to ``last`` inclusive, wrapping mod 256."""
    count = ((last - start) & 0xFF) + 1
    return [(start + k) & 0xFF for k in range(count)]


def _section_ok(image: SidImage, base: int, index: int) -> bool:
    """Cheap validity probe: header + all voice stream pointers land in-image."""
    header = image.ptr(
        base + c.SECTION_HEADER_PTR_LO, base + c.SECTION_HEADER_PTR_HI, index
    )
    if not _in_image(image, header):
        return False
    patlen = image.peek(header + c.SH_PATLEN)
    if patlen == 0 or patlen > c.MAX_PATTERN_LEN:
        return False
    for voice in range(3):
        stream = image.ptr(
            base + c.VOICE_PTR_LO[voice], base + c.VOICE_PTR_HI[voice], index
        )
        if not _in_image(image, stream):
            return False
    return True


def _count_sections(image: SidImage, base: int, start: int, last: int) -> int:
    """Valid sections decodable from ``start`` to ``last`` (stops at first bad)."""
    count = 0
    for index in _section_span(start, last):
        if not _section_ok(image, base, index):
            break
        count += 1
    return count


def _plan(image: SidImage) -> tuple:
    """Choose ``(base, start, last, count)`` that decodes the most valid sections.

    Enumerates the operand-recovered base and section-bound candidates (falling
    back to the load page and a full walk) and picks the combination yielding the
    largest bounded run of valid sections -- robust to base ambiguity and to the
    relocation-dependent player globals that a blind walk would run into garbage.
    """
    bases = _base_candidates(image) or [image.load & 0xFF00]
    bounds = _bounds_candidates(image) or [(0, c.MAX_SECTIONS - 1)]
    best = (bases[0], bounds[0][0], bounds[0][1], -1)
    for base in bases:
        for start, last in bounds:
            count = _count_sections(image, base, start, last)
            if count > best[3]:
                best = (base, start, last, count)
    return best


def _recover_base(image: SidImage) -> int:
    """The chosen data-region base address (see :func:`_plan`)."""
    return _plan(image)[0]


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
    patlen = image.peek(header + c.SH_PATLEN)
    if patlen == 0 or patlen > c.MAX_PATTERN_LEN:
        return None
    latch = image.peek(header + c.SH_LATCH_LO) | (
        image.peek(header + c.SH_LATCH_HI) << 8
    )
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


def _decode_sections(
    image: SidImage, base: int, start: int, last: int
) -> List[Section]:
    """Decode the song's sections over ``start..last`` (inclusive, wrapping).

    Stops at the first invalid section. ``base`` and the ``(start, last)`` bounds
    come from :func:`_plan`, which recovers them from the player's own operands
    (so a bounded, correct set is decoded rather than walking a relocation-
    dependent table into garbage and hitting the 256 cap).
    """
    sections: List[Section] = []
    for index in _section_span(start, last):
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


def decode_note_freq(
    image: SidImage, addr: int, length: int = c.NOTE_FREQ_LEN
) -> NoteFreqTable:
    """Decode the parallel hi/lo note-frequency tables at ``addr``."""
    hi = list(image.slice(addr, length))
    lo = list(image.slice(addr + length, length))
    return NoteFreqTable(hi=hi, lo=lo, addr=addr)


def _valid_freq_hi(image: SidImage, addr: int, length: int) -> bool:
    """Is ``[addr, addr+length)`` a valid note-freq hi octave ramp?"""
    if addr < 0 or addr + length > len(image.mem):
        return False
    hi = np.frombuffer(image.slice(addr, length), dtype=np.uint8).astype(np.int16)
    if hi[0] == 0 or hi[0] > c.NOTE_FREQ_HI_START_MAX:
        return False
    if hi[-1] < c.NOTE_FREQ_HI_END_MIN:
        return False
    diffs = np.diff(hi)
    if not bool((diffs >= 0).all()):
        return False
    return int((diffs > 0).sum()) >= c.NOTE_FREQ_MIN_STEPS


def _find_note_freq(image: SidImage) -> Optional[tuple]:
    """Locate the note-freq tables from the player's paired ``LDA`` operands.

    The player reads ``LDA NoteFreqHi,X`` and ``LDA NoteFreqLo,X``; the two
    absolute-indexed operands abut (``lo = hi + length``), so a pair of
    ``LDA abs,X`` targets that differ by a candidate table length and whose hi
    table is a valid octave ramp pins the tables regardless of relocation.
    Returns ``(hi_addr, length)`` or ``None``.
    """
    targets = set(_operand_targets(image, c.LDA_ABSX))
    for hi_addr in sorted(targets):
        for length in c.NOTE_FREQ_LENGTHS:
            if hi_addr + length not in targets:
                continue
            if _valid_freq_hi(image, hi_addr, length):
                return (hi_addr, length)
    return None


def _find_note_freq_addr(image: SidImage) -> Optional[int]:
    found = _find_note_freq(image)
    return found[0] if found is not None else None


def locate_note_freq(image: SidImage) -> Optional[NoteFreqTable]:
    """Locate and decode the note-frequency tables, or ``None``.

    Uses the relocation-tolerant paired-operand locator (:func:`_find_note_freq`);
    returns ``None`` only when the player carries no such table pair.
    """
    found = _find_note_freq(image)
    if found is None:
        return None
    hi_addr, length = found
    return decode_note_freq(image, hi_addr, length)


def _run_init(image: SidImage) -> bool:
    """Emulate the tune's init so a packed/relocating player lands in memory.

    Returns ``True`` if init ran. A no-op (returns ``False``) for a bare ``.prg``
    with no header, or when the optional emulator dependency is unavailable.
    """
    if image.header is None:
        return False
    try:
        from pysidtracker import run_init
        from pysidtracker.image import MEM_SIZE

        run_init(image)
        image.end = MEM_SIZE
        return True
    except SidParseError:
        return False


def parse(data: bytes) -> Song:
    """Decode raw ``.sid``/``.prg`` ``data`` into a :class:`Song`.

    Recognises the engine build-tolerantly (CIA-timed or fixed-cadence). A
    packed/relocating tune only exposes its player and song tables after its init
    routine has moved/decompressed them, so init is emulated and decoding retried
    whenever the engine is absent or no section decodes in the directly loaded
    image. Raises :class:`SidParseError` if the engine is not found either way.
    """
    image = SidImage.from_bytes(data)
    anchor = find_fingerprint(image)
    plan = _plan(image) if anchor is not None else None
    if (anchor is None or plan[3] <= 0) and _run_init(image):
        retry = find_fingerprint(image)
        if retry is not None:
            anchor = retry
        plan = _plan(image)
    if anchor is None:
        raise SidParseError("Soundmonitor engine fingerprint not found in image")
    base, start, last, _count = plan
    sections = _decode_sections(image, base, start, last)
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
