"""AXI4-Lite master driver and protocol monitor for cocotb.

Design notes
------------
The five channels are driven as independent coroutines so that AW and W can be
issued in either order, or simultaneously, with independent delays. That is the
whole point of testing an AXI4-Lite slave: a slave that only works when AW
precedes W is broken, and a driver that only ever issues AW first will never
find out.

Handshake pattern used throughout::

    sig.value = 1
    while True:
        await ReadOnly()            # settle; sample the partner's ready/valid
        if partner is asserted: break
        await RisingEdge(clk)
    await RisingEdge(clk)           # the edge on which the transfer occurs
    sig.value = 0

Sampling in the ReadOnly region is what makes this correct: it reads the value
the DUT presents *before* the clock edge, which is what the edge actually
transfers.
"""

import random

from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge


def _to_int(sig):
    """Read a handle as int, treating any X/Z as 'not asserted'."""
    try:
        return int(sig.value)
    except ValueError:
        return -1


class AxiLiteMaster:
    """Drives an AXI4-Lite slave. One outstanding read and write at a time."""

    def __init__(self, dut, clk, prefix="s_axil_"):
        self.dut = dut
        self.clk = clk
        self.p = prefix
        self.log = dut._log

    def _s(self, name):
        return getattr(self.dut, self.p + name)

    async def reset(self):
        """Park every master-driven signal in its inactive state."""
        self._s("awaddr").value = 0
        self._s("awprot").value = 0
        self._s("awvalid").value = 0
        self._s("wdata").value = 0
        self._s("wstrb").value = 0
        self._s("wvalid").value = 0
        self._s("bready").value = 0
        self._s("araddr").value = 0
        self._s("arprot").value = 0
        self._s("arvalid").value = 0
        self._s("rready").value = 0

    # -- low-level channel helpers -------------------------------------------

    async def _drive_valid(self, valid_sig, ready_sig, setup, delay):
        for _ in range(delay):
            await RisingEdge(self.clk)
        setup()
        valid_sig.value = 1
        while True:
            await ReadOnly()
            if _to_int(ready_sig) == 1:
                break
            await RisingEdge(self.clk)
        await RisingEdge(self.clk)
        valid_sig.value = 0

    async def _accept_valid(self, valid_sig, ready_sig, capture, delay):
        for _ in range(delay):
            await RisingEdge(self.clk)
        ready_sig.value = 1
        while True:
            await ReadOnly()
            if _to_int(valid_sig) == 1:
                break
            await RisingEdge(self.clk)
        result = capture()
        await RisingEdge(self.clk)
        ready_sig.value = 0
        return result

    # -- transactions ---------------------------------------------------------

    async def write(self, addr, data, strb=0xF,
                    aw_delay=0, w_delay=0, b_delay=0):
        """Issue a write. Returns BRESP.

        `aw_delay` and `w_delay` are independent, so passing e.g. aw_delay=0,
        w_delay=3 exercises AW-before-W and the reverse with the values swapped.
        """
        import cocotb

        def setup_aw():
            self._s("awaddr").value = addr
            self._s("awprot").value = 0

        def setup_w():
            self._s("wdata").value = data
            self._s("wstrb").value = strb

        def capture_b():
            return _to_int(self._s("bresp"))

        aw = cocotb.start_soon(
            self._drive_valid(self._s("awvalid"), self._s("awready"), setup_aw, aw_delay))
        w = cocotb.start_soon(
            self._drive_valid(self._s("wvalid"), self._s("wready"), setup_w, w_delay))
        b = cocotb.start_soon(
            self._accept_valid(self._s("bvalid"), self._s("bready"), capture_b, b_delay))

        await aw
        await w
        resp = await b
        return resp

    async def read(self, addr, ar_delay=0, r_delay=0):
        """Issue a read. Returns (RDATA, RRESP)."""
        import cocotb

        def setup_ar():
            self._s("araddr").value = addr
            self._s("arprot").value = 0

        def capture_r():
            return (_to_int(self._s("rdata")), _to_int(self._s("rresp")))

        ar = cocotb.start_soon(
            self._drive_valid(self._s("arvalid"), self._s("arready"), setup_ar, ar_delay))
        r = cocotb.start_soon(
            self._accept_valid(self._s("rvalid"), self._s("rready"), capture_r, r_delay))

        await ar
        data, resp = await r
        return data, resp

    # -- convenience ----------------------------------------------------------

    async def write_rand(self, addr, data, strb=0xF, rng=None):
        """Write with randomised inter-channel delays."""
        rng = rng or random
        return await self.write(addr, data, strb,
                                aw_delay=rng.randint(0, 3),
                                w_delay=rng.randint(0, 3),
                                b_delay=rng.randint(0, 3))

    async def read_rand(self, addr, rng=None):
        rng = rng or random
        return await self.read(addr,
                               ar_delay=rng.randint(0, 3),
                               r_delay=rng.randint(0, 3))


