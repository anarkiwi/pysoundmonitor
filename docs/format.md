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
loader's own `LDA $pp00,X` table-indexing operands (the smallest page whose
voice + section-header table geometry is all present), falling back to the image
load address — no baked addresses.

## Data model

Layout, in byte offsets from the data base:

| offset | table |
|---|---|
| `+$000/$100` | voice-0 stream pointer lo/hi (per section) |
| `+$200/$300` | voice-0 transpose |
| `+$400/$500`,`+$600/$700` | voice-1 stream pointer / transpose |
| `+$800/$900`,`+$A00/$B00` | voice-2 stream pointer / transpose |
| `+$C00/$D00` | section-header pointer lo/hi |
| `+$E00` | instrument records (`index * 24`) |

**Section** = one song-step. Its header holds `[0..1]` CIA latch (play cadence =
`latch + 1` cycles/call), `[2]` tempo divider, `[3]` pattern length, `[4]`
volume-ramp config, `[5]` base-volume nibble, `[6..13]` an 8-entry arp-note
table. Each section has three per-voice pattern streams.

The played section range is `sec_start .. sec_last` (inclusive, wrapping mod
256). Those bounds are player globals, so rather than walk the pointer table into
the `MAX_SECTIONS` cap the reader recovers them from the section-advance code's
own `CPX sec_last` / `LDX sec_start` operands (two adjacent globals), and picks
the base + bounds combination that decodes the largest run of valid sections.

**Pattern stream** = a flat array of 2-byte `[note][ctrl]` rows. `note == $00` is
a rest, `note == $80` a tie/hold; otherwise `note` bit7 is the new-note key-on
trigger and bits0-6 a freq-table index. `ctrl` bits: 0-3 instrument index, bit4
portamento, bit5 absolute (skip section transpose), bit6 arp/glide enable, bit7
clear adds the per-section instrument base.

**Instrument record** = 16 bytes: `0` CTRL+gate, `1` AD, `2` SR, `3` glide-rate
lo, `4` PW seed, `5` PW up-dwell, `6` PW down-dwell, `7` PW step, `8` sustain
CTRL, `9` mode, `10` glide-enable, `11` glide-rate hi, `12` vibrato depth, `13`
vibrato period/dir, `14` vibrato delay, `15` detune. Byte `16` is a marker: if
not `$FF`, an 8-byte global-filter tail (`vol/mode, res/filt, cutoff-hi,
up-dwell, down-dwell, step, bound, flags`) follows (24-byte record stride).

**Note-frequency tables** = two parallel per-semitone tables (freq hi then lo,
95 entries each, abutting). The player reads them with paired `LDA NoteFreqHi,X`
/ `LDA NoteFreqLo,X`, so they are located relocation-tolerantly from those two
absolute-indexed operands (their difference is the table length) validated by the
hi table's octave-ramp signature.

## Player and playback notes

`SoundMonitorPlayer` is a [`pysidtracker.TunePlayer`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/irqhost.md)
subclass that reproduces the replay byte-exact. Rather than re-transcribe the
copyrighted, relocatable player for each build, it runs the tune's **own** 6502
`init`/`play` over the shared 64 KiB memory image (with the VIC-raster and
voice-3 SID read-back observers the replay samples), reading back the SID
register file each frame:

```python
from pysoundmonitor import SoundMonitorPlayer

player = SoundMonitorPlayer(open("tune.sid", "rb").read())
grid = player.render_grid(250)               # 25-reg, forward-filled per frame
writes = list(player.register_writes(250))   # pysidtracker RegWrite log
player.render_wav("tune.wav", 250)           # pysidtracker.audio, emulated SID
```

