"""Decode a Soundmonitor ``.sid``/``.prg`` image into a :class:`Song`.

The player is relocatable and the SID-header addresses are not a reliable
locator, so the song data is found *relocation- and build-tolerantly* from the
player's own code operands: the engine is recognised by its CIA section-loader
fingerprint or its note-frequency tables, the data-region base page comes from
the loader's table-indexing operands, the section bounds from the section-advance
``CPX``/``LDX`` operands, and the note-freq tables from the paired ``LDA`` reads.
A packed/relocating tune is unpacked by emulating its init first. The stages
(as loaded, after init, after the BASIC ``SYS`` stub chain) are
:func:`pysidtracker.locate` strategies: each candidate layout is decoded once, the
winning stage is recorded as the :class:`Song`'s ``variant`` facets, and a failure
raises with the full rejection log. This is a song-data reader, not a playback
engine.
"""

from __future__ import annotations

import pathlib
import tempfile
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

from pysidtracker import (
    KernalHost,
    KernalHostError,
    Located,
    Rejection,
    SidImage,
    SitePattern,
    Strategy,
    find_code_all,
    locate,
    read_bytes,
    sys_addr,
)
from pysidtracker import locate_note_freq as _base_locate_note_freq
from pysidtracker.detect import Detection, PlayroutineKind
from pysidtracker.facets import Entry, Variant, detected, inferred
from pysidtracker.image import MEM_SIZE
from pysidtracker.locate import AFTER_INIT, STATIC

from . import constants as c
from .grammar import (
    GRAMMAR,
    INSTRUMENT,
    PATTERN,
    SECTION_HEADER,
    SECTION_HEADER_LEN,
    TABLES_LEN,
    TRACK_TABLES,
    TrackTables,
)
from .errors import SidParseError
from .model import Instrument, NoteFreqTable, Row, Section, Song
from .variant import (
    BASE_LOAD_PAGE,
    BOUNDS_DEFAULT,
    ENGINE_CIA,
    ENGINE_NOTE_FREQ,
    SOURCE_OPERAND,
    STAGE_AFTER_INIT,
    STAGE_STATIC,
    STAGE_SYS_UNPACK,
    make_variant,
)

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


# ``STA $DC04 ... CMP #$06 ... STA $DC05`` within the fingerprint window: the gaps
# are bounded here and the whole match against ``FINGERPRINT_WINDOW`` below.
_CIA_FINGERPRINT = SitePattern(
    f"{c.STA_DC04.hex(' ')} ??{{0,{c.FINGERPRINT_WINDOW - 5}}} "
    f"{c.CMP_06.hex(' ')} ??{{0,{c.FINGERPRINT_WINDOW - 5}}} {c.STA_DC05.hex(' ')}"
)


def find_cia_fingerprint(image: SidImage) -> Optional[int]:
    """Address of the CIA-timer section-loader fingerprint, or ``None``.

    Matches the relocation-invariant ``STA $DC04 ... CMP #$06 ... STA $DC05``
    sequence (writes to fixed hardware registers). Present in the CIA-timed
    builds ($C000 Hulsbeck / $1000); absent in the fixed-cadence builds, which
    are recognised by their note-freq tables instead.
    """
    span = len(c.STA_DC04) + c.FINGERPRINT_WINDOW
    for match in _CIA_FINGERPRINT.find_all(image):
        if match.length <= span:
            return match.addr
    return None


def engine_entry(image: SidImage) -> Optional[Entry]:
    """The ``engine`` facet of ``image`` (value and anchor source), or ``None``.

    The CIA section-loader fingerprint when present (the CIA-timed builds), else
    the note-frequency hi-table address (the fixed-cadence builds carry the same
    octave-ramp tables, read via paired ``LDA`` operands).
    """
    cia = find_cia_fingerprint(image)
    if cia is not None:
        return Entry(ENGINE_CIA, detected(cia))
    table = locate_note_freq(image)
    if table is not None:
        return Entry(ENGINE_NOTE_FREQ, detected(table.addr))
    return None


