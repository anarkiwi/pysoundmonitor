"""pysoundmonitor's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv \
        --work .hvsc-survey --render oracle_hvsc:render
"""

import numpy as np
from pysidtracker.cadence import cadence_info

from pysoundmonitor import SoundMonitorPlayer, parse

PLAYER = "^Soundmonitor$"

# The variant's materialisation strategy, named for the survey's ``stage``.
_STAGES = {"static": "direct", "after-init": "relocated", "sys-unpack": "packed"}


def _play(data, song, nframes):
    grid = SoundMonitorPlayer(data, song - 1).render_grid(nframes)
    return np.asarray(grid, np.int16), cadence_info(data, song)


def render(data, song, nframes):
    """Render 1-based ``song`` of a Soundmonitor ``.sid`` and the build it carries.

    A tune that never materialises into a song raises ``SidParseError`` (counted
    ``not_decompiled``). ``info`` carries every variant facet with its source;
    ``stage`` names the materialisation stage (direct, relocated or packed).
    """
    variant = parse(data).variant
    grid, info = _play(data, song, nframes)
    stage = _STAGES[variant["materialisation"]]
    return grid, {**variant.as_info(), "stage": stage, **info}
