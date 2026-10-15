"""Synthetic Soundmonitor-format image builder for the offline test suite.

The bytes here are *ours*, constructed to the documented layout (see
``docs/format.md``); no copyrighted player or tune bytes are used. The builder
emits a self-consistent PSID (or bare ``.prg``) that the reader decodes.
"""

from __future__ import annotations

import struct

from pysoundmonitor import constants as c

STA_DC04 = c.STA_DC04
STA_DC05 = c.STA_DC05
CMP_06 = c.CMP_06

# id -> HVSC relative path for the optional on-demand real-tune test.
TUNES = {
    "only3": "MUSICIANS/J/JAP/Only_3.sid",
}


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


def _psid(payload, load, init, play, songs=1, magic=b"PSID"):
    header = bytearray(0x7C)
    header[0:4] = magic
    struct.pack_into(">H", header, 0x04, 2)  # version
    struct.pack_into(">H", header, 0x06, 0x7C)  # data offset
    struct.pack_into(">H", header, 0x08, load)
    struct.pack_into(">H", header, 0x0A, init)
    struct.pack_into(">H", header, 0x0C, play)
    struct.pack_into(">H", header, 0x0E, songs)
    struct.pack_into(">H", header, 0x10, 1)  # start song
    return bytes(header) + payload


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


def _unpacker(src: int, dst: int, count: int, player: int, base: int = 0) -> bytes:
    """A 6502 forward-copy of ``count`` bytes ``src`` -> ``dst`` that leaves via ``JMP player``.

    Zero-page registers with the core's ``$A5``/``$E6``/``$C6`` set and ``(zp),Y`` I/O;
    branches are absolute and resolved in place. Require ``src >= dst`` (no self-run-over).
    """
    sp_l, sp_h, dp_l, dp_h, cl, ch = 0x02, 0x03, 0x04, 0x05, 0x08, 0x09
    prog = bytearray()
    label = {}
    branches = []

    def lda_i(byte):
        return bytes((0xA9, byte))

    def sta(a):
        return bytes((0x8D, a & 0xFF, a >> 8))

    def br(opbyte, target):
        idx = len(prog)
        prog.append(opbyte)
        prog.append(0)
        branches.append((idx, target))

    def jmp(target):
        prog.extend((0x4C, target & 0xFF, target >> 8))

    prog.extend(lda_i(src & 0xFF) + sta(sp_l))
    prog.extend(lda_i(src >> 8) + sta(sp_h))
    prog.extend(lda_i(dst & 0xFF) + sta(dp_l))
    prog.extend(lda_i(dst >> 8) + sta(dp_h))
    prog.extend(lda_i(count & 0xFF) + sta(cl))
    prog.extend(lda_i(count >> 8) + sta(ch))

    label["top"] = len(prog)
    prog.extend((0xA5, cl))
    br(0xF0, "chk")
    br(0xD0, "body")
    label["chk"] = len(prog)
    prog.extend((0xA5, ch))
    br(0xF0, "fin")
    br(0xD0, "body")
    label["body"] = len(prog)
    prog.extend((0xA0, 0x00))
    prog.extend((0xB1, sp_l))
    prog.extend((0x91, dp_l))
    prog.extend((0xE6, sp_l))
    br(0xD0, "s1")
    prog.extend((0xE6, sp_h))
    label["s1"] = len(prog)
    prog.extend((0xE6, dp_l))
    br(0xD0, "d1")
    prog.extend((0xE6, dp_h))
    label["d1"] = len(prog)
    prog.extend((0xA5, cl))
    br(0xF0, "db")
    prog.extend((0xC6, cl))
    br(0xD0, "cont")
    prog.extend((0xA5, ch))
    br(0xF0, "fin")
    br(0xD0, "cont")
    label["db"] = len(prog)
    prog.extend((0xC6, cl))
    prog.extend((0xC6, ch))
    label["cont"] = len(prog)
    jmp(label["top"] + base)
    label["fin"] = len(prog)
    jmp(player)

    for idx, target in branches:
        prog[idx + 1] = (label[target] - (idx + 2)) & 0xFF
    return bytes(prog)


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
    unpacker = _unpacker(src, dst, len(payload), _PACKED_RETURN, base=_PACKED_ENTRY)
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
    counter = load + 0x02  # RAM cell just past the load address
    init = load + 0x10
    play = load + 0x20
    end = load + 0x40
    body = bytearray(end - load)
    init_code = bytes(
        (
            0xA9,
            0x0F,
            0x8D,
            0x18,
            0xD4,
            0xA9,
            0x00,
            0x8D,
            counter & 0xFF,
            counter >> 8,
            0x60,
        )
    )
    play_code = bytes(
        (
            0xAD,
            0x12,
            0xD0,  # LDA $D012 (VIC raster read observer)
            0xAD,
            0x1B,
            0xD4,  # LDA $D41B (voice-3 osc read observer)
            0xEE,
            counter & 0xFF,
            counter >> 8,  # INC counter
            0xAD,
            counter & 0xFF,
            counter >> 8,  # LDA counter
            0x8D,
            0x00,
            0xD4,  # STA $D400 (voice-1 freq lo)
            0x8D,
            0x07,
            0xD4,  # STA $D407 (voice-2 freq lo)
            0x60,  # RTS
        )
    )
    body[init - load : init - load + len(init_code)] = init_code
    body[play - load : play - load + len(play_code)] = play_code
    return _psid(bytes(body), load, init, play)


