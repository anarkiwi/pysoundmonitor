"""Pure-Python reader and detector for Soundmonitor (64'er / Hulsbeck) SID tunes.

Soundmonitor is HVSC tracker #6. Its player is relocatable and the SID-header
addresses are not a reliable locator, so this package finds the engine by a
small hardware-register fingerprint and decodes the documented section->stream
song-data structures into a :class:`Song` model. Scope is the container/detection
plus a song-data reader -- not a byte-exact playback engine.
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
from .reader import find_fingerprint, parse, read
from .sidparser import SoundMonitorSidParser

__version__ = "0.1.0"

__all__ = [
    "FilterTail",
    "Instrument",
    "NoteFreqTable",
    "Row",
    "Section",
    "SidParseError",
    "Song",
    "SoundMonitorError",
    "SoundMonitorSidParser",
    "__version__",
    "find_fingerprint",
    "parse",
    "read",
]
