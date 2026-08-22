# 03 — Microarchitecture

**IP:** `axis_fir_accel`
**Revision:** 1.0 · Phase 4
**Status:** Implemented and verified. All figures below are measured or derived, never estimated.

---

## 1. Module Hierarchy

```
axis_fir_top                        structural only -- no logic
├── reset_sync                      async assert, sync de-assert, 2 stages
├── axil_slave                      AXI4-Lite protocol engine
├── csr_regfile                     ALL address decode; CSR semantics
├── coeff_bank                      dual bank, atomic frame-boundary swap
├── axis_fifo   (in)                elastic buffer
├── credit_ctr                      admission credits -- the whole flow control
├── fir_ctrl                        FSM + fold scheduler
├── fir_core                        the datapath
│   ├── delay_line                  sample history, shift-register
│   ├── mac_array                   MAC_UNITS signed multipliers
│   ├── acc_reduce                  adder tree, one register per level
│   └── round_sat                   the only lossy stage
└── axis_fifo   (out)               elastic buffer
```

Thirteen leaf modules, none over ~200 lines, each with one responsibility.

---

## 2. The Fold Schedule

`MAC_UNITS` physical multipliers process the taps in **P passes per output sample**:

```
    N_eff = cfg_symmetric ? ceil(N/2) : N
    P     = ceil(N_eff / MAC_UNITS)              (a shift: MAC_UNITS is 2^k)
```

### 2.1 Cycle-by-cycle

```
 cycle │ event
───────┼──────────────────────────────────────────────────────────────────────
   c   │ sample_start: pop input FIFO, consume a credit,
       │               SHIFT THE DELAY LINE (once, for this sample)
  c+1  │ pass 0    tap_base = 0
  c+2  │ pass 1    tap_base = MAC_UNITS
  ...  │
  c+P  │ pass P-1  tap_base = (P-1)*MAC_UNITS   ← next sample_start may occur
       │                                          on this same cycle
```

So the initiation interval is exactly **P cycles**, and consecutive samples overlap by one cycle
at the boundary.

**The overlap is safe and the reason is worth stating.** At cycle `c+P` the delay line shifts on
the clock edge while pass `P-1` captures its operands on that same edge. A register captures the
value present *before* the edge, so pass `P-1` sees the pre-shift taps — the correct history for
the sample it is finishing.

### 2.2 The delay line shifts once per sample, not once per pass

This is **risk R3**, and it is the single most dangerous bug available in this design. A delay
line that advanced per pass would produce a filter that:

- streams correctly,
- violates no protocol,
- sets no status bit,
- passes every handshake and losslessness check,

and returns numerically wrong data. Nothing in the protocol layer can see it.

The defence is not an assertion — it is the requirement that **every `MAC_UNITS` produce results
bit-identical to a `MAC_UNITS`-independent reference model.** Since the model knows nothing about
folding, agreement can only mean the schedule is right.

**Measured:** `MAC_UNITS ∈ {1, 2, 4, 8, 16, 32}` at `NTAPS_MAX = 32`, all 7 tests pass in every
build.

### 2.3 Measured initiation interval

`NTAPS_MAX = 32`, `MAC_UNITS = 8`:

| N | symmetric | N_eff | predicted P | **measured II** |
|---|---|---|---|---|
| 32 | no | 32 | 4 | **4.027** |
| 32 | yes | 16 | 2 | **2.027** |
| 8 | no | 8 | 1 | **1.028** |
| 1 | no | 1 | 1 | **1.028** |

The residual ~0.027 is pipeline fill and drain amortised over 400 samples. Note the second row:
**symmetric mode halves the pass count**, so linear-phase filters run at twice the throughput for
the same silicon — which is the practical payoff of the pre-adder, and the reason it exists even
though it saves no accumulator bits (D-022).

---

## 3. Pipeline and Latency

