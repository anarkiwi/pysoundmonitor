"""Synthetic Soundmonitor-format image builder for the offline test suite.

The bytes here are *ours*, constructed to the documented layout (see
``docs/format.md``); no copyrighted player or tune bytes are used. The builder
emits a self-consistent PSID (or bare ``.prg``) that the reader decodes.
"""

from __future__ import annotations

from pysidtracker import write_psid
from pysidtracker.asm6502 import assemble

from pysoundmonitor import constants as c

STA_DC04 = c.STA_DC04
STA_DC05 = c.STA_DC05
CMP_06 = c.CMP_06


def make_section(
    *,
    latch=0x4C00,
    tempo=7,
    patlen=4,
    vol_ramp=0xFF,
    base_vol=0x0F,
    arp=(0, 1, 2, 3, 4, 5, 6, 7),
    transpose=(0, 0, 0),
    voices=None,
):
    """A section spec dict with sensible defaults."""
    if voices is None:
        # voice 0: key-on note (instrument 1), rest, tie, legato note.
        voices = [
            [(0x81, 0x41), (0x00, 0x00), (0x80, 0x00), (0x10, 0x02)],
            [(0x83, 0x21), (0x00, 0x00), (0x00, 0x00), (0x00, 0x00)],
            [(0x85, 0x40), (0x00, 0x00), (0x00, 0x00), (0x00, 0x00)],
        ]
    return {
        "latch": latch,
        "tempo": tempo,
        "patlen": patlen,
        "vol_ramp": vol_ramp,
        "base_vol": base_vol,
        "arp": tuple(arp),
        "transpose": tuple(transpose),
        "voices": voices,
    }


def make_instruments():
    """Two 24-byte instrument records: index 1 with a filter tail, 2 without."""
    with_filter = bytes(range(1, 17)) + bytes(
        (0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17)
    )
    # marker byte record[16] == 0xFF => no filter tail.
    no_filter = bytes(range(17, 33))[:16] + bytes((0xFF,)) + bytes(7)
    return {1: with_filter, 2: no_filter}


def _psid(payload, load, init, play, magic="PSID"):
    return write_psid(load=load, init=init, play=play, image=payload, kind=magic)


def build_payload(
    sections, instruments, *, base=0x1000, with_freq=True, with_operands=True
):
    """Build the raw C64 image (load..end) plus its ``(load, init, play)``."""
    mem = bytearray(0x10000)
    base_page = base >> 8
    hdr_base = base + 0x1000
    stream_base = base + 0x1400
    for i, sec in enumerate(sections):
        hdr = hdr_base + i * 0x10
        mem[base + c.SECTION_HEADER_PTR_LO + i] = hdr & 0xFF
        mem[base + c.SECTION_HEADER_PTR_HI + i] = (hdr >> 8) & 0xFF
        mem[hdr + c.SH_LATCH_LO] = sec["latch"] & 0xFF
        mem[hdr + c.SH_LATCH_HI] = (sec["latch"] >> 8) & 0xFF
        mem[hdr + c.SH_TEMPO] = sec["tempo"]
        mem[hdr + c.SH_PATLEN] = sec["patlen"]
        mem[hdr + c.SH_VOL_RAMP] = sec["vol_ramp"]
        mem[hdr + c.SH_BASE_VOL] = sec["base_vol"]
        for a in range(c.ARP_TABLE_LEN):
            mem[hdr + c.SH_ARP_TABLE + a] = sec["arp"][a]
        for v in range(3):
            stream = stream_base + (i * 3 + v) * 0x80
            mem[base + c.VOICE_PTR_LO[v] + i] = stream & 0xFF
            mem[base + c.VOICE_PTR_HI[v] + i] = (stream >> 8) & 0xFF
            mem[base + c.VOICE_XPOSE[v] + i] = sec["transpose"][v]
            for r, (note, ctrl) in enumerate(sec["voices"][v]):
                mem[stream + r * 2] = note
                mem[stream + r * 2 + 1] = ctrl
    for idx, rec in instruments.items():
        addr = base + c.INSTRUMENTS + idx * c.INSTRUMENT_STRIDE
        mem[addr : addr + len(rec)] = rec
    player = base + 0x2000
    pos = player
    if with_operands:
        for page in (base_page, base_page + 4, base_page + 8, base_page + 12):
            mem[pos : pos + 3] = bytes((c.LDA_ABSX, 0x00, page))
            pos += 3
    anchor = pos
    mem[pos : pos + 3] = STA_DC04
    pos += 3
    mem[pos : pos + 2] = CMP_06
    pos += 2
    mem[pos : pos + 2] = bytes((0x90, 0x03))  # BCC guard
    pos += 2
    mem[pos : pos + 3] = STA_DC05
    pos += 3
    end = pos
    if with_freq:
        freq = base + 0x2100
        for i in range(c.NOTE_FREQ_LEN):
            mem[freq + i] = 1 + (i * 0xFE) // (c.NOTE_FREQ_LEN - 1)
        for i in range(c.NOTE_FREQ_LEN):
            mem[freq + c.NOTE_FREQ_LEN + i] = (i * 0x10) & 0xFF
        # The player reads the tables with paired LDA abs,X (hi then lo); emit
        # those operands so the locator finds them exactly as on a real tune.
        mem[pos : pos + 3] = bytes((c.LDA_ABSX, freq & 0xFF, (freq >> 8) & 0xFF))
        pos += 3
        lo = freq + c.NOTE_FREQ_LEN
        mem[pos : pos + 3] = bytes((c.LDA_ABSX, lo & 0xFF, (lo >> 8) & 0xFF))
        pos += 3
        end = freq + 2 * c.NOTE_FREQ_LEN
    return bytes(mem[base:end]), base, player, player + 0x20, anchor


