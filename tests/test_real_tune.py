"""Optional on-demand real-tune test (skips cleanly when offline).

Uses the HVSC reference tune (``MUSICIANS/J/JAP/Only_3.sid``). The tune is a
copyright work and is never committed; it is fetched + cached on demand and the
test skips if it cannot be obtained.
"""

from pysidtracker import PlayroutineKind

from pysoundmonitor import SoundMonitorSidParser, read


def test_only3_detects(only3_path):
    parser = SoundMonitorSidParser()
    detection = parser.detect(only3_path)
    # Only_3 is a relocated $C000 build: the engine is present in the loaded
    # image, so it recognises directly (or, if packed, after init).
    assert detection.kind in (PlayroutineKind.DIRECT, PlayroutineKind.RELOCATED)
    assert detection.anchor


def test_only3_reads(only3_path):
    song = read(only3_path)
    assert song.player_anchor
    assert song.sections
    assert song.cia_latch is not None
