# 04 — Interface Specification

**IP:** `axis_fir_accel`
**Revision:** 0.3 · Phase 2
**Status:** AXI4-Lite control plane and AXI4-Stream flow control **implemented and verified**.
The FIR arithmetic that occupies the pipeline arrives in Phase 3.

This document is written so that a firmware engineer can write a driver without reading RTL.

---

## 1. Port Summary

| Group | Direction | Clock | Description |
|---|---|---|---|
| `aclk`, `aresetn` | in | — | Single clock and asynchronous reset (Phase 1–6) |
| `s_axil_*` | slave | `aclk` | AXI4-Lite control plane, 32-bit data |
| `s_axis_*` | slave | `aclk` | AXI4-Stream sample input |
| `m_axis_*` | master | `aclk` | AXI4-Stream sample output |
| `irq` | out | `aclk` | Level-sensitive interrupt |

---

## 2. Clock and Reset

| Signal | Dir | Width | Description |
|---|---|---|---|
| `aclk` | in | 1 | All logic is positive-edge triggered on this clock. |
| `aresetn` | in | 1 | Active-low reset. **Asynchronously asserted, synchronously de-asserted** via `reset_sync` (2 stages). |
| `irq` | out | 1 | Asserted while `(IRQ_STATUS & IRQ_ENABLE) != 0`. Level-sensitive; cleared by W1C on `IRQ_STATUS`. |

**Reset requirements on the integrator:**

- `aresetn` must be held low for at least 2 `aclk` cycles after the clock is stable, so both
  synchroniser stages load.
- Throughout reset, `s_axil_awready`, `s_axil_wready`, `s_axil_arready`, `s_axil_bvalid`,
  `s_axil_rvalid` and `m_axis_tvalid` are held **low**. The IP is therefore protocol-legal from
  the first cycle and cannot accept or emit a transaction during reset.
- **`s_axis_tready` is high during reset**, and this is deliberate. AXI4-Stream constrains the
  *master* during reset — `TVALID` must be low — and places no requirement on `TREADY`, because a
  compliant master presents nothing for a high `TREADY` to accept. Gating `s_axis_tready` with
  reset would insert the reset net into the ready path for no benefit. See requirement CR-04 and
  engineering log D-019.
- Reset may be asserted at any time, including mid-stream. Data in flight is discarded — this is
  documented behaviour (requirement CR-05), not a violation of the losslessness guarantee, which
  applies to reset-free operation.

---

## 3. AXI4-Lite Control Interface (`s_axil_*`)

32-bit data, 12-bit address, one outstanding read and one outstanding write.

### 3.1 Signals

| Signal | Dir | Width | Description |
|---|---|---|---|
| `s_axil_awaddr` | in | 12 | Write address, byte-addressed |
| `s_axil_awprot` | in | 3 | **Accepted and ignored.** No privilege/security filtering. |
| `s_axil_awvalid` | in | 1 | Write address valid |
| `s_axil_awready` | out | 1 | Write address accepted |
| `s_axil_wdata` | in | 32 | Write data |
| `s_axil_wstrb` | in | 4 | Byte enables. **Honoured on every writable register.** |
| `s_axil_wvalid` | in | 1 | Write data valid |
| `s_axil_wready` | out | 1 | Write data accepted |
| `s_axil_bresp` | out | 2 | `OKAY` (`2'b00`) or `SLVERR` (`2'b10`) |
| `s_axil_bvalid` | out | 1 | Write response valid |
| `s_axil_bready` | in | 1 | Write response accepted |
| `s_axil_araddr` | in | 12 | Read address |
| `s_axil_arprot` | in | 3 | **Accepted and ignored.** |
| `s_axil_arvalid` | in | 1 | Read address valid |
| `s_axil_arready` | out | 1 | Read address accepted |
| `s_axil_rdata` | out | 32 | Read data |
| `s_axil_rresp` | out | 2 | `OKAY` or `SLVERR` |
| `s_axil_rvalid` | out | 1 | Read data valid |
| `s_axil_rready` | in | 1 | Read data accepted |

### 3.2 Handshake and Timing Guarantees

1. **AW and W may arrive in any order**, in either order or the same cycle. The slave captures
   whichever half arrives first and performs the register write when both are present.
   *Verified by `test_aw_w_channel_ordering`, including 9-cycle separation in both directions.*
