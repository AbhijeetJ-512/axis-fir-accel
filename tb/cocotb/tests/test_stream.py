"""Phase 2 regression: AXI4-Stream path, elastic buffers and credit flow control.

Phase 2 Definition of Done (docs/00_project_plan.md):
  - byte-for-byte input/output match over >= 100k randomised beats with
    randomised handshakes
  - zero protocol violations
  - combinational independence of s_axis_tready from m_axis_tready

The last item is a structural property and is checked separately by
scripts/check_comb_independence.sh against the synthesised netlist; asserting it
in simulation would only sample it, not establish it.

Every test in this file checks LOSSLESSNESS AND ORDER by comparing the exact
sequence the source got accepted against the exact sequence the sink observed,
including TLAST position. No test passes on "it looked busy".
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

from env.axis_driver import (AxiStreamProtocolMonitor, AxiStreamSink,
                             AxiStreamSource, CreditInvariantChecker)

DATA_W = int(os.environ.get("DATA_W", "16"))
IN_FIFO_DEPTH = int(os.environ.get("IN_FIFO_DEPTH", "8"))
OUT_FIFO_DEPTH = int(os.environ.get("OUT_FIFO_DEPTH", "8"))
PIPE_DEPTH = int(os.environ.get("PIPE_DEPTH", "4"))
LONG_BEATS = int(os.environ.get("LONG_BEATS", "100000"))

CLK_PERIOD_NS = 10
MASK = (1 << DATA_W) - 1


class CycleCounter:
    def __init__(self, clk):
        self.clk, self.n, self._stop = clk, 0, False

    def stop(self):
        self._stop = True

    async def run(self):
        while not self._stop:
            await RisingEdge(self.clk)
            self.n += 1


class Env:
    """Everything a stream test needs, started and stopped as a unit."""

    def __init__(self, dut, seed=0):
        self.dut = dut
        self.rng = random.Random(seed)
        self.src = AxiStreamSource(dut, dut.clk, "s_axis_", random.Random(seed + 1))
        self.snk = AxiStreamSink(dut, dut.clk, "m_axis_", random.Random(seed + 2))
        self.mon_in = AxiStreamProtocolMonitor(dut, dut.clk, "s_axis_", "s_axis")
        self.mon_out = AxiStreamProtocolMonitor(dut, dut.clk, "m_axis_", "m_axis")
        self.credit = CreditInvariantChecker(dut, dut.clk, OUT_FIFO_DEPTH)
        self.cycles = CycleCounter(dut.clk)

    def start(self):
        for c in (self.src, self.snk, self.mon_in, self.mon_out,
                  self.credit, self.cycles):
            cocotb.start_soon(c.run())

    def stop(self):
        for c in (self.src, self.snk, self.mon_in, self.mon_out,
                  self.credit, self.cycles):
            c.stop()

    def check(self, expect_beats=None):
        """Scoreboard: exact sequence equality, plus protocol and invariant."""
        assert not self.credit.errors, \
            "CREDIT INVARIANT: " + "; ".join(self.credit.errors[:5])
        assert not self.mon_in.errors, \
            "INPUT PROTOCOL: " + "; ".join(self.mon_in.errors[:5])
        assert not self.mon_out.errors, \
            "OUTPUT PROTOCOL: " + "; ".join(self.mon_out.errors[:5])

        sent, recv = self.src.sent, self.snk.received
        assert len(sent) == len(recv), (
            f"beat count mismatch: {len(sent)} accepted in, {len(recv)} out "
            f"({len(sent) - len(recv)} lost)")
        if expect_beats is not None:
            assert len(sent) == expect_beats, \
                f"expected {expect_beats} beats, source only placed {len(sent)}"

        for i, (s, r) in enumerate(zip(sent, recv)):
            assert s == r, (
                f"beat {i}: sent (data=0x{s[0]:X}, last={s[1]}) but received "
                f"(data=0x{r[0]:X}, last={r[1]})")

        self.dut._log.info(
            f"{len(sent)} beats matched exactly | peak in_fifo={self.credit.peak_in} "
            f"peak out_fifo={self.credit.peak_out}/{OUT_FIFO_DEPTH} "
            f"| {self.cycles.n} cycles")


async def setup(dut, seed=0):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    env = Env(dut, seed)
    dut.soft_clear.value = 0
    await env.src.reset()
    dut.m_axis_tready.value = 0

    dut.arst_n.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.arst_n.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)

    env.start()
    return env


async def drain(dut, env, timeout_cycles=200000):
    """Wait until the design is genuinely empty.

    Quiescence is defined structurally -- both FIFOs empty and all credits
    returned -- rather than as "sent count equals received count". The flush
    tests deliberately discard data, so a count comparison would never settle
    there, and a purely time-based wait would race a slow consumer.
    """
    settle = 0
    need = PIPE_DEPTH + IN_FIFO_DEPTH + OUT_FIFO_DEPTH + 16
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        quiet = (env.src.idle()
                 and int(dut.dbg_in_count.value) == 0
                 and int(dut.dbg_out_count.value) == 0
                 and int(dut.dbg_credits.value) == OUT_FIFO_DEPTH)
        settle = settle + 1 if quiet else 0
        if settle > need:
            return
    raise AssertionError(
        f"drain timeout: {len(env.src.sent)} accepted in, "
        f"{len(env.snk.received)} out, {len(env.src.queue)} still queued, "
        f"in_fifo={int(dut.dbg_in_count.value)} out_fifo={int(dut.dbg_out_count.value)} "
        f"credits={int(dut.dbg_credits.value)}")


# =============================================================================
# Tests
# =============================================================================

@cocotb.test()
async def test_single_beat(dut):
    """One beat in, the same beat out."""
    env = await setup(dut, seed=1)
    env.src.send(0x1234 & MASK, last=1)
    await drain(dut, env)
    env.stop()
    env.check(expect_beats=1)


@cocotb.test()
async def test_single_frame(dut):
    """A frame of 16 beats keeps its TLAST on the last beat only."""
    env = await setup(dut, seed=2)
    vals = [(i * 7 + 3) & MASK for i in range(16)]
    env.src.send_frame(vals)
    await drain(dut, env)
    env.stop()
    env.check(expect_beats=16)
    lasts = [i for i, (_, l) in enumerate(env.snk.received) if l]
    assert lasts == [15], f"TLAST appeared at beats {lasts}, expected only [15]"


@cocotb.test()
async def test_continuous_stream_ii1(dut):
    """Sustained streaming with no stalls achieves one beat per cycle."""
    env = await setup(dut, seed=3)
    n = 2000
    for i in range(n):
        env.src.send(i & MASK, last=1 if (i % 64) == 63 else 0)

    # Measure to the arrival of the last BEAT, not to quiescence: drain() waits
    # for a settle window proportional to the FIFO depths, which would be
    # charged against throughput and make a deep-FIFO build look slow.
    start_cycles = env.cycles.n
    for _ in range(20 * n + 1000):
        await RisingEdge(dut.clk)
        if len(env.snk.received) >= n:
            break
    elapsed = env.cycles.n - start_cycles

    await drain(dut, env)
    env.stop()
    env.check(expect_beats=n)

    # Only pipeline fill and FIFO transit are permitted overhead. If the credit
    # counter throttled admission below one per cycle this would be ~2n.
    overhead = PIPE_DEPTH + IN_FIFO_DEPTH + OUT_FIFO_DEPTH + 16
    assert elapsed <= n + overhead, (
        f"II=1 not sustained: {n} beats took {elapsed} cycles "
        f"(budget {n + overhead}); credits may be throttling admission")
    dut._log.info(f"throughput: {n} beats in {elapsed} cycles "
                  f"(II = {elapsed / n:.3f})")


@cocotb.test()
async def test_input_backpressure(dut):
    """Sparse producer: gaps in TVALID must not lose or reorder anything."""
    env = await setup(dut, seed=4)
    env.src.stall_prob = 0.7
    n = 500
    for i in range(n):
        env.src.send((i * 13) & MASK, last=1 if (i % 32) == 31 else 0)
    await drain(dut, env)
    env.stop()
    env.check(expect_beats=n)


@cocotb.test()
async def test_output_backpressure(dut):
    """Sparse consumer: backpressure must propagate without loss."""
    env = await setup(dut, seed=5)
    env.snk.stall_prob = 0.8
    n = 500
    for i in range(n):
        env.src.send((i * 29) & MASK, last=1 if (i % 16) == 15 else 0)
    await drain(dut, env)
    env.stop()
    env.check(expect_beats=n)
    # With the consumer stalling this hard the output FIFO must have filled.
    assert env.credit.peak_out >= OUT_FIFO_DEPTH - 1, (
        f"output FIFO only reached {env.credit.peak_out}/{OUT_FIFO_DEPTH}; "
        f"backpressure was not actually exercised")


@cocotb.test()
async def test_simultaneous_stall(dut):
    """Both sides stalling independently at high rates."""
    env = await setup(dut, seed=6)
    env.src.stall_prob = 0.6
    env.snk.stall_prob = 0.6
    n = 500
    for i in range(n):
        env.src.send((i * 41) & MASK, last=1 if (i % 7) == 6 else 0)
    await drain(dut, env)
    env.stop()
    env.check(expect_beats=n)


@cocotb.test()
async def test_indefinite_output_stall(dut):
    """Consumer stalls for a long time, then resumes: no loss, clean restart."""
    env = await setup(dut, seed=7)
    n = 64
    for i in range(n):
        env.src.send((i * 5 + 1) & MASK, last=1 if i == n - 1 else 0)

    env.snk.pause = True
    for _ in range(500):                    # park the design
        await RisingEdge(dut.clk)

    # Everything must be parked: FIFOs full, credits exhausted, input stalled.
    assert int(dut.dbg_have_credit.value) == 0, \
        "credits not exhausted after a 500-cycle downstream stall"
    assert int(dut.s_axis_tready.value) == 0, \
        "s_axis_tready still high with the design fully backed up"
    assert len(env.snk.received) == 0, "sink received data while paused"

    env.snk.pause = False
    await drain(dut, env)
    env.stop()
    env.check(expect_beats=n)


@cocotb.test()
async def test_tlast_positions(dut):
    """Frames of every awkward length, including single-beat frames."""
    env = await setup(dut, seed=8)
    env.src.stall_prob = 0.3
    env.snk.stall_prob = 0.3

    lengths = [1, 1, 2, 3, 1, 5, 8, 1, 13, 16, 17, 31, 1, 2]
    expected_last = []
    idx = 0
    for L in lengths:
        env.src.send_frame([(idx + k) & MASK for k in range(L)])
        idx += L
        expected_last.append(idx - 1)

    await drain(dut, env)
    env.stop()
    env.check(expect_beats=sum(lengths))

    got_last = [i for i, (_, l) in enumerate(env.snk.received) if l]
    assert got_last == expected_last, (
        f"TLAST positions {got_last} != expected {expected_last}")


@cocotb.test()
async def test_pipeline_bubbles(dut):
    """Deliberate single-cycle gaps: the pipe must tolerate holes in the valid chain."""
    env = await setup(dut, seed=9)
    n = 300
    for i in range(n):
        env.src.send((i * 3) & MASK, last=0)

    # Alternate short bursts and gaps rather than a uniform stall probability,
    # so bubbles land at fixed pipeline positions instead of averaging out.
    async def burst():
        for _ in range(n * 4):
            env.src.stall_prob = 0.0
            await RisingEdge(dut.clk)
            await RisingEdge(dut.clk)
            env.src.stall_prob = 1.0
            await RisingEdge(dut.clk)

    cocotb.start_soon(burst())
    await drain(dut, env)
    env.stop()
    env.check(expect_beats=n)


# Flush tests tag their traffic so that pre- and post-flush beats are
# distinguishable in the output stream. Clearing the scoreboard lists instead
# would be unsound: the source may already have popped a beat and be holding it
# on the bus, so that beat is inside the design but absent from `sent`.
TAG = 1 << (DATA_W - 1)
UNTAGGED = MASK ^ TAG      # value space reserved for pre-flush traffic


def check_flush_boundary(env, post_sent, what):
    """After a flush, assert the post-flush stream is an exact, clean suffix.

    The guarantee a flush gives is not "no data was lost" -- a flush discards by
    definition. It is: everything sent AFTER the flush comes out, in order, and
    no pre-flush beat appears once post-flush data has started.
    """
    recv = env.snk.received
    first_post = next((i for i, (d, _) in enumerate(recv) if d & TAG), len(recv))
    tail = recv[first_post:]

    stragglers = [i for i, (d, _) in enumerate(tail) if not (d & TAG)]
    assert not stragglers, (
        f"{what}: pre-flush data reappeared after post-flush data began "
        f"(at tail indices {stragglers[:5]})")
    assert tail == post_sent, (
        f"{what}: post-flush stream mismatch -- {len(post_sent)} sent, "
        f"{len(tail)} received")


@cocotb.test()
async def test_reset_midstream(dut):
    """Reset during traffic leaves a clean, usable design (requirement CR-05)."""
    env = await setup(dut, seed=10)
    for i in range(200):
        env.src.send(i & UNTAGGED, last=0)      # untagged: pre-reset traffic
    for _ in range(50):
        await RisingEdge(dut.clk)

    dut.arst_n.value = 0
    for _ in range(4):
        await RisingEdge(dut.clk)
        # AXI4-Stream constrains the MASTER during reset: TVALID must be low.
        # It places no requirement on TREADY, and this IP deliberately does not
        # gate s_axis_tready with reset -- see docs/01_requirements.md CR-04.
        assert int(dut.m_axis_tvalid.value) == 0, "m_axis_tvalid high during reset"
    dut.arst_n.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)

    n = 200
    post = [((i * 11) & UNTAGGED) | TAG for i in range(n)]
    for i, v in enumerate(post):
        env.src.send(v, last=1 if i == n - 1 else 0)
    post_sent = [(v, 1 if i == n - 1 else 0) for i, v in enumerate(post)]

    await drain(dut, env)
    env.stop()
    assert not env.credit.errors, "; ".join(env.credit.errors[:3])
    assert not env.mon_out.errors, "; ".join(env.mon_out.errors[:3])
    check_flush_boundary(env, post_sent, "reset")


@cocotb.test()
async def test_soft_clear(dut):
    """soft_clear empties the FIFOs, restores credits, and leaves the path working."""
    env = await setup(dut, seed=11)
    env.snk.pause = True
    for i in range(20):
        env.src.send(i & UNTAGGED, last=0)      # untagged: pre-flush traffic
    for _ in range(60):
        await RisingEdge(dut.clk)
    assert int(dut.dbg_out_count.value) > 0, "nothing buffered before soft_clear"

    env.src.queue.clear()
    dut.soft_clear.value = 1
    await RisingEdge(dut.clk)
    dut.soft_clear.value = 0
    await RisingEdge(dut.clk)

    # The input FIFO and pipeline are flushed. The OUTPUT FIFO deliberately is
    # not: dropping m_axis_tvalid on a beat the consumer has already been
    # offered would violate AXI4-Stream. Credits are reconciled to the beats
    # still buffered, so the invariant survives the flush exactly.
    assert int(dut.dbg_in_count.value) == 0, "input FIFO not emptied by soft_clear"
    out_occ = int(dut.dbg_out_count.value)
    assert out_occ > 0, "output FIFO should have retained its buffered beats"
    assert int(dut.dbg_credits.value) == OUT_FIFO_DEPTH - out_occ, (
        f"credits not reconciled by soft_clear: got {int(dut.dbg_credits.value)}, "
        f"expected {OUT_FIFO_DEPTH - out_occ} for an occupancy of {out_occ}")

    env.snk.pause = False
    n = 100
    post = [((i * 7) & UNTAGGED) | TAG for i in range(n)]
    for i, v in enumerate(post):
        env.src.send(v, last=1 if i == n - 1 else 0)
    post_sent = [(v, 1 if i == n - 1 else 0) for i, v in enumerate(post)]

    await drain(dut, env)
    env.stop()
    assert not env.credit.errors, "; ".join(env.credit.errors[:3])
    assert not env.mon_out.errors, "; ".join(env.mon_out.errors[:3])
    check_flush_boundary(env, post_sent, "soft_clear")


@cocotb.test()
async def test_long_random_stream(dut):
    """The Phase 2 DoD test: a long stream with fully randomised handshakes."""
    env = await setup(dut, seed=12)
    rng = random.Random(0xBEEF)
    n = LONG_BEATS

    for i in range(n):
        env.src.send(rng.randrange(1 << DATA_W),
                     last=1 if rng.random() < 0.05 else 0)

    # Re-roll both stall probabilities periodically so the run covers bursty,
    # sparse and saturated regimes rather than one averaged behaviour.
    async def churn():
        while True:
            env.src.stall_prob = rng.choice([0.0, 0.0, 0.1, 0.4, 0.8])
            env.snk.stall_prob = rng.choice([0.0, 0.0, 0.1, 0.4, 0.8])
            for _ in range(rng.randint(50, 500)):
                await RisingEdge(dut.clk)

    cocotb.start_soon(churn())
    await drain(dut, env, timeout_cycles=40 * n + 100000)
    env.stop()
    env.check(expect_beats=n)