def build_sid(sections=None, instruments=None, **kwargs):
    """Build a full synthetic Soundmonitor PSID; return ``(bytes, anchor)``."""
    if sections is None:
        sections = [make_section()]
    if instruments is None:
        instruments = make_instruments()
    payload, load, init, play, anchor = build_payload(sections, instruments, **kwargs)
    return _psid(payload, load, init, play), anchor


def build_prg(sections=None, instruments=None, **kwargs):
    """Build the same image as a bare ``.prg`` (load-address prefix)."""
    if sections is None:
        sections = [make_section()]
    if instruments is None:
        instruments = make_instruments()
    payload, load, _init, _play, anchor = build_payload(sections, instruments, **kwargs)
    return bytes((load & 0xFF, (load >> 8) & 0xFF)) + payload, anchor


_PACKED_LOAD = 0x0801  # PRG/BASIC load address
_PACKED_ENTRY = 0x0825  # the 6502 unpacker entry (the SYS target)
_PACKED_SRC = 0x4000  # packed payload source: above the destination, in low RAM
_PACKED_RETURN = 0xC000  # the unpacker's exit jump; ends the host run


def _sys_line(entry: int) -> bytes:
    """A tokenised BASIC line running ``SYS <entry>``."""
    return b"\x01\x00\x00\x00\x01\x9e\x20" + str(entry).encode("ascii") + b"\x00"


def _unpacker(src: int, dst: int, count: int, player: int, org: int) -> bytes:
    """A 6502 forward-copy of ``count`` bytes ``src`` -> ``dst`` that leaves via ``JMP player``.

    Zero-page pointers/counter with ``(zp),Y`` I/O, assembled at ``org``. Require
    ``src >= dst`` (no self-run-over).
    """
    return assemble(
        f"""
        LDA #${src & 0xFF:02X}
        STA $02
        LDA #${src >> 8:02X}
        STA $03
        LDA #${dst & 0xFF:02X}
        STA $04
        LDA #${dst >> 8:02X}
        STA $05
        LDA #${count & 0xFF:02X}
        STA $08
        LDA #${count >> 8:02X}
        STA $09
  top:  LDA $08
        BEQ chk
        BNE body
  chk:  LDA $09
        BEQ fin
        BNE body
  body: LDY #$00
        LDA ($02),Y
        STA ($04),Y
        INC $02
        BNE s1
        INC $03
  s1:   INC $04
        BNE d1
        INC $05
  d1:   LDA $08
        BEQ db
        DEC $08
        BNE cont
        LDA $09
        BEQ fin
        BNE cont
  db:   DEC $08
        DEC $09
  cont: JMP top
  fin:  JMP ${player:04X}
""",
        org,
    )[0]


def build_packed_prg(sections=None, instruments=None, **kwargs):
    """A bare PRG whose SYS stub unpacks the song data to the expected base.

    The payload is laid out at ``base`` (default $1000) but first sits at a higher
    packed page; a 6502 stub reached through a BASIC ``SYS`` copies it back -- the
    shape of a SoundMonitor-Crunch output. ``parse`` must run the stub first.

    Returns:
        ``(prg_bytes, anchor)``.
    """
    if sections is None:
        sections = [make_section()]
    if instruments is None:
        instruments = make_instruments()
    payload, base, _init, _play, anchor = build_payload(sections, instruments, **kwargs)
    src, dst = _PACKED_SRC, base
    if src < dst or src + len(payload) >= 0x8000:
        raise ValueError("packed source must sit above the destination and in low RAM")
    mem = bytearray(0x10000)
    line = _sys_line(_PACKED_ENTRY)
    mem[_PACKED_LOAD : _PACKED_LOAD + len(line)] = line
    unpacker = _unpacker(src, dst, len(payload), _PACKED_RETURN, _PACKED_ENTRY)
    mem[_PACKED_ENTRY : _PACKED_ENTRY + len(unpacker)] = unpacker
    mem[src : src + len(payload)] = payload
    prg = bytes((_PACKED_LOAD & 0xFF, (_PACKED_LOAD >> 8) & 0xFF)) + bytes(
        mem[_PACKED_LOAD : src + len(payload)]
    )
    return prg, anchor


def build_no_fingerprint():
    """A valid-looking PSID whose image carries no engine fingerprint."""
    payload = bytes(0x200)
    return _psid(payload, 0x1000, 0x1000, 0x1020)


