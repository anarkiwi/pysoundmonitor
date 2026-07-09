# pysoundmonitor

Pure-Python reader and detector for **Soundmonitor** (64'er / Chris Hulsbeck)
SID tunes — HVSC tracker #6. Built on [`pysidtracker`](https://github.com/anarkiwi/pysidtracker).

The Soundmonitor player is relocatable and the SID-header addresses are not a
reliable locator, so the engine is found by a small relocation-invariant
hardware-register fingerprint and the documented section→stream song data is
decoded into a model. Scope is container/detection + a song-data reader, not a
byte-exact playback engine.

## Install

```sh
pip install pysoundmonitor
```

## Usage

```python
import pysoundmonitor as psm

song = psm.read("tune.sid")            # or psm.parse(raw_bytes)
print(song.cia_latch, len(song.sections), len(song.instruments))
for section in song.sections:
    print(section.cadence, section.tempo, section.pattern_length)

# Relocation-tolerant detection (untrustworthy header):
det = psm.SoundMonitorSidParser().detect("tune.sid")
print(det.kind)          # DIRECT / RELOCATED / PACKED / UNKNOWN
```

## Development

```sh
pip install -e ".[dev]"
pytest                    # offline synthetic fixtures
python scripts/fetch_tunes.py   # cache the HVSC reference tune (never committed)
```

Test tunes are HVSC copyright works: fetched + cached on demand (gitignored),
never committed.

## Docs

- [`docs/format.md`](docs/format.md) — Soundmonitor container, detection, and
  song-data model.

## License

Apache-2.0.
