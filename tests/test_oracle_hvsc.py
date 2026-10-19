"""Byte-exact comparison of :class:`SoundMonitorPlayer` against the sidtrace oracle.

Marked ``oracle``: these tests need Docker (the ``anarkiwi/sidtrace`` image) and
network access to HVSC, so the default suite excludes them (see ``pyproject``); a
dedicated CI job runs ``pytest -m oracle``. They are never skipped -- an
unavailable tune or a failed oracle render fails the test rather than hiding a
regression. HVSC ``.sid`` files are copyright works: they are downloaded to a
cache (or a local ``$HVSC`` tree), never committed.

The tunes span both Soundmonitor replay variations:

* the **CIA-timed** cohort, whose section loader latches ``$DC04``/``$DC05`` and
  drives a self-scheduled (dynamic) cadence (``only_3``, ``defu``, ``charts``);
* the **fixed-cadence** cohort, recognised by its note-frequency tables and run
  off the PAL video frame (``fi``, ``big_boing``, ``into_orbit``, ``megsa``,
  ``over_and_over``),

and both directly loaded ``$A000`` Hulsbeck builds and relocated ``$6xxx``/
``$9xxx`` builds (``charts``, ``big_boing``, ``into_orbit`` -- the last two only
land their tables in place after init runs). ``sidewalk`` is the issue #20
case: play address 0 (the init installs its own interrupt) and 21 subtunes,
whose frame-0 ``v1_freq_hi`` must stay the oracle's 0 baseline. Synthetic RSIDs
(``helpers``) cover each interrupt a tune can install for itself.
"""

import os
from pathlib import Path

import pytest

from pysidtracker import make_oracle_fixtures
from pysidtracker.oracle import (
    aligned_match,
    cached_sidtrace,
    read_sidtrace,
    run_sidtrace,
    sidtrace_cadence,
    sidtrace_grid,
)
from pysidtracker.testing import resolve_tune

import helpers
from pysoundmonitor import SoundMonitorPlayer

# Cache under the workspace (a Docker-daemon-visible path, and what CI persists
# via actions/cache). ``$PYSOUNDMONITOR_ORACLE_CACHE`` overrides the location.
_CACHE = Path(os.environ.get("PYSOUNDMONITOR_ORACLE_CACHE", ".oracle-cache"))

TUNES = {
    # CIA-timed, self-scheduled cadence (section loader latches $DC04/$DC05).
    "only_3": "MUSICIANS/J/JAP/Only_3.sid",
    "defu": "MUSICIANS/N/Nagie_Sascha/Defu.sid",
    "charts": "MUSICIANS/T/Tel_Jeroen/Charts.sid",  # relocated $6ff5 build
    # Fixed-cadence (PAL video), located via the note-frequency tables.
    "fi": "MUSICIANS/V/Vulgarik/Fi.sid",
    "megsa": "MUSICIANS/M/Mitch_and_Dane/Mitch/Megsa_Tune.sid",
    "over_and_over": "MUSICIANS/G/Gilmore_Adam/Over_and_Over.sid",
    "big_boing": "DEMOS/A-F/Big_Boing.sid",  # relocated $6000, decode-after-init
    "into_orbit": "MUSICIANS/D/Danko_Tomas/Into_Orbit.sid",  # relocated $9ff0
}


def _render(data, nframes):
    return SoundMonitorPlayer(data).render_grid(nframes)


tune_id, oracle_match = make_oracle_fixtures(
    TUNES,
    hvsc_cache=_CACHE / "hvsc",
    oracle_cache=_CACHE / "csv",
    render=_render,
    frames=250,
)


@pytest.mark.oracle
def test_render_matches_oracle(oracle_match):  # noqa: F811
    oracle_match()


# Issue #20: 20 of these 21 subtunes once differed only in frame-0 v1_freq_hi; every one must now be exact.
SIDEWALK = "MUSICIANS/C/Callet_Charles/Sidewalk.sid"
SIDEWALK_SONGS = 21
SIDEWALK_FRAMES = 250


@pytest.mark.oracle
@pytest.mark.parametrize("song", range(1, SIDEWALK_SONGS + 1))
def test_sidewalk_subtunes_match_the_oracle(song):
    path = resolve_tune(SIDEWALK, cache_dir=_CACHE / "hvsc", local_env="HVSC")
    assert path is not None, "tune unavailable"
    data = Path(path).read_bytes()
    csv = _CACHE / "csv" / f"sidewalk-{song}.csv.zst"
    seconds = SIDEWALK_FRAMES // 50 + 2
    rows = cached_sidtrace(Path(path), csv, seconds=seconds, song=song)
    oracle = sidtrace_grid(rows, cycles_per_frame=sidtrace_cadence(rows))[
        :SIDEWALK_FRAMES
    ]
    rendered = SoundMonitorPlayer(data, song - 1).render_grid(len(oracle))
    assert aligned_match(
        oracle, rendered, max_lead=4
    ), f"sidewalk song {song}: player render does not match the sidtrace oracle"


@pytest.mark.oracle
@pytest.mark.parametrize(
    "name, kwargs",
    [
        ("raster", {"source": "raster"}),
        ("raster_rmw", {"source": "raster", "ack": 0xEE}),
        ("cia_kernal", {"source": "cia"}),
        ("cia_ram", {"source": "cia", "bank": 0x35}),
        ("nmi_kernal", {"source": "nmi"}),
        ("nmi_ram", {"source": "nmi", "bank": 0x35}),
    ],
)
def test_installed_interrupt_matches_oracle(name, kwargs):
    data = helpers.build_irq_sid(**kwargs)
    sid = _CACHE / "synthetic" / f"{name}.sid"
    sid.parent.mkdir(parents=True, exist_ok=True)
    sid.write_bytes(data)
    trace = run_sidtrace(sid, sid.with_suffix(".csv.zst"), seconds=3)
    oracle = sidtrace_grid(read_sidtrace(trace))[:100]
    assert len(oracle) == 100
    assert SoundMonitorPlayer(data).render_grid(100) == oracle
