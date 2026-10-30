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

from pathlib import Path

import pytest

from pysidtracker import make_oracle_fixtures
from pysidtracker.oracle import aligned_match
from pysidtracker.testing import oracle_grid, resolve_tune, whole_song

import helpers
from pysoundmonitor import SoundMonitorPlayer

# Synthetic RSIDs are rendered afresh each run, under the workspace (a
# Docker-daemon-visible path) but outside the download cache.
_SYNTHETIC = Path("build/synthetic")

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
    render=_render,
)


@pytest.mark.oracle
def test_render_matches_oracle(oracle_match):  # noqa: F811
    oracle_match()


# Every subtune of this tune must match the oracle exactly.
SIDEWALK = "MUSICIANS/C/Callet_Charles/Sidewalk.sid"
SIDEWALK_SONGS = 21


@pytest.mark.oracle
@pytest.mark.parametrize("song", range(1, SIDEWALK_SONGS + 1))
def test_sidewalk_subtunes_match_the_oracle(song):
    path = resolve_tune(SIDEWALK)
    assert path is not None, "tune unavailable"
    data = Path(path).read_bytes()
    oracle = oracle_grid(Path(path), song=song)
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
    sid = _SYNTHETIC / f"{name}.sid"
    sid.parent.mkdir(parents=True, exist_ok=True)
    sid.write_bytes(data)
    oracle = oracle_grid(
        sid,
        oracle_cache=sid.parent,
        seconds=whole_song(3),
        frames=whole_song(100),
        force=True,
    )
    assert len(oracle) == whole_song(100)
    assert SoundMonitorPlayer(data).render_grid(whole_song(100)) == oracle
