"""Exceptions for pysoundmonitor.

Built with :func:`pysidtracker.make_package_errors`, so the leaf parse error
subclasses BOTH the package root and the base :class:`pysidtracker.SidParseError`;
a caller's ``except SidParseError`` (base) therefore catches this package's errors.
"""

from pysidtracker import make_package_errors

_ERRORS = make_package_errors("SoundMonitor")

SoundMonitorError = _ERRORS.error
SidParseError = _ERRORS.parse_error
SoundMonitorFormatError = _ERRORS.format_error