2. **No combinational path from `BREADY` to `AWREADY`/`WREADY`, or from `RREADY` to `ARREADY`.**
   All three ready signals are functions of registered state only. This is what makes the slave
   safe to attach to any interconnect without creating a cross-channel loop.
3. **`BVALID`/`RVALID` are never withdrawn** without the corresponding `READY`, and
   `BRESP`/`RDATA`/`RRESP` are stable while the response is stalled.
   *Verified continuously by `AxiLiteProtocolMonitor` in every test.*
4. **Exactly one `B` response per accepted write**, and exactly one `R` beat per accepted read.
   *Verified by transaction counting in `test_randomised_register_traffic`.*

**Throughput.** `AWREADY` is held low while a write response is outstanding, so a write occupies
3 cycles rather than 1. This is a deliberate trade: the control plane carries one-time
configuration traffic where bandwidth is irrelevant, and the alternative — allowing a new write
to be accepted while `B` is pending — requires either a response FIFO or a combinational path
from `BREADY`, both of which cost more than they are worth here.

### 3.3 Error Response Policy

| Condition | Response | State change |
|---|---|---|
| Access to an unmapped address | `SLVERR` | None |
| Coefficient index ≥ `NTAPS_MAX` | `SLVERR` | None |
| Write to `CFG` while `STATUS.BUSY = 1` | `SLVERR` | **None** — the write is fully rejected |
| Write to a read-only register | **`OKAY`** | None — silently ignored |
| Everything else | `OKAY` | As specified per register |

**Why read-only writes return `OKAY` rather than `SLVERR`.** This follows the usual ARM-style
convention. Driver code frequently does read-modify-write over a register block, and returning
an error for a harmless write to a status register turns ordinary driver patterns into bus
faults. `SLVERR` is reserved for accesses that indicate a genuine software bug — a wrong address,
or a configuration change at an illegal time. The distinction is a contract, not an accident.

---

## 4. Register Map

Base address is assigned by the integrator. All offsets are byte offsets.

**Regions:**

| Range | Contents |
|---|---|
| `0x000` – `0x0FF` | Control and status registers |
| `0x100` – `0x3FF` | Reserved (unmapped, returns `SLVERR`) — kept free so future CSRs can be added without moving the coefficient region |
| `0x400` – `0x7FF` | Coefficient region, one coefficient per 32-bit word, up to 256 taps |
| `0x800` and above | Unmapped |

### 4.1 Register Table

| Offset | Register | Access | Reset | Description |
|---|---|---|---|---|
| `0x000` | `ID` | RO | `0x46495230` | IP identifier, ASCII `"FIR0"` |
| `0x004` | `CAPABILITY` | RO | derived | Synthesised build parameters — see §4.2 |
| `0x008` | `CTRL` | RW / W1P | `0x0` | Enable and command pulses — see §4.3 |
| `0x00C` | `CFG` | RW | `0x0` | Filter configuration — see §4.4. `SLVERR` if written while `BUSY` |
| `0x010` | `STATUS` | RO | see §4.5 | Live and sticky status |
| `0x014` | `IRQ_STATUS` | RW1C | `0x0` | Interrupt sources; write 1 to clear |
| `0x018` | `IRQ_ENABLE` | RW | `0x0` | Interrupt mask |
| `0x01C` | `SAMPLE_CNT` | RO | `0x0` | Samples accepted since last counter clear |
| `0x020` | `FRAME_CNT` | RO | `0x0` | `TLAST`-delimited frames completed |
| `0x400 + 4i` | `COEFF[i]` | RW | **undefined** | Coefficient *i*, Q1.15 in bits `[15:0]`. Valid for `i < NTAPS_MAX` |

> **`COEFF[]` is not reset.** Coefficient storage powers up undefined by design (engineering log
> D-009): resetting the largest register array in the IP buys nothing, because software must
> write coefficients before enabling the block anyway. **Software must write all `NTAPS`
> coefficients before setting `CTRL.ENABLE`.**

### 4.2 `CAPABILITY` (RO, `0x004`)

Lets software discover the synthesised build at runtime, with no device-tree entry.

