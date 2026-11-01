"""The Soundmonitor playspec: synthetic songs, and the tune's own 6502 as reference."""

import pytest

from pysidtracker import SidImage

from pysidtracker.irqhost import InterruptPlayer
from pysidtracker.oracle import EmuPlayer
from pysidtracker.playspec.unnative import equivalent, unnative
from pysidtracker.testing import whole_song

import helpers
from pysoundmonitor import SoundMonitorPlayer, SidParseError, playspec
from pysoundmonitor import constants as c
from pysoundmonitor.formats import soundmonitor_format

RAM = ((0, 0x100), (0x200, 0xD400), (0xD800, 0x10000))
FIRST_ROW = 8
TEMPO = 1
STEP = TEMPO + 1
SID_CTRL, SID_AD, SID_SR = 4, 5, 6
VOLUME = 0x18

# Instrument 1: gate-on triangle, AD 9, SR $F0, sustain control $40, no effects.
PLAIN = (
    bytes((0x41, 0x09, 0xF0, 0, 0, 0, 0, 0, 0x40))
    + bytes(7)
    + bytes((0xFF,))
    + bytes(7)
)


def song(voice0, *, patlen=None, **section):
    rows = voice0 + [(0, 0)] * (patlen or len(voice0) - len(voice0))
    quiet = [(0, 0)] * len(rows)
    return helpers.make_section(
        tempo=TEMPO,
        patlen=len(rows),
        voices=[rows, quiet, quiet],
        **section,
    )


def play(sections, frames, instruments=None):
    data = helpers.build_engine_sid(sections, instruments or {1: PLAIN})
    return SoundMonitorPlayer(data).render_grid(whole_song(frames))


