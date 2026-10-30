"""The Soundmonitor playroutine as a ``pysidtracker.playspec`` spec.

``specs/soundmonitor.play`` states the play call; :func:`binding` resolves its
``layout`` from the reader's discovery plus the cells the play code's own
operands name.
"""

from importlib import resources

from pysidtracker import CodePattern, SidImage, find_code_all
from pysidtracker.playspec import parse as parse_spec

from . import constants as c
from .reader import _layouts, locate_note_freq

SPEC = parse_spec(
    resources.files(__package__).joinpath("specs/soundmonitor.play").read_text()
)

ENTRIES = (
    CodePattern("20 {swap:w} AD {flag:w} F0 03 20 {init:w} AE {on:w} D0"),
    CodePattern("AD {flag:w} F0 03 20 {init:w} AE {on:w} D0"),
)
SECTIONS = CodePattern(
    "AE C1 02 EC {last:w} D0 ?? AD C4 02 F0 ?? A9 00 8D C3 02 A9 01 60 AE {first:w}"
)
RECORD = CodePattern("A2 {count} 9D {rec:w} CA 10 FA")
FILL = CodePattern("A2 {count} 9D {fill:w} CA D0 FA")
MISC = CodePattern("AD {misc:w} 29 10 F0 ?? AD")
SWAP = CodePattern("B9 A5 00 AA B9 {ptrs:w} 99 A5 00")
FIXED = CodePattern("A9 {lo} 85 AB A9 {hi} 85 AC BD ?? ?? 85 {zp}")
ZP_PTRS = 0xA5
HEADER_PTR = 6
LATCH = CodePattern("{op} 04 DC C8 B1 ?? C8 C9 06")
STA_ABS = 0x8D
READER = CodePattern("AD C6 02 29 01 F0 01 60 AD C0 02 0A A8 B1")


def _image(view) -> SidImage:
    return SidImage.from_prg(b"\0\0" + bytes(view))


def _one(image, pattern):
    return next(iter(find_code_all(image, pattern))).captures


def _entries(image):
    return [m for pattern in ENTRIES for m in find_code_all(image, pattern)]


def entries(view):
    """Every Soundmonitor play entry in ``view``."""
    return {m.addr for m in _entries(_image(view))}


def binding(view, entry):
    """The playspec layout of the Soundmonitor instance at ``entry`` in the
    64 KiB ``view``."""
    image = _image(view)
    play = next(m for m in _entries(image) if m.addr == entry)
    sections = _one(image, SECTIONS)
    record = _one(image, RECORD)
    fill = _one(image, FILL)
    table = locate_note_freq(image)
    swap = next(iter(find_code_all(image, SWAP)), None)
    ptrs = swap.captures["ptrs"] if swap else ZP_PTRS
    fixed = next(iter(find_code_all(image, FIXED)), None)
    return {
        "entry": entry,
        "init_flag": play.captures["flag"],
        "last_sec": sections["last"],
        "first_sec": sections["first"],
        "rec": record["rec"],
        "rec_n": record["count"],
        "fill": fill["fill"],
        "fill_hi": fill["fill"],
        "fill_n": fill["count"],
        "misc": _one(image, MISC)["misc"],
        "ptrs": ptrs,
        "hdr_zp": fixed.captures["zp"] if fixed else ptrs + HEADER_PTR,
        "hdr_fixed": fixed.captures["lo"] | fixed.captures["hi"] << 8 if fixed else 0,
        "base": _layouts(image)[0].base,
        "freq_hi": table.addr,
        "freq_lo": table.addr + c.NOTE_FREQ_LEN,
        "cia": _one(image, LATCH)["op"] == STA_ABS,
    }


def row_reader(view) -> int:
    """The address of voice 0's row reader."""
    return next(iter(find_code_all(_image(view), READER))).addr