| Bits | Field | Encoding |
|---|---|---|
| `[3:0]` | `LOG2_NTAPS_MAX` | `NTAPS_MAX = 1 << field` |
| `[7:4]` | `LOG2_MAC_UNITS` | `MAC_UNITS = 1 << field` |
| `[15:8]` | `DATA_W` | Sample width in bits, direct value |
| `[23:16]` | `COEF_W` | Coefficient width in bits, direct value |
| `[30:24]` | `ACC_W` | Accumulator width in bits, direct value |
| `[31]` | `SYMMETRIC_EN` | 1 if the symmetric pre-adder path is synthesised |

Measured values from the Phase 1 regression (`tb/cocotb`, all 16 tests passing):

| Build | `CAPABILITY` | Decodes to |
|---|---|---|
| `NTAPS_MAX=64, MAC_UNITS=8, SYM=1` | `0xA6101036` | 64 taps, 8 MACs, 16/16-bit, ACC_W=38 |
| `NTAPS_MAX=32, MAC_UNITS=4, SYM=1` | `0xA5101025` | 32 taps, 4 MACs, 16/16-bit, ACC_W=37 |
| `NTAPS_MAX=16, MAC_UNITS=8, SYM=1` | `0xA4101034` | 16 taps, 8 MACs, 16/16-bit, ACC_W=36 |
| `NTAPS_MAX=128, MAC_UNITS=16, SYM=1` | `0xA7101047` | 128 taps, 16 MACs, 16/16-bit, ACC_W=39 |
| `NTAPS_MAX=256, MAC_UNITS=1, SYM=0` | `0x28101008` | 256 taps, 1 MAC, 16/16-bit, ACC_W=40, no symmetric path |

Each `ACC_W` above equals `DATA_W + COEF_W + ceil(log2(NTAPS_MAX))`, as required by NR-04.

### 4.3 `CTRL` (`0x008`)

| Bits | Field | Access | Description |
|---|---|---|---|
| `[0]` | `ENABLE` | RW | Level. When 1 and configuration is legal, the datapath may leave `IDLE`. |
| `[1]` | `SOFT_RESET` | W1P | Returns datapath state to post-reset. Does **not** disturb coefficients or `CFG`. |
| `[2]` | `COEFF_COMMIT` | W1P | Requests promotion of the shadow coefficient bank at the next frame boundary. |
| `[3]` | `FLUSH` | W1P | Zeroes the sample delay line. |
| `[4]` | `CNT_CLR` | W1P | Clears `SAMPLE_CNT` and `FRAME_CNT`. |
| `[31:5]` | — | RO | Reserved, reads 0 |

**W1P (write-1-pulse)** bits generate a single-cycle internal pulse and **always read back 0**.
Writing 0 to them is a no-op. *Pulse width verified as exactly one cycle by
`test_command_pulses`.*

### 4.4 `CFG` (`0x00C`)

Writable only while `STATUS.BUSY = 0`. A write while `BUSY` returns `SLVERR` and changes nothing.

| Bits | Field | Description |
|---|---|---|
| `[8:0]` | `NTAPS` | Active tap count. Legal range `1 … NTAPS_MAX`. |
| `[15:9]` | — | Reserved, reads 0 |
| `[17:16]` | `ROUND_MODE` | `0` truncate · `1` round-half-up · `2` convergent (half-to-even) · `3` **reserved/illegal** |
| `[19:18]` | — | Reserved, reads 0 |
| `[20]` | `SAT_MODE` | `0` asymmetric `[-32768, 32767]` · `1` symmetric `[-32767, 32767]` |
| `[23:21]` | — | Reserved, reads 0 |
| `[24]` | `SYMMETRIC` | Enable linear-phase coefficient folding. **Tied to 0 if `SYMMETRIC_EN = 0`** — software reads back what it actually got, rather than receiving an error. |
| `[31:25]` | — | Reserved, reads 0 |

Fields are placed on byte boundaries where possible so that one field can be updated with a
single strobed byte write. *Byte-strobe behaviour verified by `test_byte_strobes`, including the
zero-strobe no-op case.*

### 4.5 `STATUS` (RO, `0x010`)

