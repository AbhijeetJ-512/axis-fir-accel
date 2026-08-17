"""Phase 1 regression: AXI4-Lite control plane.

Covers the Phase 1 Definition of Done from docs/00_project_plan.md:
  - every implemented register reads its specified reset value
  - CAPABILITY reflects the elaborated parameters
  - RW / RO / RW1C / W1P semantics
  - SLVERR on unmapped access and on CFG writes while BUSY
  - AW-before-W, W-before-AW and simultaneous arrival
  - byte-strobe granularity
  - coefficient region decode, including out-of-range rejection
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

from env import regmap as rm
from env.axil_driver import AxiLiteMaster, AxiLiteProtocolMonitor

# Elaborated parameters. The Makefile passes the same values to the RTL, so a
# mismatch here shows up as a CAPABILITY failure rather than silent divergence.
NTAPS_MAX = int(os.environ.get("NTAPS_MAX", "64"))
MAC_UNITS = int(os.environ.get("MAC_UNITS", "8"))
DATA_W = int(os.environ.get("DATA_W", "16"))
COEF_W = int(os.environ.get("COEF_W", "16"))
SYMMETRIC_EN = int(os.environ.get("SYMMETRIC_EN", "1"))

CLK_PERIOD_NS = 10

# Highest coefficient index the region can address, regardless of NTAPS_MAX.
COEF_REGION_SLOTS = 1 << rm.COEF_IDX_W


def cfg_expect(cfg: int) -> int:
    """CFG as the RTL reads it back, accounting for elaboration tie-offs.

    When the symmetric datapath is not synthesised, CFG.SYMMETRIC is tied to 0
    rather than reported as an error, so software reads back what it actually
    got. A test that hard-codes SYMMETRIC_EN=1 fails against a legitimate build.
    """
    if not SYMMETRIC_EN:
        cfg &= ~(1 << rm.CFG_SYM_BIT)
    return cfg


# =============================================================================
# Harness
# =============================================================================

class PulseCounter:
    """Counts the clock cycles during which `sig` is high."""

    def __init__(self, sig, clk):
        self.sig, self.clk, self.count, self._stop = sig, clk, 0, False

    def stop(self):
        self._stop = True

    async def run(self):
        while not self._stop:
            await RisingEdge(self.clk)
            await ReadOnly()
            try:
                if int(self.sig.value) == 1:
                    self.count += 1
            except ValueError:
                pass


async def setup(dut, start_monitor=True):
    """Clock, reset, quiescent inputs, driver and protocol monitor."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())

    axil = AxiLiteMaster(dut, dut.clk)
    await axil.reset()

    for name in ("sts_busy", "sts_draining", "sts_coeff_pending",
                 "sts_in_fifo_empty", "sts_out_fifo_full",
                 "evt_sample", "evt_frame_done", "evt_saturate"):
        getattr(dut, name).value = 0

    dut.arst_n.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.arst_n.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)

    mon = None
    if start_monitor:
        mon = AxiLiteProtocolMonitor(dut, dut.clk)
        cocotb.start_soon(mon.run())
    return axil, mon


def check_monitor(mon):
    if mon is not None:
        mon.stop()
        assert not mon.errors, "AXI4-Lite protocol violations: " + "; ".join(mon.errors)


async def pulse(dut, name, cycles=1):
    """Drive a TB-side event input high for `cycles` clock edges."""
    sig = getattr(dut, name)
    sig.value = 1
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    sig.value = 0
    await RisingEdge(dut.clk)


# =============================================================================
# Tests
# =============================================================================