def find_fingerprint(image: SidImage) -> Optional[int]:
    """Address of the Soundmonitor engine, or ``None`` -- build-tolerant.

    Either anchor (see :func:`engine_entry`) is a truthy relocation-tolerant
    anchor for :meth:`detect`.
    """
    engine = engine_entry(image)
    return engine.source.addr if engine is not None else None


def _base_candidates(image: SidImage) -> List[Tuple[int, int]]:
    """Candidate ``(base, site)`` data-region bases from the loader's table operands.

    The section loader reads the split pointer tables with ``LDA $pp00,X``; a
    base page is any ``$pp00`` operand whose voice/section-header lo tables are
    all present, ``site`` being the first such read. A relocated image can
    present a run of consecutive candidate pages; the caller disambiguates by
    which one actually decodes sections.
    """
    targets = _operand_targets(image, c.LDA_ABSX)
    pages = {target >> 8 for target in targets if target & 0xFF == 0x00}
    probe_pages = {off >> 8 for off in c.BASE_PROBE_OFFSETS}
    return [
        (page << 8, targets[page << 8])
        for page in sorted(pages)
        if all((page + rel) in pages for rel in probe_pages)
    ]


def _bounds_candidates(image: SidImage) -> List[Tuple[int, int, int]]:
    """Candidate ``(sec_start, sec_last, site)`` from the section-advance code.

    ``SUB_c940`` does ``CPX sec_last`` then, on wrap, ``LDX sec_start``; the two
    globals are adjacent (``sec_last`` at ``A``, ``sec_start`` at ``A+1``), so a
    ``CPX $A`` whose neighbour ``$A+1`` is an ``LDX`` operand pins the bounds.
    ``sec_start`` may exceed ``sec_last`` (the section counter wraps mod 256).
    """
    cpx = _operand_targets(image, c.CPX_ABS)
    ldx = _operand_targets(image, c.LDX_ABS)
    out: List[Tuple[int, int, int]] = []
    for last_addr in sorted(cpx):
        if last_addr + 1 in ldx:
            out.append(
                (image.peek(last_addr + 1), image.peek(last_addr), cpx[last_addr])
            )
    return out


def _section_span(start: int, last: int) -> List[int]:
    """Section indices from ``start`` up to ``last`` inclusive, wrapping mod 256."""
    count = ((last - start) & 0xFF) + 1
    return [(start + k) & 0xFF for k in range(count)]


def _null_ptr(header: int) -> bool:
    """True for a null section/voice pointer (an unused slot in the split table)."""
    return header == 0


def _tables(image: SidImage, base: int) -> TrackTables:
    """The track tables at data ``base``."""
    return GRAMMAR.read(image, base, TRACK_TABLES, size=TABLES_LEN).model


def _section_header(image: SidImage, tables: TrackTables, index: int):
    """Section ``index``'s header and address, or ``None`` if it is not a section."""
    addr = tables.header_ptr(index)
    if _null_ptr(addr) or not image.contains(addr):
        return None
    try:
        header = GRAMMAR.read(image, addr, SECTION_HEADER, size=SECTION_HEADER_LEN)
    except SidParseError:
        return None
    return header.model, addr


def _stream_ptrs(image: SidImage, tables: TrackTables, index: int):
    """The three voices' stream addresses of section ``index``, or ``None``."""
    ptrs = [tables.stream_ptr(voice, index) for voice in range(3)]
    if any(_null_ptr(ptr) or not image.contains(ptr) for ptr in ptrs):
        return None
    return ptrs


def _section_ok(image: SidImage, tables: TrackTables, index: int) -> bool:
    """Cheap validity probe: header + all voice stream pointers land in-image."""
    return (
        _section_header(image, tables, index) is not None
        and _stream_ptrs(image, tables, index) is not None
    )


def _count_sections(image: SidImage, base: int, start: int, last: int) -> int:
    """Valid sections decodable from ``start`` to ``last`` (stops at first bad)."""
    tables = _tables(image, base)
    count = 0
    for index in _section_span(start, last):
        if not _section_ok(image, tables, index):
            break
        count += 1
    return count


class _Layout(NamedTuple):
    """One candidate layout: data base + section bounds and where each came from."""

    anchor: int
    base: int
    start: int
    last: int
    count: int
    base_site: Optional[int]
    bounds_site: Optional[int]


