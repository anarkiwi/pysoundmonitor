"""Documented Soundmonitor (64'er / Hulsbeck) song-data layout constants.

Every value here is a *structural fact* transcribed from the reverse-engineering
architecture reference (``re-trackers/Soundmonitor/soundmonitor-architecture.md``)
- table geometry, the 16-byte instrument-record field map, section-header and row
layouts, and a tiny relocation-invariant engine fingerprint. No player code is
reproduced: the fingerprint is expressed purely as writes to fixed C64 hardware
registers plus an immediate guard constant, which are functional facts, not
copyrightable expression.
"""

from __future__ import annotations

from pysidtracker.registers import CIA1_TIMER_A_HI, CIA1_TIMER_A_LO, STA_ABS

# --- Recognizer fingerprint (relocation-invariant) --------------------------
# Soundmonitor is CIA-timed and programs its OWN play period: the section loader
# reads the CIA-1 Timer-A latch from the per-section header and writes it to the
# fixed hardware registers $DC04/$DC05, guarding the hi byte with ``CMP #$06``.
# These target absolute hardware addresses that never relocate, so the sequence
# is a load-address-independent signature of the engine. The store bytes are
# built from the base register map (``STA <abs>`` targeting the CIA-1 timer).
STA_DC04 = bytes((STA_ABS, CIA1_TIMER_A_LO & 0xFF, CIA1_TIMER_A_LO >> 8))
STA_DC05 = bytes((STA_ABS, CIA1_TIMER_A_HI & 0xFF, CIA1_TIMER_A_HI >> 8))
CMP_06 = bytes((0xC9, 0x06))  # CMP #$06   (hi-latch guard)
# Max byte gap between the two CIA stores for the fingerprint to match.
FINGERPRINT_WINDOW = 0x10

# ``LDA abs,X`` opcode: the section loader indexes the page-aligned split
# pointer tables (and the note-freq tables) with it, so its operands recover the
# data-region base page and the note-freq table addresses relocation-tolerantly.
LDA_ABSX = 0xBD
# ``CPX abs`` / ``LDX abs`` opcodes: the section-advance code compares the live
# section index against ``sec_last`` (``CPX``) and reloads ``sec_start``
# (``LDX``) from two adjacent player globals, so their operands recover the song
# section bounds relocation-tolerantly (see ``_recover_section_bounds``).
CPX_ABS = 0xEC
LDX_ABS = 0xAE

# --- Data-region table geometry (byte offsets from the data base) -----------
# The per-tune data region is a run of page-aligned split (lo/hi) pointer and
# transpose tables indexed by the section number, followed by the instrument
# records. Layout mirrors the Only_3 map ($A000 base) from the architecture doc.
PAGE = 0x100
V0_PTR_LO = 0x000
V0_PTR_HI = 0x100
V0_XPOSE = 0x200
V0_XPOSE2 = 0x300
V1_PTR_LO = 0x400
V1_PTR_HI = 0x500
V1_XPOSE = 0x600
V1_XPOSE2 = 0x700
V2_PTR_LO = 0x800
V2_PTR_HI = 0x900
V2_XPOSE = 0xA00
V2_XPOSE2 = 0xB00
SECTION_HEADER_PTR_LO = 0xC00
SECTION_HEADER_PTR_HI = 0xD00
INSTRUMENTS = 0xE00

# Per-voice (stream-ptr-lo, stream-ptr-hi, transpose) page offsets, voice order.
VOICE_PTR_LO = (V0_PTR_LO, V1_PTR_LO, V2_PTR_LO)
VOICE_PTR_HI = (V0_PTR_HI, V1_PTR_HI, V2_PTR_HI)
VOICE_XPOSE = (V0_XPOSE, V1_XPOSE, V2_XPOSE)

# Pages that must all be present for a run of pages to be the data region.
BASE_PROBE_OFFSETS = (V0_PTR_LO, V1_PTR_LO, V2_PTR_LO, SECTION_HEADER_PTR_LO)

