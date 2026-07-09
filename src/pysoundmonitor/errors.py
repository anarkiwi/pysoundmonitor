"""Exceptions for pysoundmonitor."""

from pysidtracker import SidError


class SoundMonitorError(SidError):
    """Base error for all pysoundmonitor failures."""


class SidParseError(SoundMonitorError):
    """A SID/PRG image could not be parsed as a Soundmonitor tune."""