def _layouts(image: SidImage) -> List[_Layout]:
    """Candidate layouts of ``image``, the one decoding the most sections first.

    Enumerates the operand-recovered base and section-bound candidates (falling
    back to the load page and a full walk) and ranks the combinations by the
    bounded run of valid sections each yields -- robust to base ambiguity and to
    the relocation-dependent player globals that a blind walk would run into
    garbage. Combinations decoding no section are dropped.
    """
    anchor = find_fingerprint(image)
    if anchor is None:
        raise SidParseError("Soundmonitor engine fingerprint not found in image")
    bases = _base_candidates(image) or [(image.load & 0xFF00, None)]
    bounds = _bounds_candidates(image) or [(0, c.MAX_SECTIONS - 1, None)]
    layouts = [
        _Layout(anchor, base, start, last, count, base_site, bounds_site)
        for base, base_site in bases
        for start, last, bounds_site in bounds
        for count in (_count_sections(image, base, start, last),)
        if count > 0
    ]
    if not layouts:
        raise SidParseError("Soundmonitor fingerprint found but no section decodes")
    return sorted(layouts, key=lambda layout: -layout.count)


def _decode_stream(image: SidImage, addr: int, rows: int) -> List[Row]:
    stream = GRAMMAR.read(image, addr, PATTERN, size=rows * c.ROW_LEN).model
    return [Row.decode(note, ctrl) for note, ctrl in stream.rows]


def _decode_section(
    image: SidImage, tables: TrackTables, index: int
) -> Optional[Section]:
    found = _section_header(image, tables, index)
    ptrs = _stream_ptrs(image, tables, index)
    if found is None or ptrs is None:
        return None
    header, addr = found
    return Section(
        index=index,
        header_addr=addr,
        cia_latch=header.latch,
        tempo=header.tempo,
        pattern_length=header.patlen,
        vol_ramp=header.vol_ramp,
        base_volume=header.base_vol,
        arp_table=header.arp,
        transpose=tuple(tables.transpose(voice, index) for voice in range(3)),
        voices=[_decode_stream(image, ptr, header.patlen) for ptr in ptrs],
    )


def _decode_sections(
    image: SidImage, base: int, start: int, last: int
) -> List[Section]:
    """Decode the song's sections over ``start..last`` (inclusive, wrapping).

    Stops at the first invalid section. ``base`` and the ``(start, last)`` bounds
    come from :func:`_layouts`, which recovers them from the player's own operands
    (so a bounded, correct set is decoded rather than walking a relocation-
    dependent table into garbage and hitting the 256 cap).
    """
    sections: List[Section] = []
    tables = _tables(image, base)
    for index in _section_span(start, last):
        section = _decode_section(image, tables, index)
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
        record = GRAMMAR.read(image, addr, INSTRUMENT, size=c.INSTRUMENT_STRIDE).model
        out.append(Instrument.from_record(index, record.to_bytes()))
    return out


def locate_note_freq(image: SidImage) -> Optional[NoteFreqTable]:
    """Locate and decode the note-frequency tables, or ``None``.

    Delegates to the shared relocation-tolerant paired-operand locator
    (:func:`pysidtracker.locate_note_freq`): it finds the ``LDA hi,X`` /
    ``LDA lo,X`` operand pair whose targets differ by a table length and whose
    hi table is a valid octave ramp. Returns ``None`` when no such pair exists.
    """
    return _base_locate_note_freq(image)


def _decode_layout(image: SidImage, layout: _Layout) -> Song:
    """The :class:`Song` decoded from ``image`` at ``layout``; rejects an empty one."""
    sections = _decode_sections(image, layout.base, layout.start, layout.last)
    if not sections:
        raise SidParseError("no section decodes")
    return Song(
        base=layout.base,
        player_anchor=layout.anchor,
        header=image.header,
        sections=sections,
        instruments=_decode_instruments(
            image, layout.base, _instrument_indices(sections)
        ),
        note_freq=locate_note_freq(image),
    )


