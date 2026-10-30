# pysoundmonitor

Pure-Python player, reader and detector for **Soundmonitor** (64'er / Chris
Hulsbeck) C64 SID tunes (HVSC tracker #6).

Consumes `.sid` files (PSID/RSID containers) and bare `.prg` images through the
shared [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) base: the
Soundmonitor player is relocatable, so it is located by a relocation-invariant
hardware-register fingerprint and packed/relocating builds are detected by
running the tune's init in a 6502 emulator — container headers are not trusted.
`SoundMonitorPlayer` reproduces the replay byte-exact (validated against the
`sidtrace` oracle) by running the playroutine as a `pysidtracker.playspec`
spec (`specs/soundmonitor.play`) on the memory the tune's own `init` leaves.

## Install

```bash
pip install pysoundmonitor
```

## Usage

```python
import pysoundmonitor as psm

song = psm.read("tune.sid")            # path; or psm.parse(raw_bytes)
print(song.cia_latch, len(song.sections), len(song.instruments))
for section in song.sections:
    print(section.cadence, section.tempo, section.pattern_length)

# Byte-exact playback (a playspec SpecPlayer over the tune's post-init memory):
player = psm.SoundMonitorPlayer(open("tune.sid", "rb").read())
grid = player.render_grid(250)         # 25-reg, forward-filled per-frame grid
writes = list(player.register_writes(250))   # pysidtracker RegWrite log
player.render_wav("tune.wav", 250)     # emulated SID (pyresidfp)

# Relocation-tolerant detection (untrustworthy header):
det = psm.SoundMonitorSidParser().detect("tune.sid")
print(det.kind)                        # DIRECT / RELOCATED / PACKED / UNKNOWN
```

## Development

```bash
pip install -e ".[dev]"
pysid-tune-fetch --tests tests     # pre-cache the suite's HVSC tunes (never committed)
pytest                             # offline synthetic fixtures + HVSC corpus
pytest -m oracle -n auto           # byte-exact vs the sidtrace Docker oracle
```

Test tunes are HVSC copyright works: fetched and cached on demand (gitignored),
never committed. The `oracle` job needs Docker (`anarkiwi/sidtrace`) and renders
each covered tune with `SoundMonitorPlayer`, asserting a frame-exact match — see
[docs/format.md](docs/format.md#oracle-testing).

See [docs/format.md](docs/format.md) for the container, detection, song-data
model and [song grammar](docs/format.md#song-grammar), and its
[HVSC oracle survey](docs/format.md#hvsc-oracle-survey) section for the
whole-song comparison over every HVSC Soundmonitor tune.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