def build_playable_sid(load=0x1000):
    """A minimal PSID with REAL 6502 init/play the player can run offline.

    ``init`` seeds volume and a RAM counter; ``play`` samples the VIC raster and
    the voice-3 SID read-back (exercising the player's read observers), bumps the
    counter and writes it to voice-1/voice-2 frequency, so successive frames
    yield a changing SID register grid. Pure functional 6502 -- no copyrighted
    engine bytes.
    """
    counter, init, play = load + 0x02, load + 0x10, load + 0x20
    body = bytearray(0x40)
    for org, src in (
        (init, f"LDA #$0F\nSTA $D418\nLDA #$00\nSTA ${counter:04X}\nRTS"),
        (
            play,
            f"LDA $D012\nLDA $D41B\nINC ${counter:04X}\nLDA ${counter:04X}\n"
            "STA $D400\nSTA $D407\nRTS",
        ),
    ):
        code = assemble(src, org)[0]
        body[org - load : org - load + len(code)] = code
    return _psid(bytes(body), load, init, play)


IRQ_LOAD = 0x1000
IRQ_MAIN = 0x1030
IRQ_HANDLER = 0x1040
IRQ_COUNTER = 0x10F0


def _bump(reg):
    """``INC counter / LDA counter / STA $D400+reg``."""
    return f"INC ${IRQ_COUNTER:04X}\nLDA ${IRQ_COUNTER:04X}\nSTA ${0xD400 + reg:04X}\n"


def build_irq_sid(source="raster", *, bank=0x37, loop=False, cli=True, ack=0x8D):
    """An RSID (play address 0) whose init installs its own play interrupt.

    ``source`` is ``raster`` (VIC), ``cia`` (CIA #1 Timer A) or ``nmi`` (CIA #2
    Timer A). The handler bumps a counter into voice 1, 2 or 3 freq-lo when its
    status register reports the interrupt, then stores a re-read of that register
    into the next register. The raster handler acknowledges with opcode ``ack``
    and leaves through ``$EA31``. The CIA handlers are installed for the CPU port
    ``bank`` sets: under the KERNAL through ``$0314`` (left through ``$EA31``) or
    ``$0318`` (left through ``$FE47``), else through ``$FFFE``/``$FFFA`` (``RTI``).
    Init ends ``CLI``/``RTS``; ``loop`` never returns, bumping a second counter
    into ``$D415``; without ``cli`` interrupts stay masked. Functional 6502
    written for the tests -- no tune bytes.
    """
    nmi, kernal = source == "nmi", bank & 2
    if source == "raster":
        init, vector = "", 0x0314
        program = "LDA #$7F\nSTA $DC0D\nLDA #$01\nSTA $D01A\nLDA #$1B\nSTA $D011\n"
        program += "LDA #$80\nSTA $D012\n"
        handler = f"LDA $D019\n.byte ${ack:02X}, $19, $D0\nBPL skip\n" + _bump(0)
        handler += "skip: LDA $D019\nSTA $D401\nJMP $EA31\n"
    else:
        init = f"LDA #${bank:02X}\nSTA $01\n"
        cia, reg = (0xDD00, 14) if nmi else (0xDC00, 7)
        vector = (
            (0x0318 if kernal else 0xFFFA) if nmi else (0x0314 if kernal else 0xFFFE)
        )
        program = "".join(
            f"LDA #${v:02X}\nSTA ${cia + r:04X}\n"
            for r, v in ((4, 0xC7), (5, 0x4C), (0xD, 0x81), (0xE, 0x11))
        )
        save = nmi or not kernal
        handler = ("PHA\n" if save else "") + f"LDA ${cia + 0xD:04X}\nBPL skip\n"
        handler += _bump(reg) + f"skip: LDA ${cia + 0xD:04X}\nSTA ${0xD401 + reg:04X}\n"
        handler += "PLA\n" if save else ""
        handler += f"JMP ${0xFE47 if nmi else 0xEA31:04X}\n" if kernal else "RTI\n"
    init += f"SEI\nLDA #${IRQ_HANDLER & 0xFF:02X}\nSTA ${vector:04X}\n"
    init += f"LDA #${IRQ_HANDLER >> 8:02X}\nSTA ${vector + 1:04X}\n{program}"
    init += "LDA #$0F\nSTA $D418\n" + ("CLI\n" if cli else "")
    init += f"JMP ${IRQ_MAIN:04X}\n" if loop else "RTS\n"
    counter = f"${IRQ_COUNTER + 1:04X}"
    spin = f"INC {counter}\nLDA {counter}\nSTA $D415\nJMP ${IRQ_MAIN:04X}\n"
    body = bytearray(IRQ_COUNTER + 2 - IRQ_LOAD)
    for org, src in ((IRQ_LOAD, init), (IRQ_MAIN, spin), (IRQ_HANDLER, handler)):
        code = assemble(src, org)[0]
        body[org - IRQ_LOAD : org - IRQ_LOAD + len(code)] = code
    return _psid(bytes((IRQ_LOAD & 0xFF, IRQ_LOAD >> 8)) + body, 0, IRQ_LOAD, 0, "RSID")
