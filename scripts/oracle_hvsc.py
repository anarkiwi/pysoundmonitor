"""pysoundmonitor's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv \
        --work .hvsc-survey --render oracle_hvsc:render
"""

import numpy as np
from pysidtracker import SidImage
from pysidtracker.cadence import cadence_info

from pysoundmonitor import SoundMonitorPlayer
from pysoundmonitor.reader import find_cia_fingerprint, find_fingerprint

PLAYER = "^Soundmonitor$"


def _engine(image):
    """How the reader recognises the replay: its CIA section loader or note tables."""
    if find_fingerprint(image) is None:
        return None
    return "cia_loader" if find_cia_fingerprint(image) is not None else "note_freq"


def render(data, song, nframes):
    """Render 1-based ``song`` of a Soundmonitor ``.sid`` and the build it carries."""
    grid = SoundMonitorPlayer(data, song - 1).render_grid(nframes)
    info = {"engine": _engine(SidImage.from_bytes(data))}
    info.update(cadence_info(data, song))
    return np.asarray(grid, np.int16), info