@cocotb.test()
async def test_reset_values(dut):
    """Every implemented register reads its specified reset value."""
    axil, mon = await setup(dut)

    expected = {
        rm.REG_CTRL: 0,
        rm.REG_CFG: 0,
        rm.REG_STATUS: (1 << rm.STS_IDLE_BIT) | (1 << rm.STS_INEMPTY_BIT) * 0,
        rm.REG_IRQ_STATUS: 0,
        rm.REG_IRQ_ENABLE: 0,
        rm.REG_SAMPLE_CNT: 0,
        rm.REG_FRAME_CNT: 0,
    }
    for addr, exp in expected.items():
        data, resp = await axil.read(addr)
        assert resp == rm.RESP_OKAY, f"read 0x{addr:03X} returned resp={resp}"
        assert data == exp, f"reset value 0x{addr:03X}: got 0x{data:08X}, expected 0x{exp:08X}"

    # CFG_ERR must NOT be set out of reset: NTAPS=0 is 'unconfigured', not
    # 'faulted'. See csr_regfile cfg_err_event.
    sts, _ = await axil.read(rm.REG_STATUS)
    assert (sts >> rm.STS_CFGERR_BIT) & 1 == 0, "CFG_ERR asserted out of reset"
    assert int(dut.irq.value) == 0, "irq asserted out of reset"
    check_monitor(mon)


@cocotb.test()
async def test_id_and_capability(dut):
    """ID is the IP magic; CAPABILITY encodes the elaborated parameters."""
    axil, mon = await setup(dut)

    ident, resp = await axil.read(rm.REG_ID)
    assert resp == rm.RESP_OKAY
    assert ident == rm.IP_ID, f"ID: got 0x{ident:08X}, expected 0x{rm.IP_ID:08X}"

    cap, resp = await axil.read(rm.REG_CAPABILITY)
    assert resp == rm.RESP_OKAY
    exp = rm.expected_capability(NTAPS_MAX, MAC_UNITS, DATA_W, COEF_W, SYMMETRIC_EN)
    assert cap == exp, (
        f"CAPABILITY: got 0x{cap:08X}, expected 0x{exp:08X} "
        f"(NTAPS_MAX={NTAPS_MAX} MAC_UNITS={MAC_UNITS} "
        f"DATA_W={DATA_W} COEF_W={COEF_W} SYM={SYMMETRIC_EN})")

    # Decode it the way software would, and confirm ACC_W is the derived value.
    acc_w = (cap >> 24) & 0x7F
    assert acc_w == rm.acc_width(DATA_W, COEF_W, NTAPS_MAX), \
        f"CAPABILITY.ACC_W={acc_w} disagrees with the derivation"
    dut._log.info(f"CAPABILITY=0x{cap:08X} -> NTAPS_MAX={1 << (cap & 0xF)}, "
                  f"MAC_UNITS={1 << ((cap >> 4) & 0xF)}, ACC_W={acc_w}")
    check_monitor(mon)


@cocotb.test()
async def test_rw_registers(dut):
    """RW registers hold what software writes."""
    axil, mon = await setup(dut)

    cfg = rm.make_cfg(ntaps=33, round_mode=rm.RND_CONVERGENT,
                      sat_mode=rm.SAT_SYMMETRIC, symmetric=1)
    assert await axil.write(rm.REG_CFG, cfg) == rm.RESP_OKAY
    exp = cfg_expect(cfg)
    rb, _ = await axil.read(rm.REG_CFG)
    assert rb == exp, f"CFG readback 0x{rb:08X} != 0x{exp:08X}"

    # Configuration must also appear on the datapath-facing outputs.
    await ReadOnly()
    assert int(dut.cfg_ntaps.value) == 33
    assert int(dut.cfg_round_mode.value) == rm.RND_CONVERGENT
    assert int(dut.cfg_sat_mode.value) == rm.SAT_SYMMETRIC
    assert int(dut.cfg_symmetric.value) == SYMMETRIC_EN
    await RisingEdge(dut.clk)

    assert await axil.write(rm.REG_IRQ_ENABLE, rm.IRQ_MASK) == rm.RESP_OKAY
    rb, _ = await axil.read(rm.REG_IRQ_ENABLE)
    assert rb == rm.IRQ_MASK, f"IRQ_ENABLE readback 0x{rb:08X}"

    # CTRL.ENABLE is level; the command bits always read back 0.
    ctrl = (1 << rm.CTRL_ENABLE_BIT) | (1 << rm.CTRL_FLUSH_BIT)
    assert await axil.write(rm.REG_CTRL, ctrl) == rm.RESP_OKAY
    rb, _ = await axil.read(rm.REG_CTRL)
    assert rb == (1 << rm.CTRL_ENABLE_BIT), \
        f"CTRL readback 0x{rb:08X}: command bits must read 0"
    check_monitor(mon)