```
 stage                                            registers
 ─────────────────────────────────────────────────────────
 delay line              (once per sample)             1
 operand + coefficient select                          1
 multiply                                              1
 adder tree              clog2(MAC_UNITS) levels    LEVELS
 fold accumulate         across P passes                1
 round + saturate                                       1
```

```
    LATENCY = 5 + clog2(MAC_UNITS) + (P - 1)
```

**Latency is configuration-dependent**, because `P` depends on `CFG.NTAPS` and `CFG.SYMMETRIC`,
which are runtime values. It remains *deterministic and data-independent*, which is what
requirement PF-05 actually demands — nothing in the datapath branches on a data value.

### 3.1 Why `out_valid` is not a fixed-depth shadow pipe

The unfolded Phase 3 core used a valid/last shift register of exactly the pipeline depth. That
construction cannot express a runtime-variable latency, so Phase 4 replaced it.

Instead the pass control record `{vld, first, last, tlast}` is shifted through **exactly the
datapath's own delay**, `DLY = 2 + LEVELS`, so that each pass's record emerges aligned with that
pass's adder-tree output. `out_valid` is then derived from `acc_last`, and:

- latency tracks the configuration automatically, with no arithmetic on `P`;
- any number of samples may be in flight without a side FIFO to keep in step;
- `TLAST` rides beside its own sample and cannot drift.

`fir_ctrl` captures `TLAST` at admission into `tlast_q` and holds it for the pass phase. On a
back-to-back boundary, cycle `c+P` reads `tlast_q` for the *old* sample while `sample_start`
overwrites it on the same edge — correct, again because a register reads its pre-edge value.

---

## 4. Coefficient Bank

```
   software writes ──────► SHADOW bank
   datapath reads  ◄────── ACTIVE bank

   swap when:  pending && (frame_boundary || idle)
   frame_boundary := a TLAST beat has LEFT the IP (output pop, not input accept)
```

Taking the boundary from the **output** side is what guarantees the swap cannot land inside a
frame still being computed. `idle` is also accepted so that a commit issued while the block is
stopped takes effect immediately rather than waiting for a frame that may never arrive.

**Measured:** a coefficient set committed mid-frame leaves that entire frame filtered with the
old coefficients, and the next frame uses the new ones; `STATUS.COEFF_PENDING` reports the
request and clears at the swap.

### 4.1 The X-propagation trap

Coefficient storage is deliberately never reset (D-009), and software writes only the `NTAPS`
taps it uses. So entries at and above `NTAPS` legitimately hold **X**.

`fir_core` reads `MAC_UNITS` coefficients every pass, including indices past `NTAPS` on the last
pass. Zeroing only the *operand* for those inactive taps is **not sufficient**: in Verilog
`0 * X` is `X`, not `0`. The X reaches the adder tree, and the entire output goes undefined.

Both operands of the multiply are therefore gated by `active`. This was found by test, not by
inspection: the DUT reported `-1` where the model expected `-13684`, and `-1` is what the
testbench's `_to_int` helper returns for an X — the value was undefined, not merely wrong.

---

## 5. Control FSM

```
                 ┌────────┐   ENABLE && cfg_illegal      ┌────────┐
                 │  IDLE  ├─────────────────────────────►│ CFGERR │
                 │        │◄─────────────────────────────┤        │
                 └───┬────┘        !ENABLE               └────────┘
                     │ ENABLE && !cfg_illegal
                     ▼
                 ┌────────┐
                 │  RUN   │  admit while credit && data available
                 └───┬────┘
                     │ !ENABLE || FLUSH
                     ▼
                 ┌────────┐
                 │ DRAIN  │  finish admitted samples
                 └───┬────┘
                     │ !busy && dp_count == 0
                     ▼
                  (IDLE)
```

**`DRAIN` exists so that stopping is graceful.** Dropping `ENABLE` mid-stream would otherwise
discard samples already admitted, violating the losslessness guarantee FR-15. `dp_count` tracks
samples inside the datapath; `DRAIN` completes only when it reaches zero and no pass is running.

