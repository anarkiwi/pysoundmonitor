"""Variant facets of a decoded Soundmonitor build.

The reader locates the song through :func:`pysidtracker.locate`; the facets here
record *how* it was found, each with its provenance, so a :class:`Song` carries
the build it came from (and the survey ``info`` can be read off it):

* ``engine`` -- the cohort the replay belongs to: the CIA-timed section loader
  or the fixed-cadence note-frequency tables;
* ``base_source`` -- whether the data-region base came from the loader's table
  operands or fell back to the image's load page;
* ``bounds_source`` -- whether the section bounds came from the section-advance
  ``CPX``/``LDX`` operands or fell back to a full ``(0, 255)`` walk;
* ``materialisation`` -- the locate stage that produced the decoded image.
"""

from __future__ import annotations

from pysidtracker.facets import Entry, Facet, FacetRegistry, Variant

FAMILY = "soundmonitor"

ENGINE_CIA = "cia_loader"
ENGINE_NOTE_FREQ = "note_freq"

SOURCE_OPERAND = "operand"
BASE_LOAD_PAGE = "load_page"
BOUNDS_DEFAULT = "default"

STAGE_STATIC = "static"
STAGE_AFTER_INIT = "after-init"
STAGE_SYS_UNPACK = "sys-unpack"

FACETS = FacetRegistry(
    [
        Facet("engine", "build", domain=(ENGINE_CIA, ENGINE_NOTE_FREQ)),
        Facet("base_source", "layout", domain=(SOURCE_OPERAND, BASE_LOAD_PAGE)),
        Facet("bounds_source", "layout", domain=(SOURCE_OPERAND, BOUNDS_DEFAULT)),
        Facet(
            "materialisation",
            "init",
            domain=(STAGE_STATIC, STAGE_AFTER_INIT, STAGE_SYS_UNPACK),
        ),
    ]
)


def make_variant(**entries: Entry) -> Variant:
    """A :class:`Variant` of one :class:`Entry` per facet, domain-checked."""
    if set(entries) != {facet.name for facet in FACETS}:
        raise ValueError(f"expected facets {[f.name for f in FACETS]}, got {entries}")
    for name, entry in entries.items():
        FACETS[name].check(entry.value)
    return Variant(entries, FAMILY)
