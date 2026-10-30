# Soundmonitor format

Summary of what `pysoundmonitor` decodes. Structural facts are transcribed from
the reverse-engineering reference (a private repo); no player or tune bytes are
reproduced here.

## Overview

Soundmonitor (64'er / Chris Hulsbeck) is HVSC tracker #6. `pysoundmonitor`
decodes the container, the detection anchor, and the section→stream song-data
structures into a model, and plays a tune byte-exact via `SoundMonitorPlayer`
(see [Player and playback](#player-and-playback-notes)).

## Container and detection notes

### Container

A Soundmonitor tune is a PSID/RSID (or bare `.prg`) wrapping a C64 memory image
of player code plus per-tune song data. `pysidtracker` unwraps the container and
places the image at its load address. The player is **relocatable** and the
header `load`/`init`/`play` addresses are not a trustworthy locator (a relocated
`$C000` build is the norm).

### Detection (relocation-tolerant)

The engine is CIA-timed and programs its **own** play period: the section loader
reads the CIA-1 Timer-A latch from the per-section header and writes it to the
fixed hardware registers `$DC04`/`$DC05`, guarding the hi byte with `CMP #$06`.
Those addresses never relocate, so the sequence

```
STA $DC04  ...  CMP #$06  ...  STA $DC05
```

is a load-address-independent fingerprint. It is present in the CIA-timed builds
($C000 Hulsbeck/64'er, $1000); the fixed-cadence builds omit the register writes
but carry the same note-frequency tables, so `recognize()` falls back to those.
`recognize()` returns an anchor address either way; `SoundMonitorSidParser().detect()`
then classifies the tune as `DIRECT` (found as loaded) or, via the base library's
init emulation, `RELOCATED`/`PACKED`. `parse()` emulates init when the song tables
are not decodable in the directly loaded image (a relocating build).

### Data-region base

The data region is a run of page-aligned split (lo/hi) pointer and transpose
tables indexed by the section number. Its base page is recovered from the
loader's own `LDA $pp00,X` table-indexing operands (every page whose four
table pages (voice 0/1/2 stream pointers, section-header pointers) are all
`LDA $pp00,X` operands is a candidate; the candidate that decodes the most
sections wins, the smaller page on a tie, and one that decodes no section is
dropped). With no candidate the base falls back to the image's load page — no
baked addresses.

## Data model

Layout, in byte offsets from the data base. Every table is one 256-byte page
indexed by the section number; the grammar's `track_tables` rule is the 14
pages below. The per-voice instrument base is added to the `ctrl` instrument
index of a row whose `ctrl` bit 7 is clear:

| offset | page |
|---|---|
| `+$000` | voice 0 stream pointer lo |
| `+$100` | voice 0 stream pointer hi |
| `+$200` | voice 0 transpose |
| `+$300` | voice 0 instrument base |
| `+$400` | voice 1 stream pointer lo |
| `+$500` | voice 1 stream pointer hi |
| `+$600` | voice 1 transpose |
| `+$700` | voice 1 instrument base |
| `+$800` | voice 2 stream pointer lo |
| `+$900` | voice 2 stream pointer hi |
| `+$A00` | voice 2 transpose |
| `+$B00` | voice 2 instrument base |
| `+$C00` | section header pointer lo |
| `+$D00` | section header pointer hi |
| `+$E00` | instrument records (`index * 24`) |

**Section** = one song-step. Its header holds `[0..1]` CIA latch (play cadence =
`latch + 1` cycles/call), `[2]` tempo divider, `[3]` pattern length, `[4]`
volume-ramp config, `[5]` base-volume nibble, `[6..13]` an 8-entry arp-note
table. Each section has three per-voice pattern streams. The pattern length is a
single byte and `0` is not a section: the grammar rejects the header, and the
reader stops the section run there, as it does at a null or out-of-image header
or stream pointer.

The played section range is `sec_start .. sec_last` (inclusive, wrapping mod
256). Those bounds are player globals, so rather than walk the pointer table into
the `MAX_SECTIONS` cap the reader recovers them from the section-advance code's
own `CPX sec_last` / `LDX sec_start` operands (two adjacent globals), and picks
the base + bounds combination that decodes the largest run of valid sections.

**Pattern stream** = a flat array of `pattern length` 2-byte `[note][ctrl]` rows. `note == $00` is
a rest, `note == $80` a tie/hold; otherwise `note` bit7 is the new-note key-on
trigger and bits0-6 a freq-table index. `ctrl` bits: 0-3 instrument index, bit4
portamento, bit5 absolute (skip section transpose), bit6 arp/glide enable, bit7
clear adds the per-section instrument base.

**Instrument record** = 16 bytes: `0` CTRL+gate, `1` AD, `2` SR, `3` glide-rate
lo, `4` PW seed, `5` PW up-dwell, `6` PW down-dwell, `7` PW step, `8` sustain
CTRL, `9` mode, `10` glide-enable, `11` glide-rate hi, `12` vibrato depth, `13`
vibrato period/dir, `14` vibrato delay, `15` detune. Bytes `16..23` are the
8-byte global-filter tail (`vol/mode, res/filt, cutoff-hi, up-dwell, down-dwell,
step, bound, flags`); the record has no tail when byte `16`, the tail's first
byte, is `$FF` (24-byte record stride either way). The reader decodes only the
instruments that a trigger row selects (`ctrl` bits 0-3); an unreferenced record
is not in `song.instruments`.

**Note-frequency tables** = two parallel per-semitone tables (freq hi then lo,
95 entries each, abutting). The player reads them with paired `LDA NoteFreqHi,X`
/ `LDA NoteFreqLo,X`, so they are located relocation-tolerantly from those two
absolute-indexed operands (their difference is the table length) validated by the
hi table's octave-ramp signature.

## Song grammar

`src/pysoundmonitor/soundmonitor.lark` is a `pysidtracker` `ByteGrammar` of the
streams above; the reader parses every table, header, pattern and instrument
record through it, so a malformed stream is a `SidParseError`. Its start rules:
`section_header`, `pattern`, `instrument`, `track_tables`, and, for the editor's
saves, `sound_bank` (SOUND first-last: consecutive 24-byte records) and
`steps_only` (STEPS ONLY: the track tables then the records). The format is
registered with it, so `pysidtracker grammar TUNE` lists a tune's streams.

## Player and playback notes

`SoundMonitorPlayer` is a
[`pysidtracker.playspec.SpecPlayer`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/playspec.md)
over `src/pysoundmonitor/specs/soundmonitor.play`. The tune's own `init` runs on
the 6502 emulator as the PSID driver boots it (selecting the subtune, placing any
relocated data); each play call is then the spec, run on the memory init left
and bound to the build's own addresses, and the memory it leaves equals the
tune's own 6502 play routine's:

```python
from pysoundmonitor import SoundMonitorPlayer

player = SoundMonitorPlayer(open("tune.sid", "rb").read())
grid = player.render_grid(250)               # 25-reg, forward-filled per frame
writes = list(player.register_writes(250))   # pysidtracker RegWrite log
player.render_wav("tune.wav", 250)           # pysidtracker.audio, emulated SID
```

### The playspec

One play call is `entry` (the first call's init, or the stop when the song has
ended), then, while playing, the voices' glide, vibrato, the filter sweep, the
pulse sweeps up and down and the arpeggio, the row `clock`, the three `reader`
machines on a row frame, and the volume. Builds that swap their zero-page
pointer block with a stash on entry and exit (`swap`) swap it in the spec too.
The song's tables are `ro` cells; the
instrument copies, effect counters, frequency, pulse and SID mirrors are shared
cells at the build's own addresses. Where the three voices' code differs the
spec keeps the difference: voice 1's new-note path stores its control mirror in
voice 0's cell, and voice `n` steps the arpeggios of voices `n` and above.

The binder (`pysoundmonitor.playspec.binding`) reads the layout from the reader's
discovery (data base, note-frequency tables) and from the operands of the play
code: the first-call flag, the section bounds, the instrument-copy and filter
work blocks, the zero-page pointer block, whether the section loader writes the
CIA latch, and whether the section header is read through a fixed pointer. It
binds the `$C000` builds (CIA-timed and fixed-cadence), the relocated `$6000`
build and the `$9FFE` section-bound build alike. `pysidtracker playspec check
src/pysoundmonitor/specs/soundmonitor.play` runs the static checks.

`SoundMonitorPlayer(data, validate=True)` parses each section header and
pattern the spec opens under the song grammar; `player.validator.failures()`
lists any that do not parse.

Both replay variations are reproduced: the **CIA-timed** cohort (self-scheduled
cadence via the `$DC04`/`$DC05` latch, e.g. `Only_3`, `Defu`, `Charts`) and the
**fixed-cadence** PAL-video cohort (e.g. `Fi`, `Big_Boing`, `Into_Orbit`).
`SoundMonitorPlayer.cadence` derives the play period from what init programs;
per-section cadence is also exposed on the model (`latch + 1` cycles/call, and
`song.cia_latch`).

### Tunes that install their own interrupt

A header play address of 0 (an RSID such as `Sidewalk`) means init installs the
play handler itself. `SoundMonitorPlayer` then plays the tune on
[`pysidtracker.irqhost.InterruptPlayer`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/irqhost.md)
through its `SpecPlayer`, which boots and runs it as the sidtrace oracle's
sidplayfp does: the PSID driver from power-on, every interrupt the tune programs
(VIC raster, CIA #1 Timer A/B IRQs, CIA #2 Timer A/B NMIs) and the cycles the VIC
steals for badlines and sprites, cycle for cycle. A call a handler makes into the
modelled entry runs as the spec, as an instant at the cycle it is reached, so an
NMI sample player beside the music IRQ, or a main loop writing `$D418`, renders
byte-exact. `render_grid` frames the rows at the play source's raises exactly as
the oracle grid is framed; `play_frame` advances one such frame. A tune whose
interrupts never run raises `SidParseError` when rendered.

### Patched tunes

Some rips patch the playroutine. The spec follows the patches it knows through
the build's own operands, not a second spec: the glide's source of the current
high byte, the volume a stop leaves, whether the ramp stores `$D418`, the volume
routine's OR source (a constant instead of the song's base volume) and a `JSR`
hook in place of the `$D418` store. The hook is a `native` statement of the spec:
the tune's own 6502 runs from the `JSR`, with the mirrored volume in the
accumulator, and the spec carries on after it. `Future_Spy` and `Lavender` are
such rips; both play byte-exact to their own 6502. A build that lacks one of the
operand shapes the binder reads, as a rip that patched elsewhere does, raises
`SidParseError` naming the missing shape; a patch that leaves every shape in
place is not detected. `pysidtracker playspec
unnative` replaces a spec's `native` statements with the spec statements that do
the same.

### Oracle testing

`tests/test_oracle_hvsc.py` renders each covered HVSC tune over its whole song
length with `SoundMonitorPlayer` and asserts the per-frame SID register grid
matches the
[`sidtrace`](https://github.com/anarkiwi/sidtrace) `sidplayfp` oracle frame for
frame (`pysidtracker.make_oracle_fixtures`). It is `oracle`-marked and runs in a
dedicated CI job (`pytest -m oracle`) with Docker. The same job runs
`tests/test_playspec_oracle.py`: the spec's 64 KiB (outside the stack page and
the SID window) equals the tune's own 6502 play routine's after every call of
the whole song, `tv.validate` proves the volume ramp, the filter sweep and start,
the filter register setup, each voice's glide and arpeggio step, the arpeggio
start and the instrument loader equal their 6502 subroutines in
`Only_3` for all initial memory, and bounded checking holds the spec's tempo
invariant from the tune's post-init memory. HVSC `.sid` files are
copyright works, fetched to a cache and never committed. See the base
[oracle-testing guide](https://github.com/anarkiwi/pysidtracker/blob/main/docs/oracle-testing.md).

### HVSC oracle survey

[`scripts/oracle_hvsc.py`](../scripts/oracle_hvsc.py) plugs `SoundMonitorPlayer`
into the
[`pysidtracker.survey`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/survey.md)
whole-song comparison against the `sidtrace` oracle (Docker), over every subtune
of every HVSC tune SIDId names `Soundmonitor`. Its `info` reports the `engine`
anchor the reader recognises on the materialised image (`cia_loader` or
`note_freq`), the `stage` that produced the decode (`direct`, `relocated` after
init, or `packed`), `rsid`, the
play `trigger` (`pal_video` / `ntsc_video` / `cia_timer` / `nmi_timer`, or
`null` when unknown), `speed` (play calls per PAL frame) and `dynamic` (variable
timer), the last four from `pysidtracker.cadence.cadence_info`. Tunes the reader does not recognise (e.g. other players) or cannot decode raise `SidParseError`, a
`pysidtracker.SidParseError`, so the survey counts them as `not_decompiled`.

```bash
pip install "pysidtracker[survey]>=0.43.0"
python -m pysidtracker.survey find $HVSC --player '^Soundmonitor$' --out .hvsc-survey/tunes.csv
PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC .hvsc-survey/tunes.csv \
    --work .hvsc-survey --render oracle_hvsc:render --jobs 32
python -m pysidtracker.survey report .hvsc-survey/results.csv
python -m pysidtracker.survey triage .hvsc-survey
PYTHONPATH=scripts python -m pysidtracker.survey diff $HVSC .hvsc-survey/tunes.csv <md5>_<song> \
    --work .hvsc-survey --render oracle_hvsc:render
PYTHONPATH=scripts python -m pysidtracker.survey file oracle_hvsc:render tune.sid \
    --song all --work .tunecache/csv
```

`file` gives one `.sid`'s subtunes (`--song all`, or `1,4-6`) the same
verdicts, leads and differing registers without a `find` CSV.

`results.csv` is written when `run` finishes. `.hvsc-survey/` holds
copyright-derived traces and is gitignored.

## References

- Soundmonitor (64'er / Chris Hulsbeck), HVSC tracker #6.
- [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) — shared
  container/image/detection base.
