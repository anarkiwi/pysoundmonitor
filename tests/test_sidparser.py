"""SoundMonitorSidParser + detect() classification tests."""

import pytest

from pysidtracker import PlayroutineKind, SidImage

import helpers
from pysoundmonitor import SidParseError, SoundMonitorSidParser


def test_parse_delegates_to_reader():
    data, anchor = helpers.build_sid()
    song = SoundMonitorSidParser().parse(data)
    assert song.player_anchor == anchor


def test_recognize_returns_anchor():
    data, anchor = helpers.build_sid()
    image = SidImage.from_bytes(data)
    assert SoundMonitorSidParser().recognize(image) == anchor


def test_recognize_none_when_absent():
    image = SidImage.from_bytes(helpers.build_no_fingerprint())
    assert SoundMonitorSidParser().recognize(image) is None


def test_detect_direct():
    data, _ = helpers.build_sid()
    detection = SoundMonitorSidParser().detect(data)
    assert detection.kind is PlayroutineKind.DIRECT
    assert detection.trustworthy_header
    assert detection.anchor


def test_detect_unknown_without_init():
    data = helpers.build_no_fingerprint()
    detection = SoundMonitorSidParser().detect(data, init=False)
    assert detection.kind is PlayroutineKind.UNKNOWN


def test_error_class():
    assert SoundMonitorSidParser.error_class is SidParseError


def test_read_dispatch(tmp_path):
    data, _ = helpers.build_sid()
    path = tmp_path / "t.sid"
    path.write_bytes(data)
    song = SoundMonitorSidParser().read(str(path))
    assert song.base == 0x1000


def test_parse_bad_data_raises():
    with pytest.raises(SidParseError):
        SoundMonitorSidParser().parse(helpers.build_no_fingerprint())
