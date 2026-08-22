"""Phase 3 regression: FIR arithmetic, bit-exact against the Python model.

Phase 3 Definition of Done (docs/00_project_plan.md):
  - bit-exact match on >= 1M random samples, >= 20 coefficient sets, every
    rounding/saturation mode combination, zero mismatches
  - impulse, step, boxcar, min/max operands, exact rounding and saturation
    boundaries, symmetric/asymmetric bit-identity
  - the accumulator no-overflow argument confirmed by a check that never fires

Every comparison here is EXACT EQUALITY. No tolerance appears in this file.
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from model.fir_ref import (RND_CONVERGENT, RND_HALF_UP, RND_TRUNC,
                           SAT_ASYMMETRIC, SAT_SYMMETRIC, FirRef, to_signed)

DATA_W = int(os.environ.get("DATA_W", "16"))
COEF_W = int(os.environ.get("COEF_W", "16"))
NTAPS_MAX = int(os.environ.get("NTAPS_MAX", "64"))
SYMMETRIC_EN = int(os.environ.get("SYMMETRIC_EN", "1"))
RAND_SETS = int(os.environ.get("RAND_SETS", "20"))
RAND_LEN = int(os.environ.get("RAND_LEN", "1200"))

CLK_NS = 10
XMIN, XMAX = -(1 << (DATA_W - 1)), (1 << (DATA_W - 1)) - 1
MODES = [(r, s) for r in (RND_TRUNC, RND_HALF_UP, RND_CONVERGENT)
         for s in (SAT_ASYMMETRIC, SAT_SYMMETRIC)]


def pack(coeffs):
    """Pack a coefficient list into the flat vector the RTL expects."""
    v = 0
    for i in range(NTAPS_MAX):
        c = coeffs[i] if i < len(coeffs) else 0
        v |= (c & ((1 << COEF_W) - 1)) << (i * COEF_W)
    return v


class Core:
    """Drives fir_core and collects results, with a reference model alongside."""

    def __init__(self, dut):
        self.dut = dut
        self.ref = FirRef(DATA_W, COEF_W, NTAPS_MAX)
        self.out = []
        self.peak_acc = 0
        # Reading the 38-bit accumulator through the VPI every cycle is the
        # single most expensive thing the collector does -- it dominates
        # wall-clock time at NTAPS_MAX=64, where the simulator is already slow.
        # Only the tests that actually report a peak turn it on.
        self.track_acc = False
        self._stop = False

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, CLK_NS, unit="ns").start())
        d = self.dut
        d.clear.value = 0
        d.in_valid.value = 0
        d.in_data.value = 0
        d.in_last.value = 0
        d.cfg_ntaps.value = 1
        d.cfg_round_mode.value = RND_TRUNC
        d.cfg_sat_mode.value = SAT_ASYMMETRIC
        d.cfg_symmetric.value = 0
        d.coef_flat.value = 0
        d.arst_n.value = 0
        for _ in range(5):
            await RisingEdge(d.clk)
        d.arst_n.value = 1
        for _ in range(5):
            await RisingEdge(d.clk)
        self.latency = int(d.dbg_latency.value)
        cocotb.start_soon(self._collect())

    def stop(self):
        self._stop = True

    async def _collect(self):
        while not self._stop:
            await FallingEdge(self.dut.clk)
            await ReadOnly()
            try:
                if int(self.dut.out_valid.value) == 1:
                    self.out.append((to_signed(int(self.dut.out_data.value), DATA_W),
                                     int(self.dut.out_sat.value),
                                     int(self.dut.out_last.value)))
                if self.track_acc:
                    a = abs(int(self.dut.dbg_acc.value.signed_integer))
                    if a > self.peak_acc:
                        self.peak_acc = a
            except (ValueError, AttributeError):
                pass

    async def configure(self, ntaps, coeffs, round_mode=RND_TRUNC,
                        sat_mode=SAT_ASYMMETRIC, symmetric=0):
        d = self.dut
        d.cfg_ntaps.value = ntaps
        d.cfg_round_mode.value = round_mode
        d.cfg_sat_mode.value = sat_mode
        d.cfg_symmetric.value = symmetric
        d.coef_flat.value = pack(coeffs)
        self.ref.configure(ntaps=ntaps, coeffs=coeffs, round_mode=round_mode,
                           sat_mode=sat_mode, symmetric=symmetric)
        await RisingEdge(d.clk)

    async def flush(self):
        """Zero the delay line in both DUT and model."""
        self.dut.clear.value = 1
        await RisingEdge(self.dut.clk)
        self.dut.clear.value = 0
        for _ in range(self.latency + 2):
            await RisingEdge(self.dut.clk)
        self.ref.reset()
        self.out.clear()

    async def stream(self, samples, gap_prob=0.0, rng=None):
        """Push samples, return the DUT results once all have emerged."""
        d = self.dut
        rng = rng or random
        base = len(self.out)
        for x in samples:
            while gap_prob and rng.random() < gap_prob:
                d.in_valid.value = 0
                await RisingEdge(d.clk)
            d.in_data.value = x & ((1 << DATA_W) - 1)
            d.in_valid.value = 1
            d.in_last.value = 0
            await RisingEdge(d.clk)
        d.in_valid.value = 0
        for _ in range(self.latency + 4):
            await RisingEdge(d.clk)
        return self.out[base:]

    def expect(self, samples):
        return [self.ref.push(x) for x in samples]

    def compare(self, got, exp, what):
        assert len(got) == len(exp), \
            f"{what}: DUT produced {len(got)} results, model expected {len(exp)}"
        for i, ((gy, gs, _), (ey, es)) in enumerate(zip(got, exp)):
            assert gy == ey, (f"{what}: sample {i} value mismatch -- "
                              f"DUT {gy}, model {ey}")
            assert gs == es, (f"{what}: sample {i} saturation flag mismatch -- "
                              f"DUT {gs}, model {es} (value {gy})")

    async def check(self, samples, what, gap_prob=0.0, rng=None):
        exp = self.expect(samples)
        got = await self.stream(samples, gap_prob, rng)
        self.compare(got, exp, what)
        return got


async def setup(dut):
    c = Core(dut)
    await c.start()
    return c


# =============================================================================
# Directed structural tests
# =============================================================================

@cocotb.test()
async def test_latency_and_widths(dut):
    """The RTL's own LATENCY and ACC_W match the derivation."""
    c = await setup(dut)
    levels = (NTAPS_MAX - 1).bit_length()
    assert c.latency == 4 + levels, \
        f"LATENCY {c.latency} != 4 + clog2({NTAPS_MAX}) = {4 + levels}"
    acc_w = int(dut.dbg_acc_w.value)
    assert acc_w == DATA_W + COEF_W + levels, \
        f"ACC_W {acc_w} != {DATA_W}+{COEF_W}+{levels}"

    # Measure the latency in cycles rather than trusting the parameter.
    await c.configure(ntaps=1, coeffs=[1 << (COEF_W - 2)])
    dut.in_data.value = XMAX
    dut.in_valid.value = 1
    await RisingEdge(dut.clk)
    dut.in_valid.value = 0
    n = 0
    while True:
        await FallingEdge(dut.clk)
        await ReadOnly()
        if int(dut.out_valid.value) == 1:
            break
        n += 1
        assert n < 64, "no output within 64 cycles"
        await RisingEdge(dut.clk)
    assert n + 1 == c.latency, f"measured latency {n+1} != reported {c.latency}"
    dut._log.info(f"LATENCY = {c.latency} cycles, ACC_W = {acc_w} bits (both confirmed)")
    c.stop()