# --- Instrument records -----------------------------------------------------
INSTRUMENT_STRIDE = 24  # record spacing: 16 record bytes + 8-byte filter tail
INSTRUMENT_RECORD_LEN = 16
FILTER_TAIL_LEN = 8
# record[16] doubles as the filter-tail vol/mode byte and the "no tail" marker.
FILTER_MARKER = 16
NO_FILTER_TAIL = 0xFF
MAX_INSTRUMENTS = INSTRUMENT_STRIDE  # ctrl low-nibble + base can reach this many

# 16-byte instrument record field offsets (architecture doc record map).
INST_CTRL_GATE = 0  # key-on CTRL (waveform + gate)
INST_AD = 1
INST_SR = 2
INST_GLIDE_RATE_LO = 3
INST_PW = 4  # lo-nibble<<4 -> PW lo, hi-nibble -> PW hi
INST_PW_UP_DWELL = 5
INST_PW_DOWN_DWELL = 6
INST_PW_STEP = 7
INST_SUSTAIN_CTRL = 8  # re-emitted on a rest (gate off)
INST_MODE = 9  # bit5 abs/PW-up-suppress, bit4 PW-sweep enable, bits0/1 glide mode
INST_GLIDE_ENABLE = 10
INST_GLIDE_RATE_HI = 11
INST_VIB_DEPTH = 12
INST_VIB_PERIOD = 13  # &$7F half-period, bit7 direction
INST_VIB_DELAY = 14
INST_DETUNE = 15

# 8-byte filter tail field offsets (relative to the tail start = record[16]).
FT_VOL_MODE = 0
FT_RES_FILT = 1  # -> $D417
FT_CUTOFF_HI = 2  # cutoff-hi seed
FT_UP_DWELL = 3
FT_DOWN_DWELL = 4
FT_STEP = 5
FT_BOUND = 6
FT_FLAGS = 7  # per-voice init/reload/reinit/ping-pong bits

# --- Section header ---------------------------------------------------------
SH_LATCH_LO = 0  # CIA Timer-A latch lo (play-cadence source)
SH_LATCH_HI = 1
SH_TEMPO = 2  # tempo divider reload
SH_PATLEN = 3  # pattern length / row loop
SH_VOL_RAMP = 4  # volume-ramp config ($FF = none)
SH_BASE_VOL = 5  # steady base-volume nibble
SH_ARP_TABLE = 6  # start of the 8-entry arp-note table
ARP_TABLE_LEN = 8
SECTION_HEADER_LEN = SH_ARP_TABLE + ARP_TABLE_LEN

# --- Pattern rows (per voice, per section: flat 2-byte [note][ctrl] rows) ----
ROW_LEN = 2
NOTE_REST = 0x00  # re-emit the saved sustain ctrl
NOTE_TIE = 0x80  # leave the voice untouched (legato hold)
NOTE_TRIGGER = 0x80  # note bit7: new-note key-on trigger
NOTE_INDEX_MASK = 0x7F  # note bits0-6: freq-table index
CTRL_INSTR_MASK = 0x0F  # ctrl bits0-3: instrument index
CTRL_PORTAMENTO = 0x10  # ctrl bit4: keep old freq (slide)
CTRL_ABSOLUTE = 0x20  # ctrl bit5: skip the section transpose
CTRL_ARP_ENABLE = 0x40  # ctrl bit6: enable the arp/glide-target chain
CTRL_INSTR_BASE = 0x80  # ctrl bit7 clear: add the per-section instrument base

# Cap on rows/sections walked, so a malformed image cannot loop unboundedly.
MAX_SECTIONS = 256
MAX_PATTERN_LEN = 256

# --- Note-frequency tables --------------------------------------------------
# Two parallel u8 tables (freq hi, then freq lo) of one entry per semitone; the
# hi table is an octave ramp (non-decreasing, doubling per octave). The lo table
# starts exactly ``NOTE_FREQ_LEN`` bytes after the hi table. Location, ramp
# validation, and candidate lengths are delegated to ``pysidtracker.notefreq``.
# On real HVSC tunes the tables are 95 entries (the hi ramp climbs 01..f8).
NOTE_FREQ_LEN = 95