@cocotb.test()
async def test_ro_registers_ignore_writes(dut):
    """Writes to RO registers are ignored and answered OKAY (not SLVERR)."""
    axil, mon = await setup(dut)

    for addr in rm.CSR_RO:
        before, _ = await axil.read(addr)
        resp = await axil.write(addr, 0xDEADBEEF)
        assert resp == rm.RESP_OKAY, \
            f"write to RO 0x{addr:03X} returned resp={resp}, expected OKAY"
        after, _ = await axil.read(addr)
        assert after == before, \
            f"RO register 0x{addr:03X} changed: 0x{before:08X} -> 0x{after:08X}"
    check_monitor(mon)


@cocotb.test()
async def test_unmapped_addresses_slverr(dut):
    """Unmapped addresses return SLVERR on both read and write."""
    axil, mon = await setup(dut)

    unmapped = [
        0x024,                      # just past the last CSR
        0x0FC,                      # end of the CSR region, unimplemented
        0x100, 0x200, 0x3FC,        # the deliberate gap
        0x800, 0xFFC,               # above the coefficient region
    ]
    # Out-of-range coefficient slots exist only if the build does not use the
    # whole region. At NTAPS_MAX = 256 every slot is legitimately mapped.
    if NTAPS_MAX < COEF_REGION_SLOTS:
        unmapped += [rm.coef_addr(NTAPS_MAX),
                     rm.coef_addr(COEF_REGION_SLOTS - 1)]
    for addr in unmapped:
        _, rresp = await axil.read(addr)
        assert rresp == rm.RESP_SLVERR, \
            f"read 0x{addr:03X} should be SLVERR, got {rresp}"
        bresp = await axil.write(addr, 0x12345678)
        assert bresp == rm.RESP_SLVERR, \
            f"write 0x{addr:03X} should be SLVERR, got {bresp}"
    check_monitor(mon)


@cocotb.test()
async def test_cfg_write_while_busy(dut):
    """CFG writes are rejected with SLVERR while BUSY, and change nothing."""
    axil, mon = await setup(dut)

    good = rm.make_cfg(ntaps=16, round_mode=rm.RND_HALF_UP)
    assert await axil.write(rm.REG_CFG, good) == rm.RESP_OKAY

    dut.sts_busy.value = 1
    await RisingEdge(dut.clk)

    resp = await axil.write(rm.REG_CFG, rm.make_cfg(ntaps=64))
    assert resp == rm.RESP_SLVERR, f"CFG write while BUSY: expected SLVERR, got {resp}"
    rb, _ = await axil.read(rm.REG_CFG)
    assert rb == good, f"CFG changed despite SLVERR: 0x{rb:08X} != 0x{good:08X}"

    # Other registers stay writable while BUSY -- only CFG is protected.
    assert await axil.write(rm.REG_IRQ_ENABLE, 0x5) == rm.RESP_OKAY
    # Coefficients too: that is the entire point of the shadow bank (FR-03).
    assert await axil.write(rm.coef_addr(0), 0x1234) == rm.RESP_OKAY

    dut.sts_busy.value = 0
    await RisingEdge(dut.clk)
    assert await axil.write(rm.REG_CFG, rm.make_cfg(ntaps=64)) == rm.RESP_OKAY
    check_monitor(mon)


