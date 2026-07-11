"""Test path setup: make ``scripts/`` (the HVSC fetch helper) importable.

The offline suite is built entirely from synthetic Soundmonitor-format images
(``helpers``); it never needs the network. The real-tune corpus and the
``oracle``-marked byte-exact tests fetch + cache HVSC tunes on demand -- HVSC
``.sid`` tunes are copyright works and are never committed.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
