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
land their tables in place after init runs). Synthetic RSIDs (``helpers``) cover
each interrupt a tune can install for itself.
"""

import os
from pathlib import Path

import pytest

from pysidtracker import make_oracle_fixtures
from pysidtracker.oracle import read_sidtrace, run_sidtrace, sidtrace_grid

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
