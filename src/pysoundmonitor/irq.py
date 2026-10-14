"""Play a tune whose init installs its own play interrupt (header play address 0).

:class:`InterruptHost` runs init as the main program and raises the source
:func:`pysidtracker.playroutine_cadence` found playing the tune from the registers
the tune programs, taking it through the vector the CPU port banks in.
"""

from __future__ import annotations

from pysidtracker import Cadence, TriggerSource
from pysidtracker import registers as reg
from pysidtracker.emu import wire_mpu

_I_FLAG = 0x04
_KERNAL_ROM, _KERNAL_ACK, _KERNAL_NMI = 0xE000, 0xEA7E, 0xFE47
_RETURN = 0x0002
_NEVER = float("inf")
_RMW = frozenset(
    (0x0E, 0x1E, 0x2E, 0x3E, 0x4E, 0x5E, 0x6E, 0x7E, 0xCE, 0xDE, 0xEE, 0xFE)
)
_VIDEO = (TriggerSource.PAL_VIDEO, TriggerSource.NTSC_VIDEO)
_STATUS = (reg.VIC_IRQ_STATUS, reg.CIA1_ICR, reg.CIA2_ICR)


class InterruptHost:
    """A tune's init as the main program, preempted by its play interrupt.

    The machine starts as the KERNAL leaves it: CPU port ``$37``, CIA #1 Timer A
    running at the KERNAL latch with its IRQ enabled, CIA #2 and the raster IRQ
    off. The source is the VIC raster compare or Timer A of CIA #1 or CIA #2.
    """

    def __init__(
        self,
        mem: bytearray,
        init: int,
        subtune: int,
        cadence: Cadence,
        *,
        sid_model: str = "6581",
        max_cycles: int = 8_000_000,
    ):
        ntsc = cadence.clock_hz == reg.NTSC_CLOCK_HZ
        self.ram = mem
        self.mpu, self.mem = wire_mpu(mem, sid_model=sid_model, ntsc=ntsc)
        self.period = cadence.cycles_per_call
        self.max_cycles = max_cycles
        self.line = reg.NTSC_CYCLES_PER_LINE if ntsc else reg.PAL_CYCLES_PER_LINE
        self.screen = reg.NTSC_CYCLES_PER_FRAME if ntsc else reg.PAL_CYCLES_PER_FRAME
        self.vic = cadence.source in _VIDEO
        self.nmi = cadence.source is TriggerSource.NMI_TIMER
        self.status = _STATUS[0 if self.vic else 1 + self.nmi]
        self.flag = self.edge = self.compare = self.owed = 0
        self.enable = int(not self.vic and not self.nmi)
        kernal = reg.KERNAL_CIA1_LATCH_NTSC if ntsc else reg.KERNAL_CIA1_LATCH_PAL
        self.latch = 0xFFFF if self.nmi else kernal
        self.running, self.oneshot = not self.nmi, False
        self.frames = []
        mem[0:2] = b"\x2f\x37"
        mem[0x02A6] = 0 if ntsc else 1
        self._wire()
        mpu = self.mpu
        self.main_sp = mpu.sp
        for value in (_RETURN - 1 >> 8, _RETURN - 1 & 0xFF):
            mem[0x0100 + mpu.sp] = value
            mpu.sp = mpu.sp - 1 & 0xFF
        mpu.a, mpu.pc = subtune & 0xFF, init
        mpu.p &= ~_I_FLAG
        self.due = self._next(0)
        self._run(max_cycles)

    def _wire(self) -> None:
        self.mem.subscribe_to_read(list(_STATUS), self._read_status)
        if self.vic:
            self.mem.subscribe_to_write([reg.VIC_IRQ_STATUS], self._ack_vic)
            self.mem.subscribe_to_write(
                [reg.VIC_IRQ_ENABLE, reg.VIC_CONTROL_1, reg.VIC_RASTER], self._vic
            )
        else:
            base = self.status & 0xFFF0
            self.mem.subscribe_to_write([base + o for o in (4, 5, 0xD, 0xE)], self._cia)

    def _io(self) -> bool:
        """The CPU port maps I/O at ``$D000``."""
        return bool(self.ram[1] & 3 and self.ram[1] & 4)

    def _now(self) -> int:
        """The cycle of the executing instruction's data access (its last)."""
        mpu = self.mpu
        return mpu.processorCycles + mpu.cycletime[self.ram[mpu.pc]] - 1

    def _next(self, now: int) -> float:
        """The source's first raise after ``now``."""
        if self.vic:
            base = self.compare * self.line
            if base >= self.screen:
                return _NEVER
            return base + ((now - base) // self.screen + 1) * self.screen
        return now + self.latch + 1 if self.running else _NEVER

    def _read_status(self, addr: int):
        if not self._io():
            return None
        if addr != self.status:
            return 0
        flag = self.flag
        if not self.vic:
            self.flag = 0
        unused = 0x70 if self.vic else 0
        return unused | flag | (0x80 if flag & self.enable else 0)

    def _ack_vic(self, _addr: int, value: int) -> None:
        if self._io():
            self.flag &= ~(self.flag if self.ram[self.mpu.pc] in _RMW else value)

    def _vic(self, addr: int, value: int) -> None:
        if not self._io():
            return
        if addr == reg.VIC_IRQ_ENABLE:
            self.enable = value & 1
            return
        if addr == reg.VIC_RASTER:
            self.compare = self.compare & 0x100 | value
        else:
            self.compare = self.compare & 0xFF | (value & 0x80) << 1
        self.due = self._next(self._now())

    def _cia(self, addr: int, value: int) -> None:
        if not self._io():
            return
        offset = addr & 0xF
        if offset == 4:
            self.latch = self.latch & 0xFF00 | value
        elif offset == 5:
            self.latch = self.latch & 0xFF | value << 8
        elif offset == 0xD:
            bit = value & 1
            self.enable = self.enable | bit if value & 0x80 else self.enable & ~bit
        else:
            started = value & 1 and not self.running
            self.running, self.oneshot = bool(value & 1), bool(value & 8)
            if started or value & 0x10 or not self.running:
                self.due = self._next(self._now())

    def _raise(self) -> None:
        """The source raises at :attr:`due`; schedule its next raise."""
        if self.nmi and self.enable and not self.flag:
            self.edge = 1
        self.flag = 1
        self.running = self.vic or self.running and not self.oneshot
        self.due = self._next(self.due)

    def _enter(self) -> None:
        """Take the interrupt through the vector the CPU port banks in."""
        mpu, ram = self.mpu, self.ram
        regs = (mpu.a, mpu.x, mpu.y)
        self.edge = 0
        mpu.nmi()
        if ram[1] & 2:
            if not self.nmi:
                for value in regs:
                    ram[0x0100 + mpu.sp] = value
                    mpu.sp = mpu.sp - 1 & 0xFF
            vector = reg.RAM_NMI_VECTOR_LO if self.nmi else reg.RAM_IRQ_VECTOR_LO
        else:
            vector = reg.CPU_NMI_VECTOR if self.nmi else reg.CPU_IRQ_VECTOR
        mpu.pc = ram[vector] | ram[vector + 1] << 8
        self.frames.append(mpu.sp)

    def _pull(self) -> int:
        mpu = self.mpu
        mpu.sp = mpu.sp + 1 & 0xFF
        return self.ram[0x0100 + mpu.sp]

    def _kernal_exit(self) -> None:
        """Leave a handler through the KERNAL ROM as sidplayfp's KERNAL does.

        Up to ``$EA7E`` it acknowledges CIA #1 and pulls the registers the IRQ
        entry saved, ``$FE47`` only returns, anywhere else pulls and returns.
        """
        mpu = self.mpu
        if mpu.pc <= _KERNAL_ACK and self.status == reg.CIA1_ICR and self._io():
            self.flag = 0
        if mpu.pc != _KERNAL_NMI:
            mpu.y, mpu.x, mpu.a = self._pull(), self._pull(), self._pull()
        mpu.p = self._pull()
        mpu.pc = self._pull() | self._pull() << 8

    def _run(self, budget: int) -> None:
        """Run until the source's next raise is due, or ``budget`` cycles pass."""
        mpu, ram, frames = self.mpu, self.ram, self.frames
        limit = mpu.processorCycles + budget
        while mpu.processorCycles < min(self.due, limit):
            if self.edge or (
                not self.nmi and self.flag & self.enable and not mpu.p & _I_FLAG
            ):
                self._enter()
            elif frames and ram[1] & 2 and mpu.pc >= _KERNAL_ROM:
                self._kernal_exit()
            elif not frames and mpu.pc == _RETURN and mpu.sp == self.main_sp:
                mpu.processorCycles = min(self.due, limit)
            else:
                mpu.step()
            while frames and mpu.sp > frames[-1]:
                frames.pop()

    def frame(self) -> None:
        """Advance one play period: a raise, or a repeat when none was due."""
        if self.owed:
            self.owed -= 1
            return
        start = self.mpu.processorCycles
        if start >= self.due:
            self._raise()
        self._run(self.max_cycles)
        elapsed = self.mpu.processorCycles - start
        self.owed = max(1, (elapsed + self.period // 2) // self.period) - 1
