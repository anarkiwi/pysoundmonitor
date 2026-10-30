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
SWAP = CodePattern("B9 {zp} 00 AA B9 {stash:w} 99 {zp} 00")
FIXED = CodePattern("A9 {lo} 85 AB A9 {hi} 85 AC BD ?? ?? 85 {zp}")
ZP_PTRS = 0xA5
HEADER_PTR = 6
LATCH = CodePattern("{op} 04 DC C8 B1 ?? C8 C9 06")
GLIDE = CodePattern("38 BD ?? ?? FD ?? ?? 8D C7 02 BD {src:w} FD ?? ?? 8D C8 02 4C")
SILENCE = CodePattern("A9 {vol} 8D 18 D4 A9 07 8D")
RAMP = CodePattern("29 F0 0D C7 02 8D ?? ?? {store} ?? ?? 60")
VOLUME = CodePattern(
    "60 AD ?? ?? 29 F0 {opcode} {operand} ?? 8D ?? ?? {finish} ?? ?? 60"
)
STA_ABS = 0x8D
ORA_IMM = 0x09
JSR_ABS = 0x20
GLIDE_OPERAND = 11
VOLUME_OPERAND = 7
VOLUME_FINISH = 12


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
    ptrs = swap.captures["zp"] if swap else ZP_PTRS
    fixed = next(iter(find_code_all(image, FIXED)), None)
    glide = next(iter(find_code_all(image, GLIDE)))
    silence = next(iter(find_code_all(image, SILENCE)))
    ramp = next(iter(find_code_all(image, RAMP)))
    volume = next(iter(find_code_all(image, VOLUME)))
    hooked = volume.captures["finish"] == JSR_ABS
    return {
        "entry": entry,
        "op_cur_hi": glide.addr + GLIDE_OPERAND,
        "stop_vol": silence.addr + 1,
        "ramp_store": ramp.captures["store"] == STA_ABS,
        "vol_or": volume.addr + VOLUME_OPERAND,
        "vol_imm": volume.captures["opcode"] == ORA_IMM,
        "vol_hook": volume.addr + VOLUME_FINISH if hooked else 0,
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
        "stash": swap.captures["stash"] if swap else 0,
        "swap": swap is not None,
        "hdr_zp": fixed.captures["zp"] if fixed else ptrs + HEADER_PTR,
        "hdr_fixed": fixed.captures["lo"] | fixed.captures["hi"] << 8 if fixed else 0,
        "base": _layouts(image)[0].base,
        "freq_hi": table.addr,
        "freq_lo": table.addr + c.NOTE_FREQ_LEN,
        "cia": _one(image, LATCH)["op"] == STA_ABS,
    }