@cocotb.test()
async def test_w1c_irq_status(dut):
    """IRQ_STATUS is write-1-to-clear, and clears only the written bits."""
    axil, mon = await setup(dut)

    await pulse(dut, "evt_frame_done")
    await pulse(dut, "evt_saturate")

    sts, _ = await axil.read(rm.REG_IRQ_STATUS)
    exp = (1 << rm.IRQ_FRAME_DONE_BIT) | (1 << rm.IRQ_SAT_EVENT_BIT)
    assert sts == exp, f"IRQ_STATUS after events: 0x{sts:08X}, expected 0x{exp:08X}"

    # Clearing one bit must leave the other alone.
    await axil.write(rm.REG_IRQ_STATUS, 1 << rm.IRQ_FRAME_DONE_BIT)
    sts, _ = await axil.read(rm.REG_IRQ_STATUS)
    assert sts == (1 << rm.IRQ_SAT_EVENT_BIT), \
        f"selective W1C failed: 0x{sts:08X}"

    # Writing 0 clears nothing.
    await axil.write(rm.REG_IRQ_STATUS, 0)
    sts, _ = await axil.read(rm.REG_IRQ_STATUS)
    assert sts == (1 << rm.IRQ_SAT_EVENT_BIT), "writing 0 must not clear"

    # The sticky STATUS view clears with the same write.
    st, _ = await axil.read(rm.REG_STATUS)
    assert (st >> rm.STS_SATSTKY_BIT) & 1 == 1, "SAT_STICKY should be set"
    await axil.write(rm.REG_IRQ_STATUS, 1 << rm.IRQ_SAT_EVENT_BIT)
    st, _ = await axil.read(rm.REG_STATUS)
    assert (st >> rm.STS_SATSTKY_BIT) & 1 == 0, "SAT_STICKY should have cleared"
    check_monitor(mon)


@cocotb.test()
async def test_w1c_set_wins_over_clear(dut):
    """A hardware event coinciding with a W1C is not lost."""
    axil, mon = await setup(dut)

    # Hold the event asserted across the entire clear transaction, so the set
    # and the clear necessarily collide.
    dut.evt_saturate.value = 1
    await RisingEdge(dut.clk)
    await axil.write(rm.REG_IRQ_STATUS, 1 << rm.IRQ_SAT_EVENT_BIT)
    sts, _ = await axil.read(rm.REG_IRQ_STATUS)
    assert (sts >> rm.IRQ_SAT_EVENT_BIT) & 1 == 1, \
        "event lost: W1C won over a coincident hardware set"

    # With the event gone, the same write clears.
    dut.evt_saturate.value = 0
    await RisingEdge(dut.clk)
    await axil.write(rm.REG_IRQ_STATUS, 1 << rm.IRQ_SAT_EVENT_BIT)
    sts, _ = await axil.read(rm.REG_IRQ_STATUS)
    assert (sts >> rm.IRQ_SAT_EVENT_BIT) & 1 == 0, "W1C failed once event cleared"
    check_monitor(mon)


@cocotb.test()
async def test_command_pulses(dut):
    """SOFT_RESET / COEFF_COMMIT / FLUSH are exactly one cycle wide."""
    axil, mon = await setup(dut)

    for bit, sig in ((rm.CTRL_SOFTRST_BIT, "cmd_soft_reset"),
                     (rm.CTRL_COMMIT_BIT, "cmd_coeff_commit"),
                     (rm.CTRL_FLUSH_BIT, "cmd_flush")):
        pc = PulseCounter(getattr(dut, sig), dut.clk)
        cocotb.start_soon(pc.run())
        await axil.write(rm.REG_CTRL, 1 << bit)
        for _ in range(8):
            await RisingEdge(dut.clk)
        pc.stop()
        assert pc.count == 1, f"{sig}: {pc.count} high cycles, expected exactly 1"
        await RisingEdge(dut.clk)
    check_monitor(mon)


