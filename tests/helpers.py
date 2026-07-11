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


def _psid(payload, load, init, play, songs=1):
    header = bytearray(0x7C)
    header[0:4] = b"PSID"
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