def freq(index):
    """The synthetic note table's frequency for ``index`` (hi ramp, lo = 16 * index)."""
    return (1 + index * 0xFE // (c.NOTE_FREQ_LEN - 1), (index * 0x10) & 0xFF)


def test_first_row_starts_the_note():
    grid = play([song([(0x85, 0x01)])], FIRST_ROW + 1)
    row = grid[FIRST_ROW]
    hi, lo = freq(5)
    assert (row[0], row[1]) == (lo, hi)
    assert (row[SID_CTRL], row[SID_AD], row[SID_SR]) == (0x41, 0x09, 0xF0)


def test_rows_advance_at_the_section_tempo():
    voice0 = [(0x81, 0x01), (0x00, 0x00), (0x83, 0x21), (0x80, 0x00)]
    grid = play([song(voice0)], FIRST_ROW + 4 * STEP)
    notes = [
        (grid[FIRST_ROW + r * STEP][1], grid[FIRST_ROW + r * STEP][0]) for r in range(4)
    ]
    assert notes[0] == freq(1)[::1]
    assert notes[2] == freq(3)
    assert notes[3] == notes[2]


def test_rest_drops_the_gate_and_tie_holds_the_voice():
    voice0 = [(0x81, 0x01), (0x00, 0x00), (0x83, 0x21), (0x80, 0x00)]
    grid = play([song(voice0)], FIRST_ROW + 4 * STEP)
    ctrl = [grid[FIRST_ROW + r * STEP][SID_CTRL] for r in range(4)]
    assert ctrl == [0x41, 0x40, 0x41, 0x41]


def test_the_song_loops_to_its_first_section():
    voice0 = [(0x81, 0x01), (0x00, 0x00)]
    grid = play([song(voice0)], FIRST_ROW + 4 * STEP)
    assert grid[FIRST_ROW][:7] == grid[FIRST_ROW + 2 * STEP][:7]


def test_section_transpose_moves_notes_but_not_absolute_ones():
    voice0 = [(0x81, 0x01), (0x81, 0x21)]
    grid = play([song(voice0, transpose=(2, 0, 0))], FIRST_ROW + 2 * STEP)
    relative, absolute = (grid[FIRST_ROW + r * STEP] for r in range(2))
    assert (relative[1], relative[0]) == freq(3)
    assert (absolute[1], absolute[0]) == freq(1)


def test_the_instrument_base_offsets_instruments_unless_the_row_is_absolute():
    other = bytes((0x21, 0x07, 0xA0)) + PLAIN[3:]
    instruments = {1: PLAIN, 2: other}
    voice0 = [(0x81, 0x01), (0x81, 0x81)]
    grid = play([song(voice0, ibase=(1, 0, 0))], FIRST_ROW + 2 * STEP, instruments)
    based, absolute = (grid[FIRST_ROW + r * STEP] for r in range(2))
    assert (based[SID_AD], based[SID_SR]) == (0x07, 0xA0)
    assert (absolute[SID_AD], absolute[SID_SR]) == (0x09, 0xF0)


def test_sections_play_in_order():
    first, second = song([(0x81, 0x01)] * 2), song([(0x85, 0x01)] * 2)
    grid = play([first, second], FIRST_ROW + 2 * 2 * STEP)
    assert (grid[FIRST_ROW][1], grid[FIRST_ROW][0]) == freq(1)
    assert (grid[FIRST_ROW + 2 * STEP][1], grid[FIRST_ROW + 2 * STEP][0]) == freq(5)


def test_base_volume_is_the_low_nibble():
    grid = play([song([(0x81, 0x01)], base_vol=0x0A)], 3)
    assert {row[VOLUME] & 0x0F for row in grid} == {0x0A}


def test_a_muted_voice_is_not_read():
    data = helpers.build_engine_sid([song([(0x81, 0x01)])], {1: PLAIN})
    player = SoundMonitorPlayer(data)
    player._mem[0x02C6] = 1  # pylint: disable=protected-access
    row = player.render_grid(whole_song(FIRST_ROW + 1))[FIRST_ROW]
    assert row[SID_CTRL] == 0 and row[SID_AD] == 0


def test_loop_mode_silences_the_voices_at_the_end():
    data = helpers.build_engine_sid([song([(0x81, 0x01)])], {1: PLAIN})
    player = SoundMonitorPlayer(data)
    flag = playspec.binding(player.view(), player.entry)["init_flag"]
    player._mem[flag] = 2  # pylint: disable=protected-access
    grid = player.render_grid(whole_song(FIRST_ROW + 6 * STEP))
    assert grid[FIRST_ROW][SID_CTRL] == 0x41
    assert [grid[-1][reg] for reg in (4, 11, 18, 23)] == [0, 0, 0, 0]
    assert grid[-1][VOLUME] == 0x0F


def test_binding_names_the_synthetic_build():
    data = helpers.build_engine_sid([song([(0x81, 0x01)])], {1: PLAIN})
    player = SoundMonitorPlayer(data)
    found = playspec.binding(player.view(), player.entry)
    assert found["base"] == 0x1000
    assert found["ptrs"] == 0xA5 and found["hdr_fixed"] == 0
    assert found["rec"] == 0x4000 and found["misc"] == found["rec"] + 0x1F3
    assert found["cia"] is False and found["swap"] is False


def test_the_spec_elaborates_against_the_binding():
    data = helpers.build_engine_sid([song([(0x81, 0x01)])], {1: PLAIN})
    program = SoundMonitorPlayer(data).program()
    assert program.cells


def test_a_non_engine_image_has_no_modelled_entry():
    with pytest.raises(SidParseError, match="no modelled entry"):
        SoundMonitorPlayer(helpers.build_playable_sid())


def test_streams_the_spec_opens_parse_under_the_song_grammar():
    data = helpers.build_engine_sid([song([(0x81, 0x01)] * 2)], {1: PLAIN})
    player = SoundMonitorPlayer(data, validate=True)
    player.render_grid(whole_song(FIRST_ROW + 4 * STEP))
    assert player.validator.results
    assert not player.validator.failures()


def memory_equals_the_6502(data, frames):
    """After every call the 64 KiB outside the stack page and the SID window equal
    the tune's own play routine's."""
    image = SidImage.from_bytes(data)
    header = image.header
    args = (image.image, image.load, header.init_address, header.play_address)
    spec, cpu = SoundMonitorPlayer(data), EmuPlayer(*args)
    for frame in range(frames):
        assert spec.play_frame() == cpu.play_frame(), frame
        mine, real = spec.memory, bytes(cpu._mem)  # pylint: disable=protected-access
        for lo, hi in RAM:
            assert mine[lo:hi] == real[lo:hi], (frame, hex(lo))


@pytest.mark.parametrize("name", ["Only_3", "Fi", "Into_Orbit"])
def test_spec_memory_equals_the_6502(tune, name):
    paths = {
        "Only_3": "MUSICIANS/J/JAP/Only_3.sid",
        "Fi": "MUSICIANS/V/Vulgarik/Fi.sid",
        "Into_Orbit": "MUSICIANS/D/Danko_Tomas/Into_Orbit.sid",
    }
    memory_equals_the_6502(tune(paths[name]).read_bytes(), whole_song(120))


def test_equal_songs_have_equal_canonical_text_wherever_they_are_built():
    fmt = soundmonitor_format()
    first, _ = helpers.build_sid(base=0x1000)
    again, _ = helpers.build_sid(base=0x2000)
    changed, _ = helpers.build_sid([helpers.make_section(tempo=3)])
    text = fmt.canonical_text(fmt.parser.parse(first))
    assert text == fmt.canonical_text(fmt.parser.parse(again))
    assert text != fmt.canonical_text(fmt.parser.parse(changed))


def test_a_patched_volume_routine_runs_its_own_hook():
    data = helpers.build_engine_sid(
        [song([(0x81, 0x01)], base_vol=0x0A)], {1: PLAIN}, patched=True
    )
    player = SoundMonitorPlayer(data)
    found = playspec.binding(player.view(), player.entry)
    assert found["vol_imm"] and found["vol_hook"] and not found["ramp_store"]
    grid = player.render_grid(whole_song(3))
    mirror = helpers.ENGINE_BASE + 0x2300 + 0x120
    assert {row[VOLUME] & 0x0F for row in grid} == {0x09}
    assert player.memory[mirror] == grid[-1][VOLUME]


def test_a_patched_stop_leaves_volume_zero():
    data = helpers.build_engine_sid([song([(0x81, 0x01)])], {1: PLAIN}, patched=True)
    player = SoundMonitorPlayer(data)
    flag = playspec.binding(player.view(), player.entry)["init_flag"]
    player._mem[flag] = 2  # pylint: disable=protected-access
    grid = player.render_grid(whole_song(FIRST_ROW + 6 * STEP))
    assert grid[-1][VOLUME] == 0


@pytest.mark.parametrize(
    "path",
    [
        "DEMOS/M-R/Rape_1_end.sid",
        "DEMOS/UNKNOWN/Lavender.sid",
        "DEMOS/A-F/Future_Spy.sid",
    ],
)
def test_the_spec_plays_a_tune_with_its_own_interrupts_as_its_6502_does(tune, path):
    """Play address 0: each handler's call runs as the spec; the second and third are
    rips that patched the volume routine and the glide, so they run their own hook."""
    data = tune(path).read_bytes()
    own = InterruptPlayer(SidImage.from_bytes(data), 0)
    assert SoundMonitorPlayer(data).render_grid(whole_song(150)) == own.render_grid(
        whole_song(150)
    )


def test_unnative_states_the_patch_hook_as_its_stores(tune):
    """The rip's volume hook (``STA $D418`` and a mirror at ``$9F62``) as spec
    statements, and the spec without the hook plays the same."""
    data = tune("DEMOS/UNKNOWN/Lavender.sid").read_bytes()
    player = SoundMonitorPlayer(data)
    player.play_frame()
    found = playspec.binding(player.memory, player.entry)
    new = unnative(playspec.HOOK_SPEC, found, player.memory)
    assert "native " not in new.source
    assert "mem[0xd418] :=" in new.source and "mem[0x9f62] :=" in new.source

    def make(spec):
        return SoundMonitorPlayer(
            data, bind=lambda view, entry: (spec, playspec.binding(view, entry))
        )

    equivalent(make, playspec.HOOK_SPEC, new, 60)


def test_bind_picks_the_hook_variant_only_for_a_hooked_volume_routine():
    for patched, spec in ((True, playspec.HOOK_SPEC), (False, playspec.SPEC)):
        data = helpers.build_engine_sid(
            [song([(0x81, 0x01)])], {1: PLAIN}, patched=patched
        )
        player = SoundMonitorPlayer(data)
        chosen, found = playspec.bind(player.view(), player.entry)
        assert chosen is spec
        assert ("vol_hook" in found) is patched


def test_a_build_missing_an_anchor_is_refused_by_name():
    data = helpers.build_engine_sid([song([(0x81, 0x01)])], {1: PLAIN})
    player = SoundMonitorPlayer(data)
    view = bytearray(player.view())
    stop = helpers.ENGINE_BASE + 0x2300 + 0xD0
    view[stop : stop + 8] = bytes(8)
    with pytest.raises(SidParseError, match="no stop volume"):
        playspec.binding(bytes(view), player.entry)


def test_a_missing_entry_is_refused():
    data = helpers.build_engine_sid([song([(0x81, 0x01)])], {1: PLAIN})
    player = SoundMonitorPlayer(data)
    with pytest.raises(SidParseError, match="no Soundmonitor play entry"):
        playspec.binding(player.view(), player.entry + 1)


def test_the_snapshot_probe_is_the_streams_the_player_reads():
    fmt = soundmonitor_format()
    data, _ = helpers.build_sid()
    tune = fmt.parser.parse(data)
    probe = fmt.snapshot_probes()["streams"](tune)
    section = tune.song.sections[0]
    assert probe.startswith(section.cia_latch.to_bytes(2, "little"))
    assert len(probe) > section.pattern_length * 2 * 3
    assert "canonical" in fmt.snapshot_probes()


def test_format_exports_the_spec():
    data = helpers.build_engine_sid([song([(0x85, 0x01)])], {1: PLAIN})
    fmt = soundmonitor_format()
    tune = fmt.parser.parse(data)
    bound = fmt.spec(tune)
    assert bound.spec is playspec.SPEC and bound.bind is playspec.bind
    frames = whole_song(FIRST_ROW + 40)
    assert fmt.spec_player(tune).render_grid(frames) == (
        SoundMonitorPlayer(data).render_grid(frames)
    )