@cocotb.test()
async def test_byte_strobes(dut):
    """Partial writes modify only the strobed bytes."""
    axil, mon = await setup(dut)

    base = rm.make_cfg(ntaps=0x1FF, round_mode=rm.RND_CONVERGENT,
                       sat_mode=rm.SAT_SYMMETRIC, symmetric=1)
    assert await axil.write(rm.REG_CFG, base) == rm.RESP_OKAY
    base_rb = cfg_expect(base)

    # Byte 0 only: changes NTAPS[7:0], leaves NTAPS[8] and all upper fields.
    assert await axil.write(rm.REG_CFG, 0x000000AA, strb=0x1) == rm.RESP_OKAY
    rb, _ = await axil.read(rm.REG_CFG)
    exp = (base_rb & 0xFFFFFF00) | 0xAA
    assert rb == exp, f"byte-0 strobe: got 0x{rb:08X}, expected 0x{exp:08X}"

    # Byte 3 only: clears the SYMMETRIC bit, leaves everything below untouched.
    assert await axil.write(rm.REG_CFG, 0x00000000, strb=0x8) == rm.RESP_OKAY
    rb2, _ = await axil.read(rm.REG_CFG)
    exp2 = exp & 0x00FFFFFF
    assert rb2 == exp2, f"byte-3 strobe: got 0x{rb2:08X}, expected 0x{exp2:08X}"

    # Zero strobe must change nothing at all.
    assert await axil.write(rm.REG_CFG, 0xFFFFFFFF, strb=0x0) == rm.RESP_OKAY
    rb3, _ = await axil.read(rm.REG_CFG)
    assert rb3 == exp2, f"zero strobe modified CFG: 0x{rb3:08X}"
    check_monitor(mon)


@cocotb.test()
async def test_aw_w_channel_ordering(dut):
    """AW-before-W, W-before-AW and simultaneous arrival all work."""
    axil, mon = await setup(dut)

    cases = [("simultaneous", 0, 0), ("AW first", 0, 4), ("W first", 4, 0),
             ("AW far ahead", 0, 9), ("W far ahead", 9, 0)]
    for name, aw_d, w_d in cases:
        val = rm.make_cfg(ntaps=(len(name) % 64) + 1)
        resp = await axil.write(rm.REG_CFG, val, aw_delay=aw_d, w_delay=w_d)
        assert resp == rm.RESP_OKAY, f"{name}: resp={resp}"
        rb, _ = await axil.read(rm.REG_CFG)
        assert rb == val, f"{name}: readback 0x{rb:08X} != 0x{val:08X}"

    # Delayed BREADY must not corrupt anything either.
    val = rm.make_cfg(ntaps=7)
    assert await axil.write(rm.REG_CFG, val, b_delay=6) == rm.RESP_OKAY
    rb, _ = await axil.read(rm.REG_CFG, r_delay=6)
    assert rb == val
    check_monitor(mon)