# The stages are ordered: as loaded, then after init (relocated builds); the SYS
# unpack of a bare packed PRG is driven by :func:`_sys_unpack`.
_STRATEGIES = (
    Strategy(STAGE_STATIC, STATIC, _decode_layout, _layouts),
    Strategy(STAGE_AFTER_INIT, AFTER_INIT, _decode_layout, _layouts),
)
_SYS_UNPACK = Strategy(STAGE_SYS_UNPACK, STATIC, _decode_layout, _layouts)


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


def _sys_unpack(image: SidImage, failed: Located) -> Located:
    """Run a bare packed PRG's BASIC SYS stubs; locate the song in the final RAM.

    Drives the ``SYS`` stub chain on the :class:`~pysidtracker.KernalHost`,
    re-reading the next stub after each stage returns to BASIC, until the RAM
    decodes or no stub remains; KERNAL/BASIC ROMs load when cached. Returns the
    accepted :class:`Located`, else ``failed`` with the reasons appended to its
    rejection log. The whole RAM is decoded (the song data may sit below the
    unpacked code).
    """
    load = image.load
    try:
        current = sys_addr(image, load)
        host = KernalHost(_host_dir(), roms=_safe_roms())
    # No SYS stub, or no CPU core available: degrade rather than raise.
    except Exception as exc:  # pylint: disable=broad-except
        failed.rejections.append(Rejection(STAGE_SYS_UNPACK, None, str(exc)))
        return failed
    host.load_prg(image.container + image.image)
    for _ in range(_MAX_SYS_STAGES):
        host.mpu.pc = current
        try:
            host.run(until=lambda h: h.mpu.pc >= 0xA000, max_steps=_STAGE_STEPS)
        except KernalHostError:
            pass
        found = locate(_ram_image(host.ram), (_SYS_UNPACK,), init=False)
        failed.rejections.extend(found.rejections)
        if found.ok:
            found.stage = STAGE_SYS_UNPACK
            found.rejections = failed.rejections
            found.detection = Detection(
                PlayroutineKind.PACKED, ran_init=False, anchor=found.candidate.anchor
            )
            return found
        try:
            current = sys_addr(host.ram, load)
        except KernalHostError as exc:
            failed.rejections.append(Rejection(STAGE_SYS_UNPACK, None, str(exc)))
            return failed
    return failed


def _variant(located: Located) -> Variant:
    """The :class:`Variant` of an accepted ``located``: engine, sources, stage."""
    layout = located.candidate
    engine = engine_entry(located.image)
    return make_variant(
        engine=engine,
        base_source=(
            Entry(SOURCE_OPERAND, detected(layout.base_site))
            if layout.base_site is not None
            else Entry(BASE_LOAD_PAGE, inferred("image load page"))
        ),
        bounds_source=(
            Entry(SOURCE_OPERAND, detected(layout.bounds_site))
            if layout.bounds_site is not None
            else Entry(BOUNDS_DEFAULT, inferred("full section walk"))
        ),
        materialisation=Entry(located.strategy, inferred(f"stage {located.stage}")),
    )


def locate_song(image: SidImage) -> Located:
    """Locate and decode the song of ``image`` once; ``Located.layout`` is the Song.

    Tries the as-loaded RAM, then an emulated init (relocated builds), then the
    KERNAL-host unpack of a bare packed PRG's SYS stubs; works on copies so the
    caller's image is never mutated. An accepted song carries its ``variant``;
    a failure carries the full rejection log (see :meth:`Located.require`).
    """
    located = locate(image, _STRATEGIES)
    if not located.ok and image.header is None:
        located = _sys_unpack(image, located)
    if located.ok:
        located.layout.variant = _variant(located)
    return located


def parse(data: bytes) -> Song:
    """Decode raw ``.sid``/``.prg`` ``data`` into a :class:`Song`.

    Decodes the as-loaded image, an emulated init, or the KERNAL-host unpack of a
    bare packed PRG's SYS stubs. Never returns an empty :class:`Song`: an image
    that cannot materialise into a song raises :class:`SidParseError` carrying
    every rejected strategy and why.
    """
    return locate_song(SidImage.from_bytes(data)).require(SidParseError)


def read(src) -> Song:
    """Read ``src`` (path, ``bytes``, or file-like) and :func:`parse` it."""
    return parse(read_bytes(src))
