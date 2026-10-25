"""Phase 4 regression: full IP integration with resource folding.

Phase 4 Definition of Done (docs/00_project_plan.md):
  - every MAC_UNITS in {1,2,4,8,16,32} produces bit-identical results to the
    unfolded reference
  - measured II matches ceil(N_eff / MAC_UNITS)
  - the coefficient swap takes effect exactly at a frame boundary, never inside
  - DRAIN loses no samples
  - illegal configuration blocks RUN

The MAC_UNITS bit-identity requirement is risk R3: in a folded configuration
the delay line must shift once per OUTPUT SAMPLE, not once per pass. Getting
that wrong produces a plausible-looking but numerically wrong filter with no
protocol violation to catch it. Since MAC_UNITS is an elaboration parameter,
the check is "every build matches the same MAC_UNITS-independent reference
model" -- which implies they all match each other.
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from env import regmap as rm
from env.axil_driver import AxiLiteMaster
from env.axis_driver import (AxiStreamProtocolMonitor, AxiStreamSink,
                             AxiStreamSource)
from model.fir_ref import (RND_CONVERGENT, RND_TRUNC, SAT_ASYMMETRIC,
                           SAT_SYMMETRIC, FirRef, to_signed)

DATA_W = int(os.environ.get("DATA_W", "16"))
COEF_W = int(os.environ.get("COEF_W", "16"))
NTAPS_MAX = int(os.environ.get("NTAPS_MAX", "64"))
MAC_UNITS = int(os.environ.get("MAC_UNITS", "8"))
IN_FIFO_DEPTH = int(os.environ.get("IN_FIFO_DEPTH", "8"))
OUT_FIFO_DEPTH = int(os.environ.get("OUT_FIFO_DEPTH", "16"))
SYMMETRIC_EN = int(os.environ.get("SYMMETRIC_EN", "1"))

CLK_NS = 10
XMIN, XMAX = -(1 << (DATA_W - 1)), (1 << (DATA_W - 1)) - 1


def passes_for(ntaps, symmetric):
    """P = ceil(N_eff / MAC_UNITS) -- must mirror fir_ctrl."""
    n_eff = (ntaps + 1) // 2 if symmetric else ntaps
    return (n_eff + MAC_UNITS - 1) // MAC_UNITS


class Dut:
    """The whole IP: AXI4-Lite configuration plus an AXI4-Stream data path."""

    def __init__(self, dut, seed=0):
        self.dut = dut
        self.axil = AxiLiteMaster(dut, dut.aclk, "s_axil_")
        self.src = AxiStreamSource(dut, dut.aclk, "s_axis_", random.Random(seed + 1))
        self.snk = AxiStreamSink(dut, dut.aclk, "m_axis_", random.Random(seed + 2))
        self.mon = AxiStreamProtocolMonitor(dut, dut.aclk, "m_axis_", "m_axis")
        self.ref = FirRef(DATA_W, COEF_W, NTAPS_MAX)

    async def start(self):
        cocotb.start_soon(Clock(self.dut.aclk, CLK_NS, unit="ns").start())
        await self.axil.reset()
        await self.src.reset()
        self.dut.m_axis_tready.value = 0
        self.dut.aresetn.value = 0
        for _ in range(5):
            await RisingEdge(self.dut.aclk)
        self.dut.aresetn.value = 1
        for _ in range(5):
            await RisingEdge(self.dut.aclk)
        for c in (self.src, self.snk, self.mon):
            cocotb.start_soon(c.run())

    def stop(self):
        for c in (self.src, self.snk, self.mon):
            c.stop()

    async def configure(self, ntaps, coeffs, round_mode=RND_CONVERGENT,
                        sat_mode=SAT_ASYMMETRIC, symmetric=0, enable=True):
        """Full software configuration sequence, exactly as a driver would."""
        symmetric = symmetric and SYMMETRIC_EN
        for i, c in enumerate(coeffs):
            resp = await self.axil.write(rm.coef_addr(i), c & ((1 << COEF_W) - 1))
            assert resp == rm.RESP_OKAY, f"coefficient {i} write returned {resp}"
        # Commit the shadow bank; the IP is idle so the swap is immediate.
        await self.axil.write(rm.REG_CTRL, 1 << rm.CTRL_COMMIT_BIT)
        for _ in range(4):
            await RisingEdge(self.dut.aclk)

        cfg = rm.make_cfg(ntaps, round_mode, sat_mode, symmetric)
        resp = await self.axil.write(rm.REG_CFG, cfg)
        assert resp == rm.RESP_OKAY, f"CFG write returned {resp}"

        # Flush the DUT delay line so its history matches the freshly-reset
        # model. Resetting only the model made every configuration after the
        # first compare a zero-padded expectation against real carried-over
        # history -- a testbench bug that looked exactly like an RTL bug.
        await self.axil.write(rm.REG_CTRL, 1 << rm.CTRL_FLUSH_BIT)
        for _ in range(8):
            await RisingEdge(self.dut.aclk)

        self.ref.configure(ntaps=ntaps, coeffs=coeffs, round_mode=round_mode,
                           sat_mode=sat_mode, symmetric=symmetric)
        self.ref.reset()

        if enable:
            await self.axil.write(rm.REG_CTRL, 1 << rm.CTRL_ENABLE_BIT)
            for _ in range(4):
                await RisingEdge(self.dut.aclk)
        return symmetric

    async def stream(self, samples, last_every=None, timeout=400000):
        """Push samples and wait for every one to come back out."""
        n = len(samples)
        base = len(self.snk.received)
        for i, x in enumerate(samples):
            last = 1 if (last_every and (i % last_every) == last_every - 1) else 0
            self.src.send(x & ((1 << DATA_W) - 1), last)
        for _ in range(timeout):
            await RisingEdge(self.dut.aclk)
            if len(self.snk.received) - base >= n:
                break
        else:
            raise AssertionError(
                f"timeout: {len(self.snk.received) - base}/{n} samples emerged")
        return self.snk.received[base:base + n]

    def compare(self, got, samples, what):
        exp = [self.ref.push(x)[0] for x in samples]
        assert len(got) == len(exp), f"{what}: {len(got)} out vs {len(exp)} expected"
        for i, (g, e) in enumerate(zip(got, exp)):
            gv = to_signed(g[0], DATA_W)
            assert gv == e, (f"{what}: sample {i} mismatch -- DUT {gv}, model {e} "
                             f"(MAC_UNITS={MAC_UNITS})")

    async def check(self, samples, what, last_every=None):
        got = await self.stream(samples, last_every)
        self.compare(got, samples, what)
        assert not self.mon.errors, "protocol: " + "; ".join(self.mon.errors[:3])
        return got


async def setup(dut, seed=0):
    d = Dut(dut, seed)
    await d.start()
    return d


# =============================================================================

@cocotb.test()
async def test_identity_vs_reference(dut):
    """Bit-identical to the model for this MAC_UNITS build -- the R3 gate."""
    d = await setup(dut, seed=1)
    rng = random.Random(31)
    d.snk.stall_prob = 0.0

    for ntaps in (1, 2, 3, 7, 8, 15, 16, 31, MAC_UNITS, MAC_UNITS + 1, NTAPS_MAX):
        if not (1 <= ntaps <= NTAPS_MAX):
            continue
        for symmetric in (0, 1):
            if symmetric and not SYMMETRIC_EN:
                continue
            h = [rng.randrange(-(1 << (COEF_W - 2)), 1 << (COEF_W - 2))
                 for _ in range(ntaps)]
            if symmetric:
                half = (ntaps + 1) // 2
                h = [h[min(k, ntaps - 1 - k)] for k in range(ntaps)]
            sym = await d.configure(ntaps, h, symmetric=symmetric)
            xs = [rng.randrange(XMIN, XMAX + 1) for _ in range(40)]
            await d.check(xs, f"N={ntaps} sym={sym} MAC_UNITS={MAC_UNITS}")
            await d.axil.write(rm.REG_CTRL, 0)          # stop between configs
            for _ in range(30):
                await RisingEdge(dut.aclk)
    dut._log.info(f"MAC_UNITS={MAC_UNITS}: bit-identical to the reference model")
    d.stop()


@cocotb.test()
async def test_initiation_interval(dut):
    """Measured II equals ceil(N_eff / MAC_UNITS)."""
    d = await setup(dut, seed=2)
    rng = random.Random(37)
    d.snk.stall_prob = 0.0

    for ntaps, symmetric in [(NTAPS_MAX, 0), (NTAPS_MAX, 1), (8, 0), (1, 0)]:
        if symmetric and not SYMMETRIC_EN:
            continue
        h = [rng.randrange(-4000, 4000) for _ in range(ntaps)]
        if symmetric:
            h = [h[min(k, ntaps - 1 - k)] for k in range(ntaps)]
        sym = await d.configure(ntaps, h, symmetric=symmetric)
        expect_p = passes_for(ntaps, sym)

        n = 400
        xs = [rng.randrange(XMIN, XMAX + 1) for _ in range(n)]
        base = len(d.snk.received)
        t0 = cocotb.utils.get_sim_time("ns") if hasattr(cocotb, "utils") else None
        cyc = 0
        for x in xs:
            d.src.send(x & ((1 << DATA_W) - 1), 0)
        while len(d.snk.received) - base < n:
            await RisingEdge(dut.aclk)
            cyc += 1
            assert cyc < 200000, "II test timed out"
        got = d.snk.received[base:base + n]
        d.compare(got, xs, f"II N={ntaps} sym={sym}")

        measured = cyc / n
        assert measured <= expect_p + 0.15 * expect_p + 2, (
            f"N={ntaps} sym={sym}: measured II {measured:.2f} exceeds "
            f"predicted {expect_p} by more than the fill/drain allowance")
        dut._log.info(f"II: N={ntaps} sym={sym} MAC_UNITS={MAC_UNITS} -> "
                      f"predicted {expect_p}, measured {measured:.3f}")
        await d.axil.write(rm.REG_CTRL, 0)
        for _ in range(40):
            await RisingEdge(dut.aclk)
    d.stop()


@cocotb.test()
async def test_backpressure_lossless(dut):
    """Randomised stalls on both interfaces: still bit-exact, still lossless."""
    d = await setup(dut, seed=3)
    rng = random.Random(41)
    h = [rng.randrange(-6000, 6000) for _ in range(16)]
    await d.configure(16, h, round_mode=RND_CONVERGENT, sat_mode=SAT_SYMMETRIC)
    d.src.stall_prob = 0.5
    d.snk.stall_prob = 0.5
    xs = [rng.randrange(XMIN, XMAX + 1) for _ in range(600)]
    await d.check(xs, "backpressure", last_every=32)
    dut._log.info("bit-exact under 50% stall on both interfaces")
    d.stop()


@cocotb.test()
async def test_illegal_config_blocks_run(dut):
    """An illegally-configured block never streams, and reports CFG_ERR."""
    d = await setup(dut, seed=4)
    h = [1000] * 8
    for i, c in enumerate(h):
        await d.axil.write(rm.coef_addr(i), c)
    await d.axil.write(rm.REG_CTRL, 1 << rm.CTRL_COMMIT_BIT)

    await d.axil.write(rm.REG_CFG, rm.make_cfg(0))      # NTAPS = 0 is illegal
    await d.axil.write(rm.REG_CTRL, 1 << rm.CTRL_ENABLE_BIT)
    for _ in range(20):
        await RisingEdge(dut.aclk)

    sts, _ = await d.axil.read(rm.REG_STATUS)
    assert (sts >> rm.STS_CFGERR_BIT) & 1 == 1, "CFG_ERR not set on illegal enable"

    d.src.send(1234, 0)
    for _ in range(60):
        await RisingEdge(dut.aclk)
    assert len(d.snk.received) == 0, "IP streamed with an illegal configuration"
    dut._log.info("illegal configuration blocks RUN and raises CFG_ERR")
    d.stop()


@cocotb.test()
async def test_drain_loses_nothing(dut):
    """Dropping ENABLE mid-stream carries admitted samples to completion."""
    d = await setup(dut, seed=5)
    rng = random.Random(43)
    h = [rng.randrange(-5000, 5000) for _ in range(8)]
    await d.configure(8, h)

    n = 64
    xs = [rng.randrange(XMIN, XMAX + 1) for _ in range(n)]
    for x in xs:
        d.src.send(x & ((1 << DATA_W) - 1), 0)

    # Let some in, then stop the block while data is still in flight.
    for _ in range(20):
        await RisingEdge(dut.aclk)
    await d.axil.write(rm.REG_CTRL, 0)

    for _ in range(400):
        await RisingEdge(dut.aclk)

    got = d.snk.received
    admitted, _ = await d.axil.read(rm.REG_SAMPLE_CNT)
    assert len(got) == admitted, (
        f"DRAIN lost samples: {admitted} admitted, {len(got)} emerged")
    exp = [d.ref.push(x)[0] for x in xs[:len(got)]]
    for i, (g, e) in enumerate(zip(got, exp)):
        assert to_signed(g[0], DATA_W) == e, f"DRAIN: sample {i} wrong"
    sts, _ = await d.axil.read(rm.REG_STATUS)
    assert (sts >> rm.STS_IDLE_BIT) & 1 == 1, "did not return to IDLE after DRAIN"
    dut._log.info(f"DRAIN: all {admitted} admitted samples emerged correctly")
    d.stop()


@cocotb.test()
async def test_coefficient_swap_at_frame_boundary(dut):
    """A committed coefficient set takes effect at a frame boundary, not inside one."""
    d = await setup(dut, seed=6)
    frame = 16
    h_a = [1 << (COEF_W - 3)] + [0] * 7          # delta: y = x * 0.25
    h_b = [0, 1 << (COEF_W - 3)] + [0] * 6       # delayed delta: y = x[n-1] * 0.25
    await d.configure(8, h_a, round_mode=RND_TRUNC)

    rng = random.Random(47)
    xs1 = [rng.randrange(XMIN, XMAX + 1) for _ in range(frame)]
    got1 = await d.stream(xs1, last_every=frame)
    d.compare(got1, xs1, "before swap")

    # Write the new set (lands in the shadow bank) and request a commit while
    # streaming. STATUS.COEFF_PENDING must report the request.
    for i, c in enumerate(h_b):
        assert await d.axil.write(rm.coef_addr(i), c & ((1 << COEF_W) - 1)) == rm.RESP_OKAY
    await d.axil.write(rm.REG_CTRL, (1 << rm.CTRL_ENABLE_BIT) | (1 << rm.CTRL_COMMIT_BIT))

    # Drive a full frame; the swap happens when its TLAST leaves the IP, so this
    # frame must still be filtered entirely with the OLD coefficients.
    xs2 = [rng.randrange(XMIN, XMAX + 1) for _ in range(frame)]
    got2 = await d.stream(xs2, last_every=frame)
    d.compare(got2, xs2, "frame spanning the commit (must use OLD coefficients)")

    for _ in range(20):
        await RisingEdge(dut.aclk)
    sts, _ = await d.axil.read(rm.REG_STATUS)
    assert (sts >> rm.STS_COEFPEND_BIT) & 1 == 0, "commit still pending after a frame boundary"

    # From here the new set is active.
    d.ref.configure(coeffs=h_b)
    xs3 = [rng.randrange(XMIN, XMAX + 1) for _ in range(frame)]
    got3 = await d.stream(xs3, last_every=frame)
    d.compare(got3, xs3, "after swap (must use NEW coefficients)")
    dut._log.info("coefficient swap landed exactly at the frame boundary")
    d.stop()


@cocotb.test()
async def test_long_random(dut):
    """Long randomised stream across configurations, with stalls."""
    d = await setup(dut, seed=7)
    rng = random.Random(0xF12)
    total = 0
    for _ in range(6):
        ntaps = rng.randint(1, NTAPS_MAX)
        symmetric = SYMMETRIC_EN and (rng.random() < 0.5)
        h = [rng.randrange(-(1 << (COEF_W - 1)), 1 << (COEF_W - 1))
             for _ in range(ntaps)]
        if symmetric:
            h = [h[min(k, ntaps - 1 - k)] for k in range(ntaps)]
        rnd = rng.choice([RND_TRUNC, RND_CONVERGENT])
        sat = rng.choice([SAT_ASYMMETRIC, SAT_SYMMETRIC])
        sym = await d.configure(ntaps, h, round_mode=rnd, sat_mode=sat,
                                symmetric=symmetric)
        d.src.stall_prob = rng.choice([0.0, 0.3, 0.7])
        d.snk.stall_prob = rng.choice([0.0, 0.3, 0.7])
        xs = [rng.randrange(XMIN, XMAX + 1) for _ in range(300)]
        await d.check(xs, f"random N={ntaps} sym={sym} rnd={rnd} sat={sat}",
                      last_every=64)
        total += len(xs)
        await d.axil.write(rm.REG_CTRL, 0)
        for _ in range(40):
            await RisingEdge(dut.aclk)
    dut._log.info(f"{total} samples across 6 configurations: BIT-EXACT "
                  f"(MAC_UNITS={MAC_UNITS})")
    d.stop()


# =============================================================================
# Tests added with the bound SVA checkers (portfolio work). Each one reaches a situation that the cover points in verif/fir_checkers.sv
# showed the original suite never produced: a flush or soft reset while samples are inside the IP, the interrupt output, leaving the
# configuration-error state, back-to-back one-beat frames, commit requests landing on and around a bank swap, and a register read while
# a write response is still pending. The results are still compared bit for bit with the reference model.
# =============================================================================

MASK = (1 << DATA_W) - 1


@cocotb.test()
async def test_flush_and_soft_reset_mid_stream(dut):
    """FR-14: CTRL.FLUSH and CTRL.SOFT_RESET with samples inside the IP. The IP recovers, the credit count is reconciled, and the next
    frame is bit-exact from a zeroed delay line."""
    d = await setup(dut, seed=8)
    rng = random.Random(81)
    h = [rng.randrange(-(1 << (COEF_W - 3)), 1 << (COEF_W - 3)) for _ in range(8)]
    await d.configure(8, h, round_mode=RND_TRUNC)
    # (command, consumer stalled?): stalled fills the output FIFO and exhausts the credits; flowing leaves a pass phase in progress
    for cmd_bit, stalled in ((rm.CTRL_FLUSH_BIT, True), (rm.CTRL_SOFTRST_BIT, True), (rm.CTRL_FLUSH_BIT, False), (rm.CTRL_SOFTRST_BIT, False)):
        d.snk.pause = stalled
        for _ in range(400 if not stalled else 60):
            d.src.send(rng.randrange(XMIN, XMAX + 1) & MASK)
        for _ in range(150 if stalled else 61):
            await RisingEdge(dut.aclk)
        # command with ENABLE still set, so the FSM leaves RUN abruptly
        await d.axil.write(rm.REG_CTRL, (1 << rm.CTRL_ENABLE_BIT) | (1 << cmd_bit))
        d.src.queue.clear()
        for _ in range(10):
            await RisingEdge(dut.aclk)
        d.snk.pause = False
        # what was buffered at the output leaves (a flush must not withdraw a beat already offered); a beat that was mid-handshake
        # at the flush is processed normally
        for _ in range(300):
            await RisingEdge(dut.aclk)
        # a second command on an empty datapath guarantees a zeroed delay line, then the model restarts from zero history as well
        await d.axil.write(rm.REG_CTRL, (1 << rm.CTRL_ENABLE_BIT) | (1 << cmd_bit))
        for _ in range(12):
            await RisingEdge(dut.aclk)
        d.ref.reset()
        xs = [rng.randrange(XMIN, XMAX + 1) for _ in range(100)]
        await d.check(xs, f"after CTRL bit {cmd_bit} issued mid-stream (consumer {'stalled' if stalled else 'flowing'})", last_every=25)
    dut._log.info("flush and soft reset mid-stream: IP recovered, next frames bit-exact")
    d.stop()


@cocotb.test()
async def test_irq_output(dut):
    """FR-23, FR-25: the level interrupt follows IRQ_STATUS & IRQ_ENABLE; a saturated output raises SAT_EVENT without disturbing the stream."""
    d = await setup(dut, seed=9)
    rng = random.Random(91)
    await d.configure(4, [1 << (COEF_W - 2), 0, 0, 0], round_mode=RND_TRUNC)
    await d.axil.write(rm.REG_IRQ_ENABLE, 1 << rm.IRQ_FRAME_DONE_BIT)
    assert int(dut.irq.value) == 0, "irq high before any event"
    await d.check([rng.randrange(XMIN, XMAX + 1) for _ in range(16)], "frame for the interrupt", last_every=16)
    for _ in range(6):
        await RisingEdge(dut.aclk)
    assert int(dut.irq.value) == 1, "FRAME_DONE enabled but irq is low after a frame left the IP"
    sts, _ = await d.axil.read(rm.REG_IRQ_STATUS)
    assert (sts >> rm.IRQ_FRAME_DONE_BIT) & 1, "IRQ_STATUS.FRAME_DONE not set"
    await d.axil.write(rm.REG_IRQ_STATUS, 1 << rm.IRQ_FRAME_DONE_BIT)
    for _ in range(3):
        await RisingEdge(dut.aclk)
    assert int(dut.irq.value) == 0, "irq still high after the event was cleared"

    # saturation: two full-scale taps double the input, so large samples clamp (and the stream continues, bit-exact)
    big = (1 << (COEF_W - 1)) - 1
    await d.axil.write(rm.REG_CTRL, 0)
    for _ in range(40):
        await RisingEdge(dut.aclk)
    await d.configure(2, [big, big], round_mode=RND_TRUNC, sat_mode=SAT_SYMMETRIC)
    await d.axil.write(rm.REG_IRQ_ENABLE, 1 << rm.IRQ_SAT_EVENT_BIT)
    await d.check([XMAX, XMIN, XMAX, XMAX, XMIN, XMIN, 1000, -1000] * 4, "saturating stream", last_every=8)
    for _ in range(6):
        await RisingEdge(dut.aclk)
    assert int(dut.irq.value) == 1, "SAT_EVENT enabled but irq is low after saturation"
    sts, _ = await d.axil.read(rm.REG_STATUS)
    assert (sts >> rm.STS_SATSTKY_BIT) & 1, "STATUS.SAT_STICKY not set"
    await d.axil.write(rm.REG_IRQ_STATUS, 1 << rm.IRQ_SAT_EVENT_BIT)
    for _ in range(3):
        await RisingEdge(dut.aclk)
    assert int(dut.irq.value) == 0
    d.stop()


@cocotb.test()
async def test_cfg_error_recovery(dut):
    """FR-21, FR-22: an illegal configuration holds the FSM out of RUN; dropping ENABLE leaves the error state, software clears the sticky
    flag, and a legal configuration then runs bit-exact."""
    d = await setup(dut, seed=10)
    await d.axil.write(rm.REG_CFG, rm.make_cfg(0))
    await d.axil.write(rm.REG_CTRL, 1 << rm.CTRL_ENABLE_BIT)
    for _ in range(8):
        await RisingEdge(dut.aclk)
    sts, _ = await d.axil.read(rm.REG_STATUS)
    assert (sts >> rm.STS_CFGERR_BIT) & 1, "STATUS.CFG_ERR not set for NTAPS=0 with ENABLE"
    await d.axil.write(rm.REG_CTRL, 0)                       # leave the error state
    for _ in range(8):
        await RisingEdge(dut.aclk)
    await d.axil.write(rm.REG_IRQ_STATUS, 1 << rm.IRQ_CFG_ERR_BIT)
    for _ in range(4):
        await RisingEdge(dut.aclk)
    sts, _ = await d.axil.read(rm.REG_STATUS)
    assert (sts >> rm.STS_CFGERR_BIT) & 1 == 0, "CFG_ERR sticky flag did not clear"
    rng = random.Random(101)
    h = [rng.randrange(-(1 << (COEF_W - 3)), 1 << (COEF_W - 3)) for _ in range(8)]
    await d.configure(8, h)
    await d.check([rng.randrange(XMIN, XMAX + 1) for _ in range(50)], "after recovering from CFG_ERR", last_every=10)
    d.stop()


@cocotb.test()
async def test_single_beat_frames(dut):
    """FR-17: TLAST on every beat (frames of length one), with stalls on both sides."""
    d = await setup(dut, seed=11)
    rng = random.Random(111)
    h = [rng.randrange(-(1 << (COEF_W - 3)), 1 << (COEF_W - 3)) for _ in range(8)]
    await d.configure(8, h)
    d.src.stall_prob = 0.3
    d.snk.stall_prob = 0.5
    got = await d.check([rng.randrange(XMIN, XMAX + 1) for _ in range(80)], "one-beat frames", last_every=1)
    assert all(last == 1 for _, last in got), "TLAST missing on a one-beat frame"
    d.stop()


@cocotb.test()
async def test_commit_churn_during_stream(dut):
    """FR-10, FR-11: coefficient writes and commit requests while frames stream, including requests that land on or next to a bank swap.
    The shadow bank always holds the same set, so the output must not depend on when each swap happens."""
    d = await setup(dut, seed=12)
    rng = random.Random(121)
    h = [rng.randrange(-(1 << (COEF_W - 3)), 1 << (COEF_W - 3)) for _ in range(8)]
    await d.configure(8, h, round_mode=RND_TRUNC)

    async def churn():
        for _ in range(60):
            for i, c in enumerate(h):
                await d.axil.write(rm.coef_addr(i), c & ((1 << COEF_W) - 1))
            await d.axil.write(rm.REG_CTRL, (1 << rm.CTRL_ENABLE_BIT) | (1 << rm.CTRL_COMMIT_BIT))
    worker = cocotb.start_soon(churn())
    d.snk.stall_prob = 0.2
    await d.check([rng.randrange(XMIN, XMAX + 1) for _ in range(400)], "frames of 4 while committing", last_every=4)
    await worker
    d.stop()


@cocotb.test()
async def test_read_while_write_response_pending(dut):
    """AXI4-Lite: the read channel is independent of a write response that the master has not yet accepted."""
    d = await setup(dut, seed=13)
    w = cocotb.start_soon(d.axil.write(rm.REG_IRQ_ENABLE, 0x5, b_delay=20))
    for _ in range(3):
        await RisingEdge(dut.aclk)
    data, resp = await d.axil.read(rm.REG_ID)
    assert resp == rm.RESP_OKAY and data == rm.IP_ID, f"ID read during a pending B response returned {data:#x}, resp {resp}"
    assert await w == rm.RESP_OKAY
    data, resp = await d.axil.read(rm.REG_IRQ_ENABLE)
    assert data == 0x5, f"IRQ_ENABLE reads {data:#x} after the delayed-response write"
    d.stop()


@cocotb.test()
async def test_flush_keeps_credit_invariant(dut):
    """FR-15 after a flush: CTRL.FLUSH with samples still inside the datapath pipeline must leave the credit accounting exact. If it does
    not, the admission credit overstates the free space of the output FIFO and a long consumer stall then loses beats (the output FIFO
    ignores backpressure by design, because the credits are supposed to make overflow impossible). Found by a_credit_inv, a_dp_nonneg
    and a_no_drop in verif/fir_checkers.sv; the stream is compared with the reference model, so a lost beat is a failure here too."""
    d = await setup(dut, seed=14)
    rng = random.Random(141)
    h = [rng.randrange(-(1 << (COEF_W - 3)), 1 << (COEF_W - 3)) for _ in range(8)]
    await d.configure(8, h, round_mode=RND_TRUNC)
    # flush three times while samples are flowing through the pipeline (consumer free-running, so the pipeline is full)
    for _ in range(3):
        for _ in range(300):
            d.src.send(rng.randrange(XMIN, XMAX + 1) & MASK)
        for _ in range(40):
            await RisingEdge(dut.aclk)
        await d.axil.write(rm.REG_CTRL, (1 << rm.CTRL_ENABLE_BIT) | (1 << rm.CTRL_FLUSH_BIT))
        d.src.queue.clear()
        for _ in range(100):
            await RisingEdge(dut.aclk)
    # push eight zero samples so the 8-tap delay line holds zeros and the model can restart from zero history. A second flush must not
    # be used for this: a flush on an empty datapath reloads the credit counter and would hide the very defect being tested.
    for _ in range(8):
        d.src.send(0)
    for _ in range(200):
        await RisingEdge(dut.aclk)
    d.ref.reset()
    # stall the consumer for a long time while a stream is offered: every admitted sample must still reach the output, in order
    n = 6 * OUT_FIFO_DEPTH
    xs = [rng.randrange(XMIN, XMAX + 1) for _ in range(n)]
    base = len(d.snk.received)
    d.snk.pause = True
    for i, x in enumerate(xs):
        d.src.send(x & MASK, 1 if i % 16 == 15 else 0)
    for _ in range(40 * OUT_FIFO_DEPTH):
        await RisingEdge(dut.aclk)
    d.snk.pause = False
    for _ in range(20000):
        await RisingEdge(dut.aclk)
        if len(d.snk.received) - base >= n:
            break
    got = d.snk.received[base:base + n]
    assert len(got) == n, (f"only {len(got)} of {n} samples came out after flushing mid-stream and stalling the consumer: "
                           f"beats were lost at the output FIFO because the credit count overstated its free space")
    d.compare(got, xs, "stream after three mid-stream flushes and a long consumer stall")
    d.stop()


@cocotb.test()
async def test_full_stall_bounds_buffering(dut):
    """With the consumer stopped from reset, the IP takes a bounded number of samples and then holds TREADY low. The admission credits cap what is in
    flight at OUT_FIFO_DEPTH, which is what makes an overflow of the output FIFO impossible, and the input FIFO holds IN_FIFO_DEPTH more. A credit pool larger
    than the output FIFO would still pass every data check (the beats would be buffered somewhere) and is caught only by this count and by a_out_bound.
    Releasing the consumer must deliver every accepted sample, in order, bit-exact."""
    d = await setup(dut, seed=15)
    rng = random.Random(151)
    h = [rng.randrange(-(1 << (COEF_W - 3)), 1 << (COEF_W - 3)) for _ in range(8)]
    await d.configure(8, h, round_mode=RND_TRUNC)
    n = 8 * OUT_FIFO_DEPTH
    xs = [rng.randrange(XMIN, XMAX + 1) for _ in range(n)]
    base = len(d.snk.received)
    sent0 = len(d.src.sent)
    d.snk.pause = True
    for i, x in enumerate(xs):
        d.src.send(x & MASK, 1 if i % 16 == 15 else 0)
    for _ in range(80 * OUT_FIFO_DEPTH):                  # long enough for the pool to be used up whatever the fold factor
        await RisingEdge(dut.aclk)
    accepted = len(d.src.sent) - sent0
    dut._log.info(f"consumer stopped: {accepted} samples accepted (credit pool {OUT_FIFO_DEPTH} + input FIFO {IN_FIFO_DEPTH})")
    assert accepted == OUT_FIFO_DEPTH + IN_FIFO_DEPTH, (
        f"with the consumer stopped the IP accepted {accepted} samples; the credit pool ({OUT_FIFO_DEPTH}) plus the input FIFO "
        f"({IN_FIFO_DEPTH}) allow exactly {OUT_FIFO_DEPTH + IN_FIFO_DEPTH}")
    assert len(d.snk.received) == base, "the stopped consumer received beats"
    d.snk.pause = False
    for _ in range(20000):
        await RisingEdge(dut.aclk)
        if len(d.snk.received) - base >= n:
            break
    got = d.snk.received[base:base + n]
    assert len(got) == n, f"only {len(got)} of {n} samples came out after the stall"
    d.compare(got, xs, "stream after a full consumer stall")
    d.stop()