@cocotb.test()
async def test_coefficient_region(dut):
    """Coefficient region decodes, round-trips, and rejects out-of-range."""
    axil, mon = await setup(dut)

    rng = random.Random(0xC0FFEE)
    written = {}
    for idx in [0, 1, 2, NTAPS_MAX // 2, NTAPS_MAX - 2, NTAPS_MAX - 1]:
        val = rng.randrange(0, 1 << COEF_W)
        written[idx] = val
        resp = await axil.write(rm.coef_addr(idx), val)
        assert resp == rm.RESP_OKAY, f"coeff[{idx}] write resp={resp}"

    for idx, val in written.items():
        rb, resp = await axil.read(rm.coef_addr(idx))
        assert resp == rm.RESP_OKAY, f"coeff[{idx}] read resp={resp}"
        assert rb == val, f"coeff[{idx}]: got 0x{rb:04X}, expected 0x{val:04X}"

    # Out-of-range indices are unmapped -- but only exist when the build uses
    # less than the whole coefficient region.
    oor = [i for i in (NTAPS_MAX, NTAPS_MAX + 1, COEF_REGION_SLOTS - 1)
           if NTAPS_MAX <= i < COEF_REGION_SLOTS]
    for idx in oor:
        _, resp = await axil.read(rm.coef_addr(idx))
        assert resp == rm.RESP_SLVERR, f"coeff[{idx}] read should be SLVERR"
    if not oor:
        dut._log.info("NTAPS_MAX fills the coefficient region; no out-of-range slots")
    check_monitor(mon)


@cocotb.test()
async def test_performance_counters(dut):
    """SAMPLE_CNT and FRAME_CNT count their events and clear on command."""
    axil, mon = await setup(dut)

    n_samples, n_frames = 17, 5
    await pulse(dut, "evt_sample", n_samples)
    await pulse(dut, "evt_frame_done", n_frames)

    sc, _ = await axil.read(rm.REG_SAMPLE_CNT)
    fc, _ = await axil.read(rm.REG_FRAME_CNT)
    assert sc == n_samples, f"SAMPLE_CNT={sc}, expected {n_samples}"
    assert fc == n_frames, f"FRAME_CNT={fc}, expected {n_frames}"

    await axil.write(rm.REG_CTRL, 1 << rm.CTRL_CNTCLR_BIT)
    for _ in range(4):
        await RisingEdge(dut.clk)
    sc, _ = await axil.read(rm.REG_SAMPLE_CNT)
    fc, _ = await axil.read(rm.REG_FRAME_CNT)
    assert sc == 0 and fc == 0, f"counters not cleared: {sc}, {fc}"
    check_monitor(mon)


@cocotb.test()
async def test_illegal_configuration(dut):
    """cfg_illegal tracks NTAPS and ROUND_MODE; CFG_ERR only fires under ENABLE."""
    axil, mon = await setup(dut)

    illegal = [
        ("NTAPS=0", rm.make_cfg(ntaps=0)),
        ("NTAPS>MAX", rm.make_cfg(ntaps=NTAPS_MAX + 1)),
        ("NTAPS=511", rm.make_cfg(ntaps=511)),
        ("reserved round", rm.make_cfg(ntaps=8, round_mode=rm.RND_RESERVED)),
    ]
    for name, cfg in illegal:
        assert await axil.write(rm.REG_CFG, cfg) == rm.RESP_OKAY, \
            f"{name}: CFG write itself must succeed; illegality is reported via status"
        await ReadOnly()
        assert int(dut.cfg_illegal.value) == 1, f"{name}: cfg_illegal not asserted"
        await RisingEdge(dut.clk)

    legal = rm.make_cfg(ntaps=NTAPS_MAX, round_mode=rm.RND_CONVERGENT)
    await axil.write(rm.REG_CFG, legal)
    await ReadOnly()
    assert int(dut.cfg_illegal.value) == 0, "legal config flagged illegal"
    await RisingEdge(dut.clk)

    # CFG_ERR is raised only when an illegally-configured block is enabled.
    await axil.write(rm.REG_IRQ_STATUS, rm.IRQ_MASK)
    await axil.write(rm.REG_CFG, rm.make_cfg(ntaps=0))
    st, _ = await axil.read(rm.REG_STATUS)
    assert (st >> rm.STS_CFGERR_BIT) & 1 == 0, \
        "CFG_ERR set by an illegal config while disabled"

    await axil.write(rm.REG_CTRL, 1 << rm.CTRL_ENABLE_BIT)
    for _ in range(3):
        await RisingEdge(dut.clk)
    st, _ = await axil.read(rm.REG_STATUS)
    assert (st >> rm.STS_CFGERR_BIT) & 1 == 1, \
        "CFG_ERR not set when enabling an illegally-configured block"
    check_monitor(mon)


@cocotb.test()
async def test_interrupt(dut):
    """irq is the OR of (IRQ_STATUS & IRQ_ENABLE)."""
    axil, mon = await setup(dut)

    await pulse(dut, "evt_frame_done")
    await ReadOnly()
    assert int(dut.irq.value) == 0, "irq asserted with IRQ_ENABLE=0"
    await RisingEdge(dut.clk)

    await axil.write(rm.REG_IRQ_ENABLE, 1 << rm.IRQ_SAT_EVENT_BIT)
    await ReadOnly()
    assert int(dut.irq.value) == 0, "irq asserted for a non-enabled source"
    await RisingEdge(dut.clk)

    await axil.write(rm.REG_IRQ_ENABLE, 1 << rm.IRQ_FRAME_DONE_BIT)
    await ReadOnly()
    assert int(dut.irq.value) == 1, "irq not asserted when source is enabled"
    await RisingEdge(dut.clk)

    await axil.write(rm.REG_IRQ_STATUS, 1 << rm.IRQ_FRAME_DONE_BIT)
    await ReadOnly()
    assert int(dut.irq.value) == 0, "irq did not deassert after W1C"
    await RisingEdge(dut.clk)
    check_monitor(mon)


@cocotb.test()
async def test_randomised_register_traffic(dut):
    """Randomised writes/reads with randomised channel delays, against a model."""
    axil, mon = await setup(dut)
    rng = random.Random(0xA5A5)

    model = {rm.REG_CFG: 0, rm.REG_IRQ_ENABLE: 0, rm.REG_CTRL: 0}
    coeffs = {}
    n_ops = 250

    for _ in range(n_ops):
        op = rng.random()

        if op < 0.35:                                   # CSR write
            addr = rng.choice([rm.REG_CFG, rm.REG_IRQ_ENABLE, rm.REG_CTRL])
            data = rng.getrandbits(32)
            strb = rng.choice([0xF, 0xF, 0x1, 0x3, 0x8, 0xC, 0x0])
            resp = await axil.write_rand(addr, data, strb, rng)
            assert resp == rm.RESP_OKAY, f"write 0x{addr:03X} resp={resp}"

            merged = 0
            for b in range(4):
                byte = (data if (strb >> b) & 1 else model[addr]) >> (b * 8)
                merged |= (byte & 0xFF) << (b * 8)
            if addr == rm.REG_CFG:
                if not SYMMETRIC_EN:
                    merged &= ~(1 << rm.CFG_SYM_BIT)
                model[addr] = merged & (rm.CFG_NTAPS_MASK | rm.CFG_ROUND_MASK
                                        | (1 << rm.CFG_SAT_BIT) | (1 << rm.CFG_SYM_BIT))
            elif addr == rm.REG_IRQ_ENABLE:
                model[addr] = merged & rm.IRQ_MASK
            else:                                        # CTRL: only ENABLE persists
                model[addr] = merged & (1 << rm.CTRL_ENABLE_BIT)

        elif op < 0.60:                                  # CSR read
            addr = rng.choice(list(model.keys()))
            data, resp = await axil.read_rand(addr, rng)
            assert resp == rm.RESP_OKAY, f"read 0x{addr:03X} resp={resp}"
            assert data == model[addr], (
                f"0x{addr:03X}: got 0x{data:08X}, model 0x{model[addr]:08X}")

        elif op < 0.85:                                  # coefficient write
            idx = rng.randrange(NTAPS_MAX)
            val = rng.randrange(1 << COEF_W)
            coeffs[idx] = val
            assert await axil.write_rand(rm.coef_addr(idx), val, 0xF, rng) == rm.RESP_OKAY

        else:                                            # coefficient read
            if coeffs:
                idx = rng.choice(list(coeffs.keys()))
                data, resp = await axil.read_rand(rm.coef_addr(idx), rng)
                assert resp == rm.RESP_OKAY
                assert data == coeffs[idx], \
                    f"coeff[{idx}]: got 0x{data:04X}, model 0x{coeffs[idx]:04X}"

    dut._log.info(f"randomised traffic: {n_ops} ops, "
                  f"{mon.writes_accepted} writes / {mon.reads_accepted} reads accepted")
    assert mon.writes_accepted == mon.responses_seen, (
        f"write/response mismatch: {mon.writes_accepted} AW accepted, "
        f"{mon.responses_seen} B responses")
    assert mon.reads_accepted == mon.read_data_seen, (
        f"read/data mismatch: {mon.reads_accepted} AR accepted, "
        f"{mon.read_data_seen} R beats")
    check_monitor(mon)
