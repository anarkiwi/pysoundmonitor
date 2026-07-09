"""The :class:`~pysidtracker.BaseSidParser` implementation for Soundmonitor."""

from __future__ import annotations

from typing import Any

from pysidtracker import BaseSidParser, SidImage

from .errors import SidParseError
from .model import Song
from .reader import find_fingerprint, parse


class SoundMonitorSidParser(BaseSidParser):
    """Parse Soundmonitor tunes and recognise the engine for ``detect()``.

    ``recognize`` returns the address of a tiny relocation-invariant fingerprint
    (the CIA Timer-A latch programming in the section loader), so a directly
    loaded tune classifies as :attr:`PlayroutineKind.DIRECT` and a
    packed/relocated one as :attr:`PlayroutineKind.RELOCATED`/``PACKED`` after
    the base library emulates its init.
    """

    error_class: type = SidParseError

    def parse(self, data: bytes, **kwargs: Any) -> Song:
        """Decode ``data`` into a :class:`~pysoundmonitor.model.Song`."""
        return parse(data)

    def recognize(self, image: SidImage) -> object:
        """Return the fingerprint address if the engine is present, else ``None``."""
        return find_fingerprint(image)