IRQ_LOAD = 0x1000
IRQ_HANDLER = 0x1040
IRQ_COUNTER = 0x10F0


def _word(value):
    return bytes((value & 0xFF, value >> 8))


def _sta(addr, value):
    """``LDA #value / STA addr``."""
    return bytes((0xA9, value, 0x8D)) + _word(addr)


def _bump(reg):
    """``INC counter / LDA counter / STA $D400+reg`` (9 bytes)."""
    return (
        b"\xee"
        + _word(IRQ_COUNTER)
        + b"\xad"
        + _word(IRQ_COUNTER)
        + b"\x8d"
        + _word(0xD400 + reg)
    )


def _setup(source, vector):
    """Init code installing the handler and programming the interrupt ``source``."""
    lo, hi = _word(IRQ_HANDLER)
    code = b"\x78" + _sta(vector, lo) + _sta(vector + 1, hi)
    if source == "raster":
        code += (_sta(0xDC0D, 0x7F) + _sta(0xD01A, 0x01) + _sta(0xD011, 0x1B)) + _sta(
            0xD012, 0x80
        )
    else:
        cia = 0xDD00 if source == "nmi" else 0xDC00
        code += _sta(cia + 4, 0xC7) + _sta(cia + 5, 0x4C) + _sta(cia + 0xD, 0x81)
        code += _sta(cia + 0xE, 0x11)
    return code + _sta(0xD418, 0x0F)


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
    if source == "raster":
        init = _setup(source, 0x0314)
        status = b"\xad\x19\xd0" + bytes((ack, 0x19, 0xD0)) + b"\x10\x09"
        handler = status + _bump(0) + b"\xad\x19\xd0\x8d\x01\xd4\x4c\x31\xea"
    else:
        nmi, kernal = source == "nmi", bank & 2
        icr = 0xDD0D if nmi else 0xDC0D
        vector = (
            (0x0318 if kernal else 0xFFFA) if nmi else (0x0314 if kernal else 0xFFFE)
        )
        init = bytes((0xA9, bank, 0x85, 0x01)) + _setup(source, vector)
        save = nmi or not kernal
        handler = (b"\x48" if save else b"") + b"\xad" + _word(icr) + b"\x10\x09"
        reg = 14 if nmi else 7
        handler += _bump(reg) + b"\xad" + _word(icr) + b"\x8d" + _word(0xD401 + reg)
        handler += b"\x68" if save else b""
        if kernal:
            handler += b"\x4c" + _word(0xFE47 if nmi else 0xEA31)
        else:
            handler += b"\x40"
    main = IRQ_LOAD + 0x30
    init += (b"\x58" if cli else b"") + (b"\x4c" + _word(main) if loop else b"\x60")
    counter = _word(IRQ_COUNTER + 1)
    spin = b"\xee" + counter + b"\xad" + counter + b"\x8d\x15\xd4\x4c" + _word(main)
    body = bytearray(IRQ_COUNTER + 2 - IRQ_LOAD)
    body[: len(init)] = init
    body[main - IRQ_LOAD : main - IRQ_LOAD + len(spin)] = spin
    body[IRQ_HANDLER - IRQ_LOAD : IRQ_HANDLER - IRQ_LOAD + len(handler)] = handler
    return _psid(_word(IRQ_LOAD) + bytes(body), 0, IRQ_LOAD, 0, magic=b"RSID")
