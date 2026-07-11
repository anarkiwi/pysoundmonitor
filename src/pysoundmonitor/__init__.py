"""Pure-Python player, reader and detector for Soundmonitor (64'er / Hulsbeck).

Soundmonitor is HVSC tracker #6. Its replay is relocatable and the SID-header
addresses are not a reliable locator, so this package finds the engine by a small
hardware-register fingerprint and decodes the documented section->stream song
structures into a :class:`Song` model. :class:`SoundMonitorPlayer` reproduces the
replay byte-exact (validated against the ``sidtrace`` oracle) by running the
tune's own 6502 ``init``/``play`` over the shared :class:`~pysidtracker.MemPlayer`.
"""

from .errors import SidParseError, SoundMonitorError
from .model import (
    FilterTail,
    Instrument,
    NoteFreqTable,
    Row,
    Section,
    Song,
)
from .player import SoundMonitorPlayer, SoundMonitorSidParser
from .reader import find_fingerprint, parse, read

__version__ = "0.3.0"

__all__ = [
    "FilterTail",
    "Instrument",
    "NoteFreqTable",
    "Row",
    "Section",
    "SidParseError",
    "Song",
    "SoundMonitorError",
    "SoundMonitorPlayer",
    "SoundMonitorSidParser",
    "__version__",
    "find_fingerprint",
    "parse",
    "read",
]