| Bits | Field | Type | Description |
|---|---|---|---|
| `[0]` | `IDLE` | live | Not busy and not draining |
| `[1]` | `BUSY` | live | Datapath is processing |
| `[2]` | `COEFF_PENDING` | live | A commit has been requested but the bank has not swapped yet |
| `[3]` | `CFG_ERR` | **sticky** | An illegal configuration was enabled. Cleared by W1C on `IRQ_STATUS[2]` |
| `[4]` | `SAT_STICKY` | **sticky** | The output stage saturated at least once. Cleared by W1C on `IRQ_STATUS[1]` |
| `[5]` | `IN_FIFO_EMPTY` | live | Input elastic buffer empty |
| `[6]` | `OUT_FIFO_FULL` | live | Output elastic buffer full |
| `[7]` | `DRAINING` | live | Finishing in-flight samples after `ENABLE` was dropped |
| `[31:8]` | — | — | Reserved, reads 0 |

Reset value is `IDLE = 1`, everything else 0. **`CFG_ERR` is 0 after reset** even though `NTAPS`
resets to the illegal value 0: an *unconfigured* block is not a *faulted* block. `CFG_ERR` is
raised only when software enables an illegally-configured block. *Verified by
`test_reset_values` and `test_illegal_configuration`.*

### 4.6 `IRQ_STATUS` (RW1C, `0x014`) and `IRQ_ENABLE` (RW, `0x018`)

| Bit | Source | Set when |
|---|---|---|
| `[0]` | `FRAME_DONE` | A `TLAST` output beat left the IP |
| `[1]` | `SAT_EVENT` | The output stage saturated |
| `[2]` | `CFG_ERR` | An illegally-configured block was enabled |

**Set wins over clear.** If a hardware event occurs in the same cycle as a software W1C, the
event is retained. The opposite policy silently drops interrupts under load — the classic RW1C
bug. *Verified by `test_w1c_set_wins_over_clear`, which holds the event asserted across the
entire clear transaction.*

Writing 0 to a bit never clears it. Un-strobed bytes never clear anything.

### 4.7 Software Configuration Sequence

```c
assert(rd(ID) == 0x46495230);
cap = rd(CAPABILITY);
ntaps_max = 1 << (cap & 0xF);

/* 1. Coefficients first -- they are not reset. */
for (i = 0; i < ntaps; i++)
    wr(0x400 + 4*i, coeff_q15[i] & 0xFFFF);

/* 2. Configuration, while BUSY == 0. */
wr(CFG, ntaps | (ROUND_CONVERGENT << 16) | (SAT_SYMMETRIC << 20) | (1 << 24));

/* 3. Interrupts, if wanted. */
wr(IRQ_ENABLE, (1 << 0) | (1 << 1));

/* 4. Go. */
wr(CTRL, 1 /* ENABLE */);

/* 5. Confirm the configuration was legal. */
assert((rd(STATUS) & (1 << 3)) == 0);   /* CFG_ERR */
```

To change coefficients while streaming: write the new values (they land in the shadow bank),
then set `CTRL.COEFF_COMMIT`. The swap happens at the next frame boundary; poll
`STATUS.COEFF_PENDING` to observe completion.

---

## 5. AXI4-Stream Interfaces

**Status:** the elastic buffers, flow control and stream contract are **implemented and verified**
(Phase 2). `TDATA` currently carries samples through a fixed-latency pass-through; the FIR
arithmetic that will occupy that pipeline arrives in Phase 3.

### 5.1 Input (`s_axis_*`, slave)

| Signal | Dir | Width | Description |
|---|---|---|---|
| `s_axis_tdata` | in | `DATA_W` (16) | One signed Q1.15 sample per beat |
| `s_axis_tvalid` | in | 1 | Sample valid |
| `s_axis_tready` | out | 1 | Accepted. Equals `!in_fifo_full` — **no combinational dependence on `m_axis_tready`** |
| `s_axis_tlast` | in | 1 | Last sample of a frame. Delimits frames for `FRAME_CNT` and coefficient commit |
| `s_axis_tkeep` | in | `DATA_W/8` | **Accepted and ignored**; all-ones assumed. A non-all-ones value is an assertion failure, not a hardware-handled case |

### 5.2 Output (`m_axis_*`, master)

| Signal | Dir | Width | Description |
|---|---|---|---|
| `m_axis_tdata` | out | `DATA_W` (16) | Filtered signed Q1.15 sample |
| `m_axis_tvalid` | out | 1 | Equals `!out_fifo_empty` |
| `m_axis_tready` | in | 1 | Downstream accepts |
| `m_axis_tlast` | out | 1 | `TLAST` of the corresponding input beat, delayed by the pipeline latency |
| `m_axis_tkeep` | out | `DATA_W/8` | Driven all-ones |

