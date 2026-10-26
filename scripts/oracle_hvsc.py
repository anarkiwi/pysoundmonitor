"""pysoundmonitor's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv \
        --work .hvsc-survey --render oracle_hvsc:render
"""

import numpy as np
from pysidtracker import SidImage
from pysidtracker.cadence import cadence_info

from pysoundmonitor import SidParseError, SoundMonitorPlayer, parse
from pysoundmonitor.reader import engine_entry

PLAYER = "^Soundmonitor$"


def _build(data):
    """The build the reader recognises: its facets, else the raw image's engine.

    A tune that decodes reports every variant facet with its source; one that
    only carries the engine fingerprint (an unexpanded packed stream) reports the
    engine alone, and a non-Soundmonitor image reports none.
    """
    try:
        return parse(data).variant.as_info()
    except SidParseError:
        engine = engine_entry(SidImage.from_bytes(data))
        return {"engine": engine.value if engine is not None else None}


def render(data, song, nframes):
    """Render 1-based ``song`` of a Soundmonitor ``.sid`` and the build it carries."""
    grid = SoundMonitorPlayer(data, song - 1).render_grid(nframes)
    info = _build(data)
    info.update(cadence_info(data, song))
    return np.asarray(grid, np.int16), info
