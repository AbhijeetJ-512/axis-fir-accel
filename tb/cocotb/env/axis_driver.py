"""AXI4-Stream source, sink and protocol monitor for cocotb.

The source and sink are independent background coroutines with independently
randomisable stall probabilities. That independence is the point: a stream
interface that only works when the producer and consumer stall together is
broken, and a testbench that couples them will never notice.

All observation happens on the FALLING edge, for the reason recorded in
engineering log D-017: sampling in the same timestep as the clock edge races
the drivers, which update their signals in that same timestep.
"""

import random
from collections import deque

from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge


def _to_int(sig):
    try:
        return int(sig.value)
    except ValueError:
        return -1


class AxiStreamSource:
    """Drives an AXI4-Stream slave.

    Honours the rule that TVALID, once asserted, is held until TREADY is seen.
    The stall die is rolled only between beats, never while a beat is pending --
    withdrawing TVALID is a protocol violation, and a source that did it would
    make the DUT's monitor findings meaningless.
    """

    def __init__(self, dut, clk, prefix="s_axis_", rng=None):
        self.dut = dut
        self.clk = clk
        self.p = prefix
        self.rng = rng or random.Random(0)
        self.queue = deque()
        self.sent = []            # (data, last) actually accepted, in order
        self.stall_prob = 0.0
        self._stop = False

    def _s(self, name):
        return getattr(self.dut, self.p + name)

    def send(self, data, last=0):
        self.queue.append((data, last))

    def send_frame(self, values):
        """Queue a frame; TLAST is set on the final beat."""
        for i, v in enumerate(values):
            self.send(v, 1 if i == len(values) - 1 else 0)

    def idle(self):
        return not self.queue

    def stop(self):
        self._stop = True

    async def reset(self):
        self._s("tdata").value = 0
        self._s("tlast").value = 0
        self._s("tvalid").value = 0

    async def run(self):
        await self.reset()
        while not self._stop:
            # Between beats: nothing queued, or a deliberate gap.
            while not self.queue or self.rng.random() < self.stall_prob:
                self._s("tvalid").value = 0
                await RisingEdge(self.clk)
                if self._stop:
                    return

            data, last = self.queue.popleft()
            self._s("tdata").value = data
            self._s("tlast").value = last
            self._s("tvalid").value = 1

            # Hold the beat until it is accepted. TVALID must not be withdrawn.
            while True:
                await ReadOnly()
                if _to_int(self._s("tready")) == 1:
                    break
                await RisingEdge(self.clk)

            self.sent.append((data, last))
            await RisingEdge(self.clk)


class AxiStreamSink:
    """Consumes an AXI4-Stream master, with randomisable TREADY.

    TREADY may be withdrawn freely by the protocol, so unlike the source this
    coroutine rolls its die every cycle.
    """

    def __init__(self, dut, clk, prefix="m_axis_", rng=None):
        self.dut = dut
        self.clk = clk
        self.p = prefix
        self.rng = rng or random.Random(1)
        self.received = []        # (data, last) in order
        self.stall_prob = 0.0
        self.pause = False        # hard hold-off, independent of stall_prob
        self._stop = False

    def _s(self, name):
        return getattr(self.dut, self.p + name)

    def stop(self):
        self._stop = True

    async def run(self):
        self._s("tready").value = 0
        while not self._stop:
            if self.pause or self.rng.random() < self.stall_prob:
                self._s("tready").value = 0
                await RisingEdge(self.clk)
                continue

            self._s("tready").value = 1
            await ReadOnly()
            if _to_int(self._s("tvalid")) == 1:
                self.received.append((_to_int(self._s("tdata")),
                                      _to_int(self._s("tlast"))))
            await RisingEdge(self.clk)

        self._s("tready").value = 0


class AxiStreamProtocolMonitor:
    """Passive checker for the AXI4-Stream rules the IP must obey.

    Every cycle, on the interface named by `prefix`:
      - TVALID is never withdrawn without a TREADY handshake
      - TDATA and TLAST are stable while TVALID is high and TREADY is low

    Violations accumulate rather than raising, so one test run reports every
    problem instead of only the first.
    """

    def __init__(self, dut, clk, prefix, name=None):
        self.dut = dut
        self.clk = clk
        self.p = prefix
        self.name = name or prefix.rstrip("_")
        self.errors = []
        self.beats = 0
        self._stop = False

    def _s(self, sig):
        return getattr(self.dut, self.p + sig)

    def stop(self):
        self._stop = True

    async def run(self):
        pv = pr = 0
        pd = pl = None
        cycle = 0
        while not self._stop:
            await FallingEdge(self.clk)
            await ReadOnly()
            cycle += 1

            v = _to_int(self._s("tvalid"))
            r = _to_int(self._s("tready"))
            d = _to_int(self._s("tdata"))
            ln = _to_int(self._s("tlast"))

            if v == 1 and r == 1:
                self.beats += 1

            if pv == 1 and v == 0 and pr != 1:
                self.errors.append(
                    f"{self.name}: TVALID withdrawn without TREADY (cycle ~{cycle})")

            if pv == 1 and v == 1 and pr == 0:
                if pd != d:
                    self.errors.append(
                        f"{self.name}: TDATA changed while stalled "
                        f"(0x{pd:X}->0x{d:X}, cycle ~{cycle})")
                if pl != ln:
                    self.errors.append(
                        f"{self.name}: TLAST changed while stalled "
                        f"({pl}->{ln}, cycle ~{cycle})")

            pv, pr, pd, pl = v, r, d, ln


class CreditInvariantChecker:
    """Checks the one safety property the whole architecture rests on.

    A write into the output FIFO must never occur while that FIFO is full. If
    the credit accounting is correct this can never happen; if it is wrong, this
    fires on the cycle it first goes wrong rather than surfacing later as
    corrupted data.

    Also records the peak output-FIFO occupancy, which is the empirical evidence
    that `in_flight + occupancy <= MAX_CREDITS` actually holds.
    """

    def __init__(self, dut, clk, depth):
        self.dut = dut
        self.clk = clk
        self.depth = depth
        self.errors = []
        self.peak_out = 0
        self.peak_in = 0
        self._stop = False

    def stop(self):
        self._stop = True

    async def run(self):
        cycle = 0
        while not self._stop:
            await FallingEdge(self.clk)
            await ReadOnly()
            cycle += 1

            push = _to_int(self.dut.dbg_out_push)
            full = _to_int(self.dut.dbg_out_full)
            occ = _to_int(self.dut.dbg_out_count)
            inocc = _to_int(self.dut.dbg_in_count)

            if push == 1 and full == 1:
                self.errors.append(
                    f"output FIFO written while FULL at cycle ~{cycle} "
                    f"-- credit invariant violated")

            if occ > self.peak_out:
                self.peak_out = occ
            if inocc > self.peak_in:
                self.peak_in = inocc

            if occ > self.depth:
                self.errors.append(
                    f"output FIFO occupancy {occ} exceeds depth {self.depth} "
                    f"at cycle ~{cycle}")