**`TUSER` is not implemented** on either interface. There is no per-beat sideband information to
carry: the filter is a one-in/one-out transform and every status condition is either sticky in a
CSR or raised as an interrupt. Adding `TUSER` for saturation was considered and rejected —
per-sample saturation flags would force every downstream block to handle them, whereas a sticky
status bit plus an interrupt gives software the same information at zero interface cost.

### 5.3 Stream Rules the IP Guarantees

| Rule | Guarantee | Evidence |
|---|---|---|
| No data loss | Every accepted input beat produces exactly one output beat | 100 000-beat randomised stream, exact sequence match |
| Order preserving | Output beat *n* corresponds to input beat *n* | same |
| `TLAST` alignment | `TLAST` travels beside its sample and cannot drift | `test_tlast_positions`, 14 frames of lengths 1…31 |
| Output stability | `TDATA`/`TLAST` stable while `TVALID && !TREADY` — **by construction**, they are the FIFO read data | protocol monitor, every test |
| No `TVALID` withdrawal | `TVALID` is never deasserted without a handshake | protocol monitor, every test |
| No deadlock | The candidate cycle `m_axis_tready → s_axis_tready` does not exist | `scripts/check_comb_independence.sh` |
| Output FIFO never overflows | `in_flight + occupancy ≤ OUT_FIFO_DEPTH` by credit counting | per-cycle invariant checker, every test |
| Data-independent timing | No stall depends on a data value | `II = 1.0` measured under continuous streaming |

### 5.4 Reset and Flush Semantics on the Stream Interfaces

| Event | Input FIFO | Delay line / pipeline | Output FIFO | Credits |
|---|---|---|---|---|
| `aresetn` low | cleared | cleared | cleared | full |
| `CTRL.SOFT_RESET` | cleared | cleared | **retained** | **reconciled** |
| `CTRL.FLUSH` | cleared | zeroed | **retained** | **reconciled** |

`SOFT_RESET` and `FLUSH` deliberately leave the output FIFO alone. Clearing it would drop
`m_axis_tvalid` on a beat already offered to the consumer, which the protocol forbids and which
the consumer cannot anticipate — it has no visibility of our soft reset. Already-computed
results are valid outputs of the configuration that produced them and are allowed to drain.

Because the flush discards in-flight samples, the credit counter is **reloaded** with
`OUT_FIFO_DEPTH - (occupancy + push - pop)` rather than reset to full, which restores the
invariant exactly and prevents the credit leak that a plain reset would cause. See
`docs/02_architecture.md` §7.1 and engineering log D-020.

**Software implication:** after a `SOFT_RESET` the IP may still emit a bounded number of beats
(at most `OUT_FIFO_DEPTH`) belonging to the pre-reset stream. A driver that must discard them
should count them out, or reset the downstream consumer as well.

---

## 6. Verification Status

| Interface | Status | Evidence |
|---|---|---|
| AXI4-Lite | **Verified** | 16/16 cocotb tests across 5 parameter sets; protocol monitor active in every test |
| Reset synchroniser | **Verified** | Exercised by every test's reset sequence |
| Coefficient region decode | **Verified** | `test_coefficient_region`, incl. out-of-range rejection |
| AXI4-Stream in/out | **Verified** | 12/12 cocotb tests across 5 FIFO/pipe configurations; 100 000-beat exact match |
| Credit invariant | **Verified** | Per-cycle checker: output FIFO never written while full |
| TR-04 (no comb path) | **Verified structurally** | `scripts/check_comb_independence.sh`, with self-test |
| Latch-free elaboration | **Verified** | `scripts/elab_sweep.sh` — 5 configs, zero latches/tri-states/loops |
| FIR arithmetic | **NOT YET IMPLEMENTED** | Phase 3 |

Reproduce with:

```bash
make check     # both suites + latch sweep + comb-independence + width derivation
```

---

## 7. Change Log

| Rev | Date | Change |
|---|---|---|
| 0.1 | 2026-08-17 | Register map drafted (Phase 0) |
| 0.2 | 2026-08-17 | AXI4-Lite implemented and verified; measured CAPABILITY values added; error policy fixed |
| 0.3 | 2026-08-17 | AXI4-Stream flow control implemented and verified; reset/flush semantics specified (D-020); CR-04 corrected (D-019) |
