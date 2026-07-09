# Soundmonitor container, detection, and song-data model

Summary of what `pysoundmonitor` decodes. Structural facts are transcribed from
the reverse-engineering reference (a private repo); no player or tune bytes are
reproduced here.

## Container

A Soundmonitor tune is a PSID/RSID (or bare `.prg`) wrapping a C64 memory image
of player code plus per-tune song data. `pysidtracker` unwraps the container and
places the image at its load address. The player is **relocatable** and the
header `load`/`init`/`play` addresses are not a trustworthy locator (a relocated
`$C000` build is the norm).

## Detection (relocation-tolerant)

The engine is CIA-timed and programs its **own** play period: the section loader
reads the CIA-1 Timer-A latch from the per-section header and writes it to the
fixed hardware registers `$DC04`/`$DC05`, guarding the hi byte with `CMP #$06`.
Those addresses never relocate, so the sequence

```
STA $DC04  ...  CMP #$06  ...  STA $DC05
```

is a load-address-independent fingerprint. `recognize()` returns its address;
`SoundMonitorSidParser().detect()` then classifies the tune as `DIRECT` (found as
loaded) or, via the base library's init emulation, `RELOCATED`/`PACKED`.

## Data-region base

The data region is a run of page-aligned split (lo/hi) pointer and transpose
tables indexed by the section number. Its base page is recovered from the
loader's own `LDA $pp00,X` table-indexing operands (the smallest page whose
voice + section-header table geometry is all present), falling back to the image
load address — no baked addresses.

## Song model

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

**Note-frequency tables** = two parallel per-semitone tables (freq hi then lo);
the hi table is an octave ramp and is located by that content signature.

## Scope

`pysoundmonitor` decodes the container, detection anchor, and song-data
structures into a model. A byte-exact per-frame playback engine is intentionally
out of scope: reproducing it faithfully would require material derived from the
copyrighted player.
