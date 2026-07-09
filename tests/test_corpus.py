"""Real HVSC Soundmonitor corpus test (skips cleanly when HVSC is absent).

A deterministic, representative sample of real HVSC Soundmonitor tunes -- spread
across DEMOS/MUSICIANS, authors A-Z, load addresses ($A000 Hulsbeck/64'er builds
plus relocated $6xxx-$9xxx builds), and both CIA-timed and fixed-cadence builds.
The sample was drawn deterministically from the tunes ``sidid`` classifies as
Soundmonitor in the local HVSC tree.

Tunes are HVSC copyright works and are **never** committed; each is fetched +
cached on demand (honouring ``$HVSC``), and the whole module SKIPS when the local
tree is not present so CI stays offline. It RUNS for real against ``$HVSC``.

Each tune must:
  * ``parse``/``read`` into a :class:`~pysoundmonitor.model.Song`;
  * ``SoundMonitorSidParser().detect()`` classify it (recognised);
  * locate its note-frequency tables (gap #1 -- the locator finds them on real
    tunes, not just the synthetic fixtures); and
  * decode a bounded, sane section set -- non-empty and strictly under the
    ``MAX_SECTIONS`` walk cap (gap #2 -- section bounds come from the player's
    own operands, not a walk that runs a relocation-dependent table into the cap).
"""

import os
import sys
from pathlib import Path

import pytest

from pysidtracker import PlayroutineKind

from pysoundmonitor import SoundMonitorSidParser, constants as c, read

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import fetch_tunes  # noqa: E402  (after sys.path tweak)

# Deterministic representative sample (HVSC-relative paths only -- no tune bytes).
CORPUS = (
    "MUSICIANS/J/JAP/Only_3.sid",
    "DEMOS/0-9/1988_Carat_tune_1.sid",
    "DEMOS/A-F/Future_Spy.sid",
    "DEMOS/M-R/Rape_1_end.sid",
    "DEMOS/UNKNOWN/Appedix_Demo_5.sid",
    "DEMOS/UNKNOWN/La_Bamba.sid",
    "DEMOS/UNKNOWN/Space_Wave.sid",
    "MUSICIANS/B/Bain_David/Twist_of_Fate.sid",
    "MUSICIANS/B/Becher_Patrick/Lets_Dream_It.sid",
    "MUSICIANS/D/DRAX/Kermit.sid",
    "MUSICIANS/D/Danko_Tomas/To_All_My_Friends.sid",
    "MUSICIANS/D/Detert_Thomas/Zoolook_89.sid",
    "MUSICIANS/D/Drake/Turbo_Hack_v1.sid",
    "MUSICIANS/G/Gray_Matt/Crazy_Comets_Special_Re-Mix.sid",
    "MUSICIANS/H/Honey/Gehirn-Analyse_3.sid",
    "MUSICIANS/K/Kaze/Gab_2.sid",
    "MUSICIANS/K/Kruki_2003/Nervous.sid",
    "MUSICIANS/M/Mad-Max/Bullshit_III.sid",
    "MUSICIANS/M/Mitch_and_Dane/Mitch/Megsa_Tune.sid",
    "MUSICIANS/N/Nagie_Sascha/Defu.sid",
    "MUSICIANS/N/Nizze/Sonate.sid",
    "MUSICIANS/R/Rock/Country-side_Song.sid",
    "MUSICIANS/R/Rosenstiel_Joerg/Flash_Hammer.sid",
    "MUSICIANS/R/Rosenstiel_Joerg/Streetfire.sid",
    "MUSICIANS/S/SIDwave/Z-Circle.sid",
    "MUSICIANS/S/Schubert_Michael/Always.sid",
    "MUSICIANS/S/Speedcracker/In_Concert.sid",
    "MUSICIANS/T/Tel_Jeroen/Charts.sid",
    "MUSICIANS/T/The_Insomniac/Rockmonitor_6.sid",
    "MUSICIANS/U/Unknown_Composer/Freedom_for_NSG.sid",
    "MUSICIANS/V/Vulgarik/Fi.sid",
    "MUSICIANS/Z/Zoolex/Mata_Max_tune_03.sid",
    "DEMOS/A-F/Bidemo_tune_3.sid",
    "MUSICIANS/D/Danko_Tomas/Into_Orbit.sid",
    "MUSICIANS/G/Gilmore_Adam/Over_and_Over.sid",
    "MUSICIANS/S/Schubert_Michael/JMS_Sounds_2.sid",
    # A relocated build whose song tables only land in place after init runs;
    # exercises the emulate-init-then-decode path on a real tune.
    "DEMOS/A-F/Big_Boing.sid",
)


def _corpus_available() -> bool:
    """True when the real corpus can be obtained without hitting the network."""
    if os.environ.get("HVSC"):
        return True
    return (fetch_tunes.CACHE / CORPUS[0]).exists()


pytestmark = pytest.mark.skipif(
    not _corpus_available(),
    reason="HVSC tree unavailable (set $HVSC to run the real corpus test)",
)


def _fetch(rel: str) -> str:
    try:
        return str(fetch_tunes.fetch(rel))
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"corpus tune unavailable: {rel}: {exc}")
        return ""


@pytest.mark.parametrize("rel", CORPUS)
def test_corpus_tune_decodes(rel):
    path = _fetch(rel)
    parser = SoundMonitorSidParser()

    detection = parser.detect(path)
    assert detection.kind in (PlayroutineKind.DIRECT, PlayroutineKind.RELOCATED)
    assert detection.anchor

    song = read(path)
    assert song.player_anchor
    # gap #2: a bounded, sane section set (never the walk cap, never empty).
    assert 0 < len(song.sections) < c.MAX_SECTIONS
    assert song.cia_latch is not None
    # gap #1: the note-frequency tables are located on the real tune.
    assert song.note_freq is not None
    assert len(song.note_freq.hi) == len(song.note_freq.lo) == c.NOTE_FREQ_LEN
