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

import pathlib
import tempfile
from typing import Dict, List, Optional, Set, Tuple

from pysidtracker import (
    EmulatorUnavailable,
    KernalHost,
    KernalHostError,
    SidImage,
    find_code_all,
    read_bytes,
    run_init,
    sys_addr,
)
from pysidtracker import SidParseError as BaseSidParseError
from pysidtracker import locate_note_freq as _base_locate_note_freq
from pysidtracker.image import MEM_SIZE

from . import constants as c
from .errors import SidParseError
from .model import Instrument, NoteFreqTable, Row, Section, Song

# A packed bare PRG unpacks through a short BASIC SYS-stub chain; cap both.
_MAX_SYS_STAGES = 4
_STAGE_STEPS = 4_000_000


def _operand_targets(image: SidImage, opcode: int) -> Dict[int, int]:
    """Map every absolute operand of ``opcode`` in the image to its first site.

    An absolute-addressed 6502 instruction is ``opcode lo hi``; this returns
    ``{lo | hi<<8: site_address}`` for every occurrence, letting a relocatable
    player's own table/global addresses be recovered from its code operands.
    Scans the whole image via the shared masked code-fragment matcher.
    """
    pattern = f"{opcode:02X} {{target:w}}"
    targets: Dict[int, int] = {}
    for match in find_code_all(bytes(image.mem), pattern):
        targets.setdefault(match.captures["target"], match.addr)
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


def _null_ptr(header: int) -> bool:
    """True for a null section/voice pointer (an unused slot in the split table)."""
    return header == 0


def _section_ok(image: SidImage, base: int, index: int) -> bool:
    """Cheap validity probe: header + all voice stream pointers land in-image."""
    header = image.ptr(
        base + c.SECTION_HEADER_PTR_LO, base + c.SECTION_HEADER_PTR_HI, index
    )
    if _null_ptr(header) or not image.contains(header):
        return False
    patlen = image.peek(header + c.SH_PATLEN)
    if patlen == 0 or patlen > c.MAX_PATTERN_LEN:
        return False
    for voice in range(3):
        stream = image.ptr(
            base + c.VOICE_PTR_LO[voice], base + c.VOICE_PTR_HI[voice], index
        )
        if _null_ptr(stream) or not image.contains(stream):
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
    if _null_ptr(header) or not image.contains(header):
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
        if _null_ptr(stream) or not image.contains(stream):
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
        if not image.contains(addr):
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


def locate_note_freq(image: SidImage) -> Optional[NoteFreqTable]:
    """Locate and decode the note-frequency tables, or ``None``.

    Delegates to the shared relocation-tolerant paired-operand locator
    (:func:`pysidtracker.locate_note_freq`): it finds the ``LDA hi,X`` /
    ``LDA lo,X`` operand pair whose targets differ by a table length and whose
    hi table is a valid octave ramp. Returns ``None`` when no such pair exists.
    """
    return _base_locate_note_freq(image)


def _find_note_freq_addr(image: SidImage) -> Optional[int]:
    table = locate_note_freq(image)
    return table.addr if table is not None else None


def _run_init(image: SidImage) -> bool:
    """Emulate the tune's init so a packed/relocating player lands in memory.

    Returns ``True`` if init ran. A no-op (returns ``False``) for a bare ``.prg``
    with no header, or if the base emulator cannot run init. ``run_init`` raises
    the BASE :class:`SidParseError` (no init address) or
    :class:`EmulatorUnavailable` (``jennings`` missing); both are caught here so a
    tune that cannot be unpacked simply decodes from its directly loaded image.
    """
    if image.header is None:
        return False
    try:
        run_init(image)
        image.end = MEM_SIZE
        return True
    except (EmulatorUnavailable, BaseSidParseError):
        return False


def _decode_song(image: SidImage) -> Optional[Song]:
    """A :class:`Song` decoded from ``image``, or ``None`` when no section decodes."""
    anchor = find_fingerprint(image)
    if anchor is None:
        return None
    base, start, last, count = _plan(image)
    if count <= 0:
        return None
    sections = _decode_sections(image, base, start, last)
    if not sections:
        return None
    return Song(
        base=base,
        player_anchor=anchor,
        header=image.header,
        sections=sections,
        instruments=_decode_instruments(image, base, _instrument_indices(sections)),
        note_freq=locate_note_freq(image),
    )


def _ram_image(mem) -> SidImage:
    """A whole-64-KiB :class:`SidImage` view of unpacked ``mem`` for decoding."""
    return SidImage(bytearray(mem), 0x0000, MEM_SIZE, None, b"", b"")