class AxiLiteProtocolMonitor:
    """Passive checker for the AXI4-Lite rules this slave must obey.

    Checks, every cycle:
      - BVALID/RVALID, once asserted, stay asserted until the matching READY
        (no valid withdrawal).
      - BRESP/RDATA/RRESP stay stable while VALID is high and READY is low.
      - No response is emitted without a preceding accepted request.

    Violations are recorded rather than raised, so a test sees the whole picture
    at the end instead of dying on the first symptom.

    Sampling region
    ---------------
    The monitor samples on the FALLING edge, not the rising edge.

    Sampling at `RisingEdge` + `ReadOnly` puts the monitor in the same timestep
    as the clock edge, racing the drivers -- which update their signals in that
    same timestep in response to the very same edge. Depending on coroutine
    scheduling order the monitor then sees either the pre-edge or the post-edge
    value, and a handshake can be counted twice or missed entirely.

    Every driver in this environment acts only on rising edges, so mid-cycle is
    quiescent: the values present at the falling edge are exactly the values the
    next rising edge will transfer. That makes the observation deterministic.
    This does assume a roughly 50% duty cycle, which the cocotb Clock provides.
    """

    def __init__(self, dut, clk, prefix="s_axil_"):
        self.dut = dut
        self.clk = clk
        self.p = prefix
        self.errors = []
        self.writes_accepted = 0
        self.responses_seen = 0
        self.reads_accepted = 0
        self.read_data_seen = 0
        self._stop = False

    def _s(self, name):
        return getattr(self.dut, self.p + name)

    def stop(self):
        self._stop = True

    async def run(self):
        prev_bvalid = 0
        prev_bresp = None
        prev_rvalid = 0
        prev_rdata = None
        prev_rresp = None

        while not self._stop:
            await FallingEdge(self.clk)
            await ReadOnly()

            bvalid = _to_int(self._s("bvalid"))
            bready = _to_int(self._s("bready"))
            bresp = _to_int(self._s("bresp"))
            rvalid = _to_int(self._s("rvalid"))
            rready = _to_int(self._s("rready"))
            rdata = _to_int(self._s("rdata"))
            rresp = _to_int(self._s("rresp"))

            awh = _to_int(self._s("awvalid")) == 1 and _to_int(self._s("awready")) == 1
            arh = _to_int(self._s("arvalid")) == 1 and _to_int(self._s("arready")) == 1
            if awh:
                self.writes_accepted += 1
            if arh:
                self.reads_accepted += 1
            if bvalid == 1 and bready == 1:
                self.responses_seen += 1
            if rvalid == 1 and rready == 1:
                self.read_data_seen += 1

            # VALID must not be withdrawn without a handshake.
            if prev_bvalid == 1 and bvalid == 0 and prev_bready != 1:
                self.errors.append("BVALID deasserted without BREADY handshake")
            if prev_rvalid == 1 and rvalid == 0 and prev_rready != 1:
                self.errors.append("RVALID deasserted without RREADY handshake")

            # Payload must be stable while stalled.
            if prev_bvalid == 1 and bvalid == 1 and prev_bready == 0:
                if prev_bresp != bresp:
                    self.errors.append(f"BRESP changed while stalled: {prev_bresp}->{bresp}")
            if prev_rvalid == 1 and rvalid == 1 and prev_rready == 0:
                if prev_rdata != rdata:
                    self.errors.append(f"RDATA changed while stalled: {prev_rdata}->{rdata}")
                if prev_rresp != rresp:
                    self.errors.append(f"RRESP changed while stalled: {prev_rresp}->{rresp}")

            prev_bvalid, prev_bready, prev_bresp = bvalid, bready, bresp
            prev_rvalid, prev_rready = rvalid, rready
            prev_rdata, prev_rresp = rdata, rresp