Both replay variations are reproduced and validated: the **CIA-timed** cohort
(self-scheduled cadence via the `$DC04`/`$DC05` latch, e.g. `Only_3`, `Defu`,
`Charts`) and the **fixed-cadence** PAL-video cohort (e.g. `Fi`, `Big_Boing`,
`Into_Orbit`), across directly loaded `$A000` and relocated `$6xxx`/`$9xxx`
builds. `SoundMonitorPlayer.cadence` derives the play period from what init
programs; per-section cadence is also exposed on the model (`latch + 1`
cycles/call, and `song.cia_latch`).

### Tunes that install their own interrupt

A header play address of 0 (every Soundmonitor RSID) means init installs the
play handler itself. `TunePlayer` then plays the tune under
[`pysidtracker.irqhost.InterruptPlayer`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/irqhost.md),
which boots and runs it as the sidtrace oracle's sidplayfp does: the PSID driver
from power-on, every interrupt the tune programs (VIC raster, CIA #1 Timer A/B
IRQs, CIA #2 Timer A/B NMIs) and the cycles the VIC steals for badlines and
sprites, cycle for cycle. Its trace equals the oracle's, so an NMI sample player
beside the music IRQ, or a main loop writing `$D418`, renders byte-exact.
`render_grid` frames the rows at the play source's raises exactly as the oracle
grid is framed; `play_frame` advances one such frame. A tune whose interrupts
never run raises `SidParseError` when rendered.

### Oracle testing

`tests/test_oracle_hvsc.py` renders each covered HVSC tune with
`SoundMonitorPlayer` and asserts the per-frame SID register grid matches the
[`sidtrace`](https://github.com/anarkiwi/sidtrace) `sidplayfp` oracle frame for
frame (`pysidtracker.make_oracle_fixtures`). It is `oracle`-marked and runs in a
dedicated CI job (`pytest -m oracle`) with Docker; HVSC `.sid` files are
copyright works, fetched to a cache and never committed. See the base
[oracle-testing guide](https://github.com/anarkiwi/pysidtracker/blob/main/docs/oracle-testing.md).

### HVSC oracle survey

[`scripts/oracle_hvsc.py`](../scripts/oracle_hvsc.py) plugs `SoundMonitorPlayer`
into the
[`pysidtracker.survey`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/survey.md)
whole-song comparison against the `sidtrace` oracle (Docker), over every subtune
of every HVSC tune SIDId names `Soundmonitor`. Its `info` reports the `engine`
anchor the reader recognises (`cia_loader`, `note_freq` or `null`), `rsid`, the
play `trigger` (`pal_video` / `ntsc_video` / `cia_timer` / `nmi_timer`, or
`null` when unknown), `speed` (play calls per PAL frame) and `dynamic` (variable
timer), the last four from `pysidtracker.cadence.cadence_info`. Tunes the player rejects raise `SidParseError`, a
`pysidtracker.SidParseError`, so the survey counts them as `not_decompiled`.

```bash
pip install "pysidtracker[survey]>=0.28.0"
python -m pysidtracker.survey find $HVSC --player '^Soundmonitor$' --out .hvsc-survey/tunes.csv
PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC .hvsc-survey/tunes.csv \
    --work .hvsc-survey --render oracle_hvsc:render --jobs 32
python -m pysidtracker.survey report .hvsc-survey/results.csv
python -m pysidtracker.survey triage .hvsc-survey
PYTHONPATH=scripts python -m pysidtracker.survey diff $HVSC .hvsc-survey/tunes.csv <md5>_<song> \
    --work .hvsc-survey --render oracle_hvsc:render
PYTHONPATH=scripts python -m pysidtracker.survey file oracle_hvsc:render tune.sid \
    --song all --frames 250 --work .oracle-cache
```

`file` gives one `.sid`'s subtunes (`--song all`, or `1,4-6`) the same
verdicts, leads and differing registers without a `find` CSV.

`results.csv` is written when `run` finishes. `.hvsc-survey/` holds
copyright-derived traces and is gitignored.

## References

- Soundmonitor (64'er / Chris Hulsbeck), HVSC tracker #6.
- [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) — shared
  container/image/detection base.
