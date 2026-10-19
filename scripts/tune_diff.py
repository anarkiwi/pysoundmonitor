"""Diff one ``.sid``'s subtunes against the sidtrace oracle, frame by frame.

Renders each subtune's player grid and the ``anarkiwi/sidtrace`` oracle grid,
then reports the best and minimum-bad lead with the differing registers.

    python scripts/tune_diff.py tune.sid --songs 1,4 --frames 250
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from pysidtracker import SidImage
from pysidtracker.oracle import (
    cached_sidtrace,
    sidtrace_cadence,
    sidtrace_grid,
)
from pysidtracker.survey import min_lead, reg_names, scan_leads

from pysoundmonitor import SoundMonitorPlayer


def _song_count(raw: bytes) -> int:
    header = SidImage.from_bytes(raw).header
    return header.songs if header is not None else 1


def diff_tune(
    tune: Path,
    oracle_cache: Path,
    *,
    songs=None,
    frames: int = 250,
    seconds: int = 8,
    chip: int = 0,
):
    """Per-subtune verdict of ``tune`` against the sidtrace oracle.

    Args:
        tune: the ``.sid``/``.prg`` to compare.
        oracle_cache: directory for cached oracle CSVs.
        songs: 1-based picks, or ``None`` for every song in the header.
        frames: number of frames to render and compare.
        seconds: oracle render seconds per subtune (cached after first run).
        chip: which SID chip's writes to frame the oracle by.

    Returns:
        List of ``(song, lead, first_bad, n_bad, reg_names)`` rows.
    """
    raw = Path(tune).read_bytes()
    count = _song_count(raw)
    picks = (
        range(1, count + 1) if songs is None else [s for s in songs if 1 <= s <= count]
    )
    out = []
    for song in picks:
        csv = Path(oracle_cache) / f"{tune.stem}.{song}.csv.zst"
        try:
            rows = cached_sidtrace(tune, csv, seconds=seconds, song=song)
            cpf = sidtrace_cadence(rows, chip=chip) or 985248
            oracle = np.asarray(
                sidtrace_grid(rows, chip=chip, cycles_per_frame=cpf), np.int16
            )[:frames]
            # Pad past the oracle for the lead scan.
            rendered = np.asarray(
                SoundMonitorPlayer(raw, song - 1).render_grid(frames + 16), np.int16
            )
        except Exception as exc:  # pylint: disable=broad-except
            print(f"song {song}: error {exc!r}")
            continue
        scan = scan_leads(oracle, rendered)
        leads, ne, size, first, nbad = scan
        i = int(np.argmax(first))
        regs = reg_names(ne[i, : size[i]].any(0))
        ml = min_lead(scan)
        print(
            f"song {song}: {len(oracle)}f lead={int(leads[i])} "
            f"first={int(first[i])} bad={int(nbad[i])}/{len(oracle)} regs={regs} "
            f"min_lead={ml['min_lead']} min_bad={ml['min_bad']} min_regs={ml['min_regs']}"
        )
        out.append((song, int(leads[i]), int(first[i]), int(nbad[i]), regs))
    return out


def _songs_arg(text, count):
    if text in ("all", ""):
        return None
    out = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out.extend(range(int(lo or 1), int(hi or (int(lo or 1))) + 1))
    return [s for s in out if 1 <= s <= count]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tune", type=Path)
    parser.add_argument("--songs", default="all", help="'all' or 1-based list/ranges")
    parser.add_argument("--frames", type=int, default=250)
    parser.add_argument("--seconds", type=int, default=8)
    parser.add_argument("--chip", type=int, default=0)
    parser.add_argument(
        "--cache", default=str(Path(__file__).resolve().parents[1] / ".oracle-cache")
    )
    args = parser.parse_args()
    raw = args.tune.read_bytes()
    diff_tune(
        args.tune,
        Path(args.cache),
        songs=_songs_arg(args.songs, _song_count(raw)),
        frames=args.frames,
        seconds=args.seconds,
        chip=args.chip,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
