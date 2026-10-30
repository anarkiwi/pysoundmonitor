"""The Soundmonitor playroutine as a ``pysidtracker.playspec`` spec.

``specs/soundmonitor.play`` states the play call; :func:`binding` resolves its
``layout`` from the reader's discovery plus the cells the play code's own
operands name. A rip whose volume routine calls its own code runs
``specs/soundmonitor_hook.play``, the variant that hands the volume to that code
(:func:`bind`).
"""

from importlib import resources

from pysidtracker import CodePattern, SidImage, find_code_all
from pysidtracker.playspec import load

from . import constants as c
from .errors import SidParseError
from .reader import _layouts, locate_note_freq

_SPECS = resources.files(__package__).joinpath("specs")
SPEC = load(_SPECS.joinpath("soundmonitor.play"))
HOOK_SPEC = load(_SPECS.joinpath("soundmonitor_hook.play"))

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
BASE_VOLUME = 0xCDEF - 0xCDED


def _image(view) -> SidImage:
    return SidImage.from_prg(b"\0\0" + bytes(view))


def _find(image, pattern, what):
    """The first match of ``pattern`` in ``image``, else the build is not one the
    spec models."""
    found = next(iter(find_code_all(image, pattern)), None)
    if found is None:
        raise SidParseError(f"not a build the playspec models: no {what}")
    return found


def _entries(image):
    return [m for pattern in ENTRIES for m in find_code_all(image, pattern)]


def bind(view, entry):
    """``(spec, binding)`` of the instance at ``entry``: the hook variant when its
    volume routine calls a rip's code, the base spec otherwise."""
    found = binding(view, entry)
    if found["vol_hook"]:
        return HOOK_SPEC, found
    del found["vol_hook"]
    return SPEC, found


def entries(view):
    """Every Soundmonitor play entry in ``view``."""
    return {m.addr for m in _entries(_image(view))}


def binding(view, entry):
    """The playspec layout of the Soundmonitor instance at ``entry`` in the
    64 KiB ``view``.

    Raises:
      SidParseError: the code lacks an operand shape the spec binds to, as a rip
        that patched it elsewhere does.
    """
    image = _image(view)
    play = next((m for m in _entries(image) if m.addr == entry), None)
    if play is None:
        raise SidParseError(f"no Soundmonitor play entry at ${entry:04x}")
    sections = _find(image, SECTIONS, "section bounds").captures
    record = _find(image, RECORD, "instrument copy block").captures
    fill = _find(image, FILL, "state clear").captures
    table = locate_note_freq(image)
    if table is None:
        raise SidParseError("not a build the playspec models: no note-frequency tables")
    swap = next(iter(find_code_all(image, SWAP)), None)
    ptrs = swap.captures["zp"] if swap else ZP_PTRS
    fixed = next(iter(find_code_all(image, FIXED)), None)
    glide = _find(image, GLIDE, "glide")
    silence = _find(image, SILENCE, "stop volume")
    ramp = _find(image, RAMP, "volume ramp")
    volume = _find(image, VOLUME, "volume routine")
    hooked = volume.captures["finish"] == JSR_ABS
    misc = _find(image, MISC, "filter flags").captures["misc"]
    return {
        "entry": entry,
        "op_cur_hi": glide.addr + GLIDE_OPERAND,
        "stop_vol": silence.addr + 1,
        "ramp_store": ramp.captures["store"] == STA_ABS,
        "vol_or": (
            volume.addr + VOLUME_OPERAND
            if volume.captures["opcode"] == ORA_IMM
            else misc + BASE_VOLUME
        ),
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
        "misc": misc,
        "ptrs": ptrs,
        "stash": swap.captures["stash"] if swap else 0,
        "swap": swap is not None,
        "hdr_zp": fixed.captures["zp"] if fixed else ptrs + HEADER_PTR,
        "hdr_fixed": fixed.captures["lo"] | fixed.captures["hi"] << 8 if fixed else 0,
        "base": _layouts(image)[0].base,
        "freq_hi": table.addr,
        "freq_lo": table.addr + c.NOTE_FREQ_LEN,
        "cia": _find(image, LATCH, "latch load").captures["op"] == STA_ABS,
    }
