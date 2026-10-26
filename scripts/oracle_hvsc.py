"""pysoundmonitor's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv \
        --work .hvsc-survey --render oracle_hvsc:render
"""

import numpy as np
from pysidtracker.cadence import cadence_info

from pysoundmonitor import SoundMonitorPlayer
from pysoundmonitor.reader import find_cia_fingerprint, materialization

PLAYER = "^Soundmonitor$"


def _engine(image):
    """How the reader recognises the replay: its CIA section loader or note tables."""
    return "cia_loader" if find_cia_fingerprint(image) is not None else "note_freq"


def _play(data, song, nframes):
    grid = SoundMonitorPlayer(data, song - 1).render_grid(nframes)
    return np.asarray(grid, np.int16), cadence_info(data, song)


def render(data, song, nframes):
    """Render 1-based ``song`` of a Soundmonitor ``.sid`` and the build it carries.

    A tune that never materialises into a song raises ``SidParseError`` (counted
    ``not_decompiled``); ``engine`` is read off the materialised image and
    ``stage`` names the stage that produced it (direct, relocated or packed).
    """
    image, stage = materialization(data)
    grid, info = _play(data, song, nframes)
    return grid, {"engine": _engine(image), "stage": stage, **info}