**Measured:** with `ENABLE` dropped mid-stream, the number of beats that emerge equals
`SAMPLE_CNT` exactly, all values correct, and the FSM returns to `IDLE`.

---

## 6. Flow Control Recap

Admission requires `state == RUN && in_tvalid && have_credit && (!busy || last_pass)`. Every term
is registered state or the credit counter — nothing downstream. That is why `s_axis_tready` has
no combinational dependence on `m_axis_tready`.

**Verified structurally on `axis_fir_top`** (`scripts/check_comb_independence.sh`, self-testing):

| Property | Result |
|---|---|
| `m_axis_tready` → `s_axis_tready` (TR-04) | no combinational path |
| `s_axis_tvalid` → `m_axis_tvalid` | no feedthrough |
| `s_axis_tdata` → `m_axis_tdata` | no feedthrough |
| `m_axis_tready` → `s_axil_awready` (cross-plane) | no combinational path |
| `s_axil_bready` → `s_axil_awready` | no combinational path |
| `s_axil_rready` → `s_axil_arready` | no combinational path |

---

## 7. Behaviour in Each Condition

| Condition | Behaviour |
|---|---|
| **Normal streaming** | Admit one sample per `P` cycles; pipeline never stalls. |
| **Input starved** | `in_tvalid` low → no admission; passes finish; output drains. |
| **Output stalled** | Output FIFO fills → credits exhaust → admission stops → input FIFO fills → `s_axis_tready` drops. Backpressure propagates through occupancy only. |
| **Both stalled** | Design parks indefinitely; no timeout, no loss; resumes in one cycle. |
| **Internal stall** | Does not exist. The credit counter guarantees a landing slot before admission, so the arithmetic pipeline has no stall input at all. |
| **Reset** | Delay line zeroed, FIFO pointers cleared, FSM to `IDLE`, credits full. Coefficient arrays preserved; `bank_sel` reset to 0. |
| **`FLUSH` / `SOFT_RESET`** | Input FIFO, delay line and pipeline cleared. **Output FIFO retained** and credits reconciled — dropping `TVALID` on a beat already offered would violate AXI4-Stream (D-020). |
| **Illegal configuration** | FSM held in `CFGERR`; never streams; sticky `CFG_ERR` and interrupt. |
| **Numerical overflow** | Impossible in the accumulator by derivation. Saturates in `round_sat` with a sticky flag; the stream continues. |
| **Coefficient reload while streaming** | Writes land in the shadow bank; swap only at a frame boundary after an explicit commit. |

---

## 8. Phase 4 Design Review

| Question | Answer |
|---|---|
| **What could fail?** | The fold schedule shifting the delay line per pass (R3) — closed by bit-identity across six `MAC_UNITS` values. `tlast_q` being overwritten on the back-to-back boundary — safe by pre-edge register semantics, and covered by the `TLAST` checks. Unwritten coefficients producing X — found and fixed. |
| **What assumptions are made?** | `MAC_UNITS` is a power of two (checked at elaboration). `OUT_FIFO_DEPTH > 5 + clog2(MAC_UNITS)` (checked at elaboration). `CFG` is stable while streaming — enforced by `SLVERR` on writes while `BUSY`. |
| **Where is the critical path?** | **NOT YET MEASURED.** Phase 9. |
| **Where can data be lost?** | Only at hard reset (documented, CR-05) and at `FLUSH`, which discards by definition. `DRAIN` covers the `ENABLE`-drop case and is verified against `SAMPLE_CNT`. |
| **Where can backpressure deadlock?** | Nowhere: verified structurally on the shipping top, six properties. |
| **What would a senior RTL reviewer criticise?** | Latency varies with configuration, so a system integrator cannot assume a constant. It is documented and exact. `dp_count` duplicates information the credit counter already holds — kept separate because the credit counter also covers the output FIFO, and conflating the two would make `DRAIN` complete early. |
| **What would a verification engineer ask for?** | Bit-identity across fold factors — provided. Measured II against prediction — provided. A frame-boundary swap test that would fail if the swap were early — provided. |