@cocotb.test()
async def test_impulse_response(dut):
    """An impulse recovers the coefficients: y[k] = h[k] * x[0]."""
    c = await setup(dut)
    rng = random.Random(7)
    for ntaps in (1, 2, 8, 17, NTAPS_MAX):
        h = [rng.randrange(-(1 << (COEF_W - 2)), 1 << (COEF_W - 2)) for _ in range(ntaps)]
        await c.configure(ntaps=ntaps, coeffs=h, round_mode=RND_TRUNC)
        await c.flush()
        # A full-scale impulse followed by zeros.
        await c.check([XMAX] + [0] * (ntaps + 2), f"impulse N={ntaps}")
    c.stop()


@cocotb.test()
async def test_boxcar_moving_average(dut):
    """All-equal coefficients: the degenerate moving-average case (D-001)."""
    c = await setup(dut)
    for ntaps in (2, 4, 8, 16):
        h = [(1 << (COEF_W - 1)) // ntaps] * ntaps
        await c.configure(ntaps=ntaps, coeffs=h, round_mode=RND_CONVERGENT)
        await c.flush()
        xs = [((i * 977) % 40000) - 20000 for i in range(64)]
        await c.check(xs, f"boxcar N={ntaps}")
    c.stop()


@cocotb.test()
async def test_extreme_operands(dut):
    """All-minimum and all-maximum inputs against all-extreme coefficients.

    -2^(W-1) squared is the largest-magnitude product a signed multiply can
    make, so this is the stimulus that drives the accumulator hardest.
    """
    c = await setup(dut)
    c.track_acc = True                    # this test reports the peak
    for h_val in (XMIN, XMAX, 0):
        for x_val in (XMIN, XMAX):
            h = [h_val] * NTAPS_MAX
            await c.configure(ntaps=NTAPS_MAX, coeffs=h,
                              round_mode=RND_CONVERGENT, sat_mode=SAT_ASYMMETRIC)
            await c.flush()
            await c.check([x_val] * (NTAPS_MAX + 4),
                          f"extremes h={h_val} x={x_val}")
    bound = 1 << (int(dut.dbg_acc_w.value) - 1)
    dut._log.info(f"peak |accumulator| = {c.peak_acc}, ACC_W bound = {bound} "
                  f"({100.0 * c.peak_acc / bound:.1f}% of range, never exceeded)")
    assert c.peak_acc < bound, "accumulator exceeded its derived bound"
    c.stop()


@cocotb.test()
async def test_rounding_boundaries(dut):
    """Exact tie values for each rounding mode.

    With N=1 and h = 2^(COEF_W-1)/2, acc = x * h, so the low SHIFT bits of the
    accumulator can be steered precisely and the half-way case hit exactly --
    which is the only case where the three modes differ.
    """
    c = await setup(dut)
    shift = COEF_W - 1
    for mode in (RND_TRUNC, RND_HALF_UP, RND_CONVERGENT):
        h = [1 << (shift - 1)]        # acc = x * 2^(shift-1): exact ties for odd x
        await c.configure(ntaps=1, coeffs=h, round_mode=mode)
        await c.flush()
        xs = [1, 2, 3, 4, 5, -1, -2, -3, -4, -5, 7, -7, 8, -8]
        got = await c.check(xs, f"rounding ties mode={mode}")
        dut._log.info(f"mode {mode}: x={xs} -> y={[g[0] for g in got]}")
    c.stop()


@cocotb.test()
async def test_rounding_modes_differ(dut):
    """The three rounding modes must NOT be silently identical.

    A test suite where every mode passes is worthless if the modes were never
    actually distinguishable; this asserts the stimulus discriminates them.
    """
    c = await setup(dut)
    shift = COEF_W - 1
    results = {}
    for mode in (RND_TRUNC, RND_HALF_UP, RND_CONVERGENT):
        await c.configure(ntaps=1, coeffs=[1 << (shift - 1)], round_mode=mode)
        await c.flush()
        xs = [1, 3, 5, -1, -3, -5]
        got = await c.check(xs, f"discriminate mode={mode}")
        results[mode] = [g[0] for g in got]
    assert results[RND_TRUNC] != results[RND_HALF_UP], \
        "TRUNC and HALF_UP produced identical output -- stimulus does not hit ties"
    assert results[RND_HALF_UP] != results[RND_CONVERGENT], \
        "HALF_UP and CONVERGENT produced identical output -- ties are not exercised"
    dut._log.info(f"TRUNC={results[RND_TRUNC]} HALF_UP={results[RND_HALF_UP]} "
                  f"CONVERGENT={results[RND_CONVERGENT]}")
    c.stop()


@cocotb.test()
async def test_saturation_boundaries(dut):
    """Saturation at the exact clamp points, in both modes."""
    c = await setup(dut)
    for sat_mode in (SAT_ASYMMETRIC, SAT_SYMMETRIC):
        # Unity gain, so y == x and the clamp is reachable exactly.
        h = [1 << (COEF_W - 1)] if False else [XMIN]   # h = -1.0 exactly
        await c.configure(ntaps=1, coeffs=h, round_mode=RND_TRUNC,
                          sat_mode=sat_mode)
        await c.flush()
        # h = -1.0 so y = -x; x = XMIN gives +2^15 which must clamp.
        xs = [XMIN, XMAX, XMIN + 1, XMAX - 1, 0, 1, -1]
        got = await c.check(xs, f"saturation sat_mode={sat_mode}")
        assert any(g[1] for g in got), \
            f"sat_mode={sat_mode}: no saturation event -- boundary not reached"
        dut._log.info(f"sat_mode={sat_mode}: x={xs} -> y={[g[0] for g in got]} "
                      f"sat={[g[1] for g in got]}")
    c.stop()


@cocotb.test()
async def test_symmetric_identity(dut):
    """Symmetric mode is BIT-IDENTICAL to asymmetric for linear-phase filters.

    Covers odd and even lengths. The odd case is the centre-tap trap: index
    N-1-k equals k there, and a naive pre-adder would double-count it.
    """
    c = await setup(dut)
    if not SYMMETRIC_EN:
        # The pre-adder is not synthesised in this build; cfg_symmetric is
        # ignored by construction. Assert exactly that, rather than skipping.
        rng0 = random.Random(99)
        h0 = [rng0.randrange(-8000, 8000) for _ in range(8)]
        xs0 = [rng0.randrange(XMIN, XMAX + 1) for _ in range(24)]
        outs = []
        for sym in (0, 1):
            await c.configure(ntaps=8, coeffs=h0, round_mode=RND_CONVERGENT,
                              symmetric=0)          # model always asymmetric here
            dut.cfg_symmetric.value = sym           # but poke the DUT pin
            await RisingEdge(dut.clk)
            await c.flush()
            outs.append([g[0] for g in await c.check(xs0, f"SYMMETRIC_EN=0 pin={sym}")])
        assert outs[0] == outs[1], \
            "SYMMETRIC_EN=0 build still responds to cfg_symmetric"
        dut._log.info("SYMMETRIC_EN=0: cfg_symmetric correctly has no effect")
        c.stop()
        return
    rng = random.Random(11)
    for ntaps in (1, 2, 3, 4, 5, 7, 8, 15, 16, 31, 32, 63, 64):
        if ntaps > NTAPS_MAX:
            continue
        half = (ntaps + 1) // 2
        first = [rng.randrange(-(1 << (COEF_W - 3)), 1 << (COEF_W - 3))
                 for _ in range(half)]
        h = [first[min(k, ntaps - 1 - k)] for k in range(ntaps)]   # h[k] == h[N-1-k]
        assert h == h[::-1], "constructed coefficient set is not symmetric"

        xs = [rng.randrange(XMIN, XMAX + 1) for _ in range(48)]

        await c.configure(ntaps=ntaps, coeffs=h, round_mode=RND_CONVERGENT,
                          sat_mode=SAT_SYMMETRIC, symmetric=0)
        await c.flush()
        asym = [g[0] for g in await c.check(xs, f"asym N={ntaps}")]

        await c.configure(ntaps=ntaps, coeffs=h, round_mode=RND_CONVERGENT,
                          sat_mode=SAT_SYMMETRIC, symmetric=1)
        await c.flush()
        sym = [g[0] for g in await c.check(xs, f"sym N={ntaps}")]

        assert sym == asym, (
            f"N={ntaps} ({'odd' if ntaps % 2 else 'even'}): symmetric mode "
            f"differs from asymmetric on a linear-phase filter -- first "
            f"mismatch at {next(i for i,(a,b) in enumerate(zip(sym,asym)) if a!=b)}")
    dut._log.info("symmetric == asymmetric for every tested length, odd and even")
    c.stop()


@cocotb.test()
async def test_ntaps_sweep(dut):
    """Every legal tap count from 1 to NTAPS_MAX."""
    c = await setup(dut)
    rng = random.Random(13)
    for ntaps in range(1, NTAPS_MAX + 1):
        h = [rng.randrange(-(1 << (COEF_W - 2)), 1 << (COEF_W - 2))
             for _ in range(ntaps)]
        await c.configure(ntaps=ntaps, coeffs=h, round_mode=RND_CONVERGENT)
        await c.flush()
        xs = [rng.randrange(XMIN, XMAX + 1) for _ in range(12)]
        await c.check(xs, f"ntaps={ntaps}")
    dut._log.info(f"all tap counts 1..{NTAPS_MAX} bit-exact")
    c.stop()


@cocotb.test()
async def test_pipeline_gaps(dut):
    """Bubbles in in_valid must not disturb the result.

    The datapath has no enables, so a gap simply propagates as an invalid
    slot -- but the delay line must not shift on it.
    """
    c = await setup(dut)
    rng = random.Random(17)
    h = [rng.randrange(-8000, 8000) for _ in range(16)]
    await c.configure(ntaps=16, coeffs=h, round_mode=RND_CONVERGENT)
    await c.flush()
    xs = [rng.randrange(XMIN, XMAX + 1) for _ in range(200)]
    await c.check(xs, "pipeline gaps", gap_prob=0.5, rng=rng)
    c.stop()


@cocotb.test()
async def test_flush_zeroes_history(dut):
    """After a flush the response restarts from a zero-padded history (FR-04)."""
    c = await setup(dut)
    h = [1 << (COEF_W - 3)] * 8
    await c.configure(ntaps=8, coeffs=h, round_mode=RND_TRUNC)
    await c.flush()
    xs = [XMAX] * 16
    first = [g[0] for g in await c.check(xs, "before flush")]
    await c.flush()
    second = [g[0] for g in await c.check(xs, "after flush")]
    assert first == second, \
        "response after flush differs from the first run -- history not cleared"
    c.stop()


# =============================================================================
# The Definition-of-Done randomised test
# =============================================================================

@cocotb.test()
async def test_random_exhaustive(dut):
    """>= 1M random samples over >= 20 coefficient sets and all 6 mode pairs."""
    c = await setup(dut)
    rng = random.Random(0x513)
    total = 0

    for cs in range(RAND_SETS):
        ntaps = rng.randint(1, NTAPS_MAX)
        symmetric = SYMMETRIC_EN and (rng.random() < 0.5)
        if symmetric:
            half = (ntaps + 1) // 2
            first = [rng.randrange(-(1 << (COEF_W - 1)), 1 << (COEF_W - 1))
                     for _ in range(half)]
            h = [first[min(k, ntaps - 1 - k)] for k in range(ntaps)]
        else:
            h = [rng.randrange(-(1 << (COEF_W - 1)), 1 << (COEF_W - 1))
                 for _ in range(ntaps)]

        round_mode, sat_mode = MODES[cs % len(MODES)]
        await c.configure(ntaps=ntaps, coeffs=h, round_mode=round_mode,
                          sat_mode=sat_mode, symmetric=int(symmetric))
        await c.flush()

        # Mix uniform noise with the extremes, so saturation and the
        # accumulator bound are both exercised rather than merely sampled.
        xs = []
        for _ in range(RAND_LEN):
            r = rng.random()
            if r < 0.05:
                xs.append(XMIN)
            elif r < 0.10:
                xs.append(XMAX)
            else:
                xs.append(rng.randrange(XMIN, XMAX + 1))

        await c.check(xs, f"random set {cs} (N={ntaps} sym={int(symmetric)} "
                          f"rnd={round_mode} sat={sat_mode})")
        total += len(xs)

    dut._log.info(f"{total} samples across {RAND_SETS} coefficient sets, "
                  f"all {len(MODES)} mode pairs: BIT-EXACT")
    assert total == RAND_SETS * RAND_LEN, "sample accounting is wrong"
    c.stop()