def _safe_roms():
    """The C64 ROMs as ``{base: bytes}`` when cached, else ``None`` (no ROM calls)."""
    try:
        from pysidtracker import roms as _roms

        return _roms.load_roms() if _roms.roms_available() else None
    # No CPU core or ROMs present here; degrade to a plain-RAM decode.
    except Exception:  # pylint: disable=broad-except
        return None


def _host_dir() -> pathlib.Path:
    d = pathlib.Path(tempfile.gettempdir()) / "pysoundmonitor-khost"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unpack_mem(image: SidImage) -> Optional[bytearray]:
    """Run a bare packed PRG's BASIC SYS stubs; the final RAM, else ``None``.

    Drives the ``SYS`` stub chain on the :class:`~pysidtracker.KernalHost`,
    re-reading the next stub after each stage returns to BASIC, until the RAM
    decodes or no stub remains; KERNAL/BASIC ROMs load when cached.
    """
    if image.header is not None:
        return None
    load = image.load
    prg = image.container + image.image
    try:
        current = sys_addr(image, load)
    except KernalHostError:
        return None
    try:
        host = KernalHost(_host_dir(), roms=_safe_roms())
    # No CPU core available: degrade rather than raise.
    except Exception:  # pylint: disable=broad-except
        return None
    host.load_prg(prg)
    for _ in range(_MAX_SYS_STAGES):
        host.mpu.pc = current
        try:
            host.run(until=lambda h: h.mpu.pc >= 0xA000, max_steps=_STAGE_STEPS)
        except KernalHostError:
            pass
        if _decode_song(_ram_image(host.ram)) is not None:
            return bytearray(host.ram)
        try:
            current = sys_addr(host.ram, load)
        except KernalHostError:
            return None
    return None


def _copy_image(image: SidImage) -> SidImage:
    """A fresh :class:`SidImage` over a copy of ``mem`` (same header/geometry)."""
    return SidImage(
        bytearray(image.mem),
        image.load,
        image.end,
        image.header,
        image.image,
        image.container,
    )


def _materialize(image: SidImage) -> Optional[Tuple[SidImage, str]]:
    """A decodable-into-a-song copy of ``image`` and the stage that produced it.

    Tries the as-loaded RAM (``"direct"``), then an emulated init for relocated
    builds (``"relocated"``), then the KERNAL-host unpack of a bare packed PRG's
    SYS stubs (``"packed"``); works on a copy so the caller's image is never
    mutated. ``None`` when no stage decodes.
    """
    probe = _copy_image(image)
    if _decode_song(probe) is not None:
        return probe, "direct"
    if _run_init(probe) and _decode_song(probe) is not None:
        return probe, "relocated"
    if probe.header is None:
        ram = _unpack_mem(probe)
        if ram is not None and _decode_song(_ram_image(ram)) is not None:
            return _ram_image(ram), "packed"
    return None


def _materialized(image: SidImage) -> Optional[SidImage]:
    """The :func:`_materialize` image (init/unpacked), else ``None``."""
    result = _materialize(image)
    return result[0] if result is not None else None


def materialization(data: bytes) -> Tuple[SidImage, str]:
    """The materialised image of ``data`` and its stage (direct/relocated/packed).

    Raises :class:`SidParseError` when the image never decodes into a song.
    """
    result = _materialize(SidImage.from_bytes(data))
    if result is None:
        parse(data)  # raises the specific SidParseError
        raise SidParseError("Soundmonitor image did not materialise into a song")
    return result


def parse(data: bytes) -> Song:
    """Decode raw ``.sid``/``.prg`` ``data`` into a :class:`Song`.

    Decodes the as-loaded image, an emulated init, or the KERNAL-host unpack of a
    bare packed PRG's SYS stubs. Never returns an empty :class:`Song`: an image
    that cannot materialise into a song raises :class:`SidParseError`.
    """
    image = SidImage.from_bytes(data)
    materialized = _materialized(image)
    song = _decode_song(materialized) if materialized is not None else None
    if song is None:
        if find_fingerprint(image) is not None:
            raise SidParseError(
                "Soundmonitor fingerprint found but no section decodes in the image"
            )
        raise SidParseError("Soundmonitor engine fingerprint not found in image")
    return song


def read(src) -> Song:
    """Read ``src`` (path, ``bytes``, or file-like) and :func:`parse` it."""
    return parse(read_bytes(src))
