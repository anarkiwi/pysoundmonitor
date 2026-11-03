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

SPEC = load(resources.files(__package__).joinpath("specs", "soundmonitor.play"))

ENTRIES = (
    CodePattern("20 {swap:w} AD {flag:w} F0 03 20 {init:w} AE {on:w} D0"),
    CodePattern("AD {flag:w} F0 03 20 {init:w} AE {on:w} D0"),
)
SECTIONS = CodePattern(
    "AE C1 02 EC {last:w} D0 ?? AD C4 02 F0 ?? A9 00 8D C3 02 A9 01 60 AE {first:w}"
)
FILL_AT = 0xCD7F - 0xCBFA
MISC = CodePattern("AD {misc:w} 29 10 F0 ?? AD")
FILL = CodePattern("A2 {count} 9D {fill:w} CA D0 FA")
RECORD = CodePattern("A2 {count} 9D {rec:w} CA 10 FA")
FIXED = CodePattern("A9 {lo} 85 AB A9 {hi} 85 AC BD ?? ?? 85 {zp}")
LATCH = CodePattern("{op} 04 DC C8 B1 ?? C8 C9 06")
GLIDE = CodePattern("38 BD ?? ?? FD ?? ?? 8D C7 02 BD {src:w} FD ?? ?? 8D C8 02 4C")
SILENCE = CodePattern("A9 {vol} 8D 18 D4 A9 07 8D")
RAMP = CodePattern("29 F0 0D C7 02 8D ?? ?? {store} ?? ?? 60")
VOLUME = CodePattern(
    "60 AD ?? ?? 29 F0 {opcode} {operand} ?? 8D ?? ?? {finish} ?? ?? 60"
)
FILTER_VARS = {
    "base_vol": (2, "byte"),
    "vol": (0x1F, "byte"),
    "fc": (0x1C, "word"),
    "fflags": (0, "byte"),
    "freq": (7, "voice7"),
    "target": (0x20, "voice7"),
    "pw": (9, "voice7"),
}
PAGE_VARS = {
    "row": 0x02C0,
    "section": 0x02C1,
    "playing": 0x02C2,
    "enabled": 0x02C3,
    "loop": 0x02C4,
    "mutes": 0x02C6,
}
VARS = {
    "tempo": (0xCD80 - 0xCBFA, "byte"),
    "period": (0xCD81 - 0xCBFA, "byte"),
    "patlen": (0xCDB5 - 0xCBFA, "byte"),
    "ramp_len": (0xCDBA - 0xCBFA, "byte"),
    "ramp_ctr": (0xCDBB - 0xCBFA, "byte"),
    "ramp_vol": (0xCDBC - 0xCBFA, "byte"),
    "fup": (0xCD9E - 0xCBFA, "byte"),
    "fdown": (0xCD9F - 0xCBFA, "byte"),
    "fstep": (0xCDA0 - 0xCBFA, "byte"),
    "fbound": (0xCDA1 - 0xCBFA, "byte"),
    "tail": (0xCC42 - 0xCBFA, "tail"),
    "ctrl": (0xCDD6 - 0xCBFA, "voice"),
    "sus": (8, "voice24"),
    "rate": (0xCD82 - 0xCBFA, "rate"),
    "limit": (0xCD85 - 0xCBFA, "voice"),
    "peak": (0xCD8D - 0xCBFA, "voice"),
    "mode": (0xCDB7 - 0xCBFA, "voice"),
    "vib_dep": (0xCD98 - 0xCBFA, "voice"),
    "vib_per": (0xCD95 - 0xCBFA, "voice"),
    "vib_delay": (0xCD92 - 0xCBFA, "voice"),
    "vib_cnt": (0xCD9B - 0xCBFA, "voice"),
    "up": (0xCDA8 - 0xCBFA, "voice"),
    "down": (0xCDAB - 0xCBFA, "voice"),
    "up_reload": (0xCDA2 - 0xCBFA, "voice"),
    "down_reload": (0xCDA5 - 0xCBFA, "voice"),
    "step": (0xCDAE - 0xCBFA, "voice"),
    "arp_on": (0xCDCB - 0xCBFA, "voice"),
    "arp_idx": (0xCDC8 - 0xCBFA, "voice"),
    "arp_base": (0xCDCE - 0xCBFA, "voice"),
    "note": (0xCDC5 - 0xCBFA, "voice"),
    "detune": (0xCDD2 - 0xCBFA, "voice"),
}
HOOK = bytes.fromhex("8d18d48d000060")
STA_ABS = 0x8D
ORA_IMM = 0x09
JSR_ABS = 0x20
VOLUME_OPERAND = 7
VOLUME_FINISH = 12


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


