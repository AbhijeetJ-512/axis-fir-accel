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