def entries(view):
    """Every Soundmonitor play entry in ``view``."""
    return {m.addr for m in _entries(_image(view))}


def _inits(view, rec, misc):
    """The player's variables as the tune's init left them. ``rec`` and ``misc`` anchor
    the two work blocks."""
    word = lambda a: view[a] | view[a + 1] << 8
    out = {f"init_{name}": view[at] for name, at in PAGE_VARS.items()}
    for anchor, names in ((rec, VARS), (misc, FILTER_VARS)):
        for name, (off, kind) in names.items():
            at = anchor + off
            out[f"init_{name}"] = {
                "byte": lambda: view[at],
                "word": lambda: word(at),
                "tail": lambda: list(view[at : at + 8]),
                "voice": lambda: [view[at + v] for v in range(3)],
                "voice7": lambda: [word(at + 7 * v) for v in range(3)],
                "voice24": lambda: [view[at + 24 * v] for v in range(3)],
                "rate": lambda: [
                    view[at + v] | view[at + 8 + v] << 8 for v in range(3)
                ],
            }[kind]()
    return out


def binding(view, entry):
    """The playspec layout of the Soundmonitor instance at ``entry`` in the memory
    ``view``: the addresses of the song data and tables the build reads, its flags
    and the variables as the tune's init left them.

    Raises:
      SidParseError: the code lacks an operand shape the spec binds to, as a rip
        that patched it elsewhere does, or calls code of its own for the volume.
    """
    image = _image(view)
    play = next((m for m in _entries(image) if m.addr == entry), None)
    if play is None:
        raise SidParseError(f"no Soundmonitor play entry at ${entry:04x}")
    sections = _find(image, SECTIONS, "section bounds").captures
    rec = _find(image, RECORD, "instrument copy block").captures["rec"]
    misc = _find(image, MISC, "filter flags").captures["misc"]
    if _find(image, FILL, "state clear").captures["fill"] != rec + FILL_AT:
        raise SidParseError(
            "the state clear does not cover the variables the spec binds"
        )
    table = locate_note_freq(image)
    if table is None:
        raise SidParseError("not a build the playspec models: no note-frequency tables")
    fixed = next(iter(find_code_all(image, FIXED)), None)
    glide = _find(image, GLIDE, "glide")
    silence = _find(image, SILENCE, "stop volume")
    ramp = _find(image, RAMP, "volume ramp")
    volume = _find(image, VOLUME, "volume routine")
    mirror = 0
    if volume.captures["finish"] == JSR_ABS:
        hook = volume.addr + VOLUME_FINISH + 1
        target = view[hook] | view[hook + 1] << 8
        code = bytes(view[target : target + len(HOOK)])
        if code[:4] + code[6:] != HOOK[:4] + HOOK[6:]:
            raise SidParseError("the volume routine calls code of the rip's own")
        mirror = code[4] | code[5] << 8
    imm = volume.captures["opcode"] == ORA_IMM
    source = glide.captures["src"]
    cur = []
    for v in range(3):
        voice, part = divmod(source + 7 * v - misc - FILTER_VARS["freq"][0], 7)
        if voice not in range(3) or part > 1:
            raise SidParseError(
                "the glide reads a byte that is not a voice's frequency"
            )
        cur.append(2 * voice + part)
    return {
        "base": _layouts(image)[0].base,
        "freq_hi": table.addr,
        "freq_lo": table.addr + c.NOTE_FREQ_LEN,
        "first_sec": sections["first"],
        "last_sec": sections["last"],
        "stop_vol": silence.addr + 1,
        "mirror": mirror,
        "vol_or": volume.addr + VOLUME_OPERAND if imm else 0,
        "hdr_fixed": fixed.captures["lo"] | fixed.captures["hi"] << 8 if fixed else 0,
        "cia": _find(image, LATCH, "latch load").captures["op"] == STA_ABS,
        "vol_imm": imm,
        "ramp_store": ramp.captures["store"] == STA_ABS,
        "cur": cur,
        "init_flag": view[play.captures["flag"]],
        **_inits(view, rec, misc),
    }


def bind(view, entry):
    """The layout of the instance at ``entry``."""
    return binding(view, entry)
