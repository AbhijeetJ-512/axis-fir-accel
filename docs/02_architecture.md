# 02 — System Architecture

**IP:** `axis_fir_accel`
**Document status:** Phase 0 — Baseline. Approved for Phase 1 entry.
**Revision:** 0.1

---

## 1. System Context

The accelerator is a leaf IP on an SoC. It has no master port into system memory: a DMA engine
elsewhere in the system moves samples to and from DRAM, and this block sits in the stream path
between them. That is a deliberate partitioning choice — it keeps the IP free of address
generation, outstanding-transaction tracking, and memory-ordering concerns, which are the DMA's
job, not the filter's.

```
                +-------------+        +------------------+        +-------------+
   DRAM <-----> |   DMA (MM)  | =====> |  axis_fir_accel  | =====> |   DMA (MM)  | <---> DRAM
                |   S2MM/MM2S |  AXI4  |                  |  AXI4  |             |
                +-------------+ Stream +--------+---------+ Stream +-------------+
                                                ^
                                                | AXI4-Lite
                                                |
                +-------------------------------+--------------------------+
                |                    SoC Interconnect                       |
                +-----------------------------+-----------------------------+
                                              |
                                        +-----+------+
                                        | CPU / MCU  |
                                        +------------+
```

**Two planes, deliberately separated:**

| Plane | Interface | Traffic character | Owner |
|---|---|---|---|
| **Data plane** | AXI4-Stream in/out | High rate, continuous, sequential, address-less | DMA / stream fabric |
| **Control plane** | AXI4-Lite slave | Low rate, bursty, random-access, register-mapped | CPU |

This separation is the single most important architectural decision in the design. It is what
allows the two planes to later run on independent clocks (Phase 7, `09_cdc_reset.md`) without
restructuring the datapath, and it is the partitioning every real streaming accelerator uses.

---

## 2. Top-Level Block Diagram

```
                          AXI4-Lite (s_axil_*)
                                   |
        ===========================|=========================================
        |                          v                       CONTROL PLANE    |
        |               +--------------------+                              |
        |               |    axil_slave      |  AXI4-Lite protocol FSM      |
        |               |  (rtl/axi_lite/)   |  -> simple internal reg bus  |
        |               +---------+----------+                              |
        |                         |                                         |
        |            +------------+-------------+                           |
        |            |                          |                           |
        |            v                          v                           |
        |   +------------------+      +--------------------+                |
        |   |   csr_regfile    |      |    coeff_bank      |                |
        |   |  CTRL/CFG/STATUS |      |  shadow | active   |                |
        |   |  IRQ, counters   |      |  atomic swap       |                |
        |   +--+------------+--+      +---------+----------+                |
        |      |            ^                   |                           |
        |      | cfg        | status            | h[k] (active bank)        |
        ========|============|===================|===========================
               |            |                   |
               v            |                   |          DATA PLANE
     +---------------------------------------------------------------------+
     |                                                                     |
     |  +------------------+                                               |
     |  |    fir_ctrl      |  issue / fold schedule / TLAST tracking        |
     |  |   (FSM + credit) |<-------------------+                          |
     |  +---+----------+---+                    | pop credit               |
     |      | issue    | tap_idx                |                          |
     |      v          v                        |                          |
     |  +--------+  +---------------+  +--------+------+  +-------------+  |
  s_ |  | in     |  |  delay_line   |  |   mac_array   |  |  acc_reduce |  |
  ax ==>| fifo   |=>|  x[n-0..N-1]  |=>| MAC_UNITS x   |=>| adder tree  |  |
  is |  |        |  | + sym pre-add |  | 16x16 signed  |  | + fold acc  |  |
     |  +--------+  +---------------+  +---------------+  +------+------+  |
     |      ^                                                    |         |
     |      | tready = !full                                     v         |
     |      |                                            +---------------+ |
     |      |                                            |  round_sat    | |
     |      |                                            | rnd/sat/sticky| |
     |      |                                            +-------+-------+ |
     |      |                                                    |         |
     |      |            +--------+                              |         |
     |      |            | out    |<-----------------------------+         |
  m_ |      |            | fifo   |                                        |
  ax <===================|        |  tvalid = !empty                       |
  is |                   +---+----+                                        |
     |                       |                                             |
     |                       +---> pop event -> credit counter (fir_ctrl)  |
     +---------------------------------------------------------------------+
                                axis_fir_top (rtl/top/)
```

---

## 3. Block Responsibilities

### 3.1 `axil_slave` — AXI4-Lite Protocol Engine
*Path: `rtl/axi_lite/axil_slave.sv`*

| Aspect | Detail |
|---|---|
| **Responsibility** | Terminate the five AXI4-Lite channels; convert to a single-cycle internal register bus (`reg_addr`, `reg_wdata`, `reg_wen`, `reg_ren`, `reg_rdata`, `reg_err`). |
| **Does NOT** | Know any register semantics. It is a pure protocol adapter. |
| **Key behaviour** | Handles independent AW/W arrival order (write address may lead, lag, or coincide with write data). Issues exactly one B response per accepted write. Never asserts `AWREADY`/`WREADY` combinationally from `BREADY`. |
| **Concurrency** | One outstanding read and one outstanding write. Read and write channels operate independently. |
| **Error path** | Propagates `reg_err` from the register layer to `BRESP`/`RRESP` as `SLVERR` (`2'b10`). |

*Why a separate module:* it is the only block that needs to change when the control plane moves
to its own clock domain in Phase 7, and it is directly reusable in future IP.

### 3.2 `csr_regfile` — Control and Status Registers
*Path: `rtl/control/csr_regfile.sv`*

| Aspect | Detail |
|---|---|
| **Responsibility** | Hold `CTRL`, `CFG`, `IRQ_ENABLE`; expose `STATUS`, `ID`, `CAPABILITY`, `SAMPLE_CNT`, `FRAME_CNT`; implement RW1C on `IRQ_STATUS`; generate `irq`. |
| **Config legality** | Combinationally evaluates `cfg_illegal` (NTAPS out of range, reserved ROUND_MODE) and exports it to `fir_ctrl`. |
| **Write protection** | Rejects `CFG` writes while `BUSY` with `reg_err` (FR-12). |
| **Sticky bits** | `SAT_STICKY` and `CFG_ERR` set on event, cleared only by RW1C or hard reset. |
| **Self-clearing** | `CTRL.SOFT_RESET`, `CTRL.FLUSH`, `CTRL.COEFF_COMMIT` are pulses: written 1, read back 0 once consumed. |

### 3.3 `coeff_bank` — Dual-Bank Coefficient Storage
*Path: `rtl/mem/coeff_bank.sv`*

Two banks of `NTAPS_MAX × COEF_W`. Software writes the **shadow** bank; the datapath reads the
**active** bank. A one-bit `bank_sel` register selects which physical bank is which.

```
   AXI-Lite writes                 datapath reads
        |                                ^
        v                                |
   +---------+                     +---------+
   | bank 0  |<--- bank_sel=1 -----| mux     |
   +---------+                     |         |
   +---------+                     |         |
   | bank 1  |<--- bank_sel=0 -----|         |
   +---------+                     +---------+

   swap: bank_sel <= ~bank_sel, on (commit_pending && frame_boundary)
```

**Why a shadow bank at all?** Without it, software updating coefficient `h[3]` mid-frame would
produce an output computed from a mixture of the old and new filters — a silent numerical
corruption that no protocol checker would catch. The shadow bank makes reconfiguration atomic
with respect to frame boundaries. This is a real hazard in real IP and one of the more
interesting correctness arguments in the design.

**Frame boundary** is defined as the cycle after an output beat with `TLAST=1` is popped from
the output FIFO — i.e. after the last sample of the frame has actually left the IP.

**Structure decision:** implemented as a register array (not a RAM macro) so both banks can be
read fully in parallel by the MAC array in the fully-parallel configuration. For large
`NTAPS_MAX` this is the dominant flop cost, and is revisited in `13_ppa_optimization.md`.

### 3.4 `fir_ctrl` — Control FSM, Fold Scheduler and Credit Manager
*Path: `rtl/control/fir_ctrl.sv`*

The brain of the data plane. Three concerns, one module:

1. **Run/stop FSM** — gates streaming on `ENABLE` and configuration legality.
2. **Fold scheduler** — for `MAC_UNITS < N_eff`, sequences `P = ceil(N_eff/MAC_UNITS)` passes per
   output sample, driving `tap_base` and `acc_first` / `acc_last` strobes.
3. **Credit manager** — guarantees the output FIFO can never overflow (see §5).

```
                    +--------+
       aresetn ---->|  RST   |
                    +---+----+
                        | (reset released)
                        v
                    +--------+   cfg_illegal            +--------+
                    |  IDLE  |------------------------->| CFGERR |
                    |        |<-------------------------|        |
                    +---+----+   IRQ_STATUS.CFG_ERR W1C +--------+
                        |
       ENABLE && !cfg_illegal
                        |
                        v
                    +--------+   in_fifo empty
                    |  RUN   |----------------+
                    |        |<---------------+  (self loop, stays in RUN)
                    +---+----+
                        |
             !ENABLE  |  |  CTRL.FLUSH
                      |  |
                      v  v
                    +--------+
                    | DRAIN  |  finish in-flight samples,
                    |        |  then zero delay line
                    +---+----+
                        |  in_flight == 0
                        v
                     (IDLE)
```

**Why `DRAIN` exists:** dropping `ENABLE` while samples are mid-pipeline would violate FR-15
(losslessness). `DRAIN` lets the pipeline empty naturally, so every accepted sample still
produces its output. Stopping is graceful; only hard reset is abrupt (CR-05).

### 3.5 `axis_fifo` — Elastic Buffers
*Path: `rtl/axi_stream/axis_fifo.sv`* (instantiated twice)

Synchronous, power-of-two depth, single clock (Phase 1–6). Carries `{TDATA, TLAST}`.

The FIFOs are not an optimisation — they are **the mechanism by which the AXI4-Stream timing
requirements are met**:

- `s_axis_tready = !in_fifo_full` — depends only on input FIFO occupancy, therefore has **no
  combinational path to `m_axis_tready`** (requirement TR-04).
- `m_axis_tvalid = !out_fifo_empty`, and `m_axis_tdata` is the FIFO read-data register —
  therefore output data is **stable by construction** while `TVALID && !TREADY` (requirement
  FR-19), with no explicit hold logic.

A skid buffer (depth-2 FIFO) is the minimum that achieves this; deeper FIFOs additionally absorb
burst jitter from the upstream DMA. `IN_FIFO_DEPTH`/`OUT_FIFO_DEPTH` are parameters so the
integrator can trade area against tolerance to bursty traffic.

### 3.6 `delay_line` — Sample History
*Path: `rtl/dsp/delay_line.sv`*

Holds `x[n], x[n-1], ..., x[n-NTAPS_MAX+1]`. Implemented as a shift register so that all taps
are readable in the same cycle, which the fully-parallel configuration requires.

**Shifts once per output sample, not once per clock.** In a folded configuration the same
sample history must remain stable for all `P` passes, so the shift enable is `acc_last`, not a
free-running enable. Getting this wrong is the most likely functional bug in the folded path
and is called out explicitly in the verification plan.

**Symmetric pre-add** (when `CFG.SYMMETRIC`): the block also produces
`p[k] = x[n-k] + x[n-(N-1-k)]` for `k = 0 .. ceil(N/2)-1`, widened by one bit to `DATA_W+1` so
the sum is exact. This halves the multiplier count for linear-phase filters.

Reading taps at a runtime-variable `N` means the "far end" tap index is `N-1-k`, not
`NTAPS_MAX-1-k` — the delay line is read at a *runtime-variable offset*. This is implemented as
a mux tree over the shift register, and its depth is a candidate critical path.

### 3.7 `mac_array` — Multiply Array
*Path: `rtl/dsp/mac_array.sv`*

`MAC_UNITS` parallel signed multipliers. Each computes `(DATA_W+1) × COEF_W -> PROD_W+1`.
Purely combinational multiply followed by a pipeline register; no accumulation here.

Multipliers are written as `signed * signed` and left to the synthesis tool to map. No manual
Booth encoding, no vendor primitives (SR-06) — the whole point of the PPA experiment is to see
what the tool does with a clean description.

### 3.8 `acc_reduce` — Reduction and Fold Accumulator
*Path: `rtl/dsp/acc_reduce.sv`*

Two functions:

1. **Spatial reduction** — a balanced binary adder tree summing the `MAC_UNITS` products in
   `ceil(log2(MAC_UNITS))` levels, pipelined every two levels to keep logic depth bounded.
2. **Temporal accumulation** — for folded configurations, a register accumulating the tree
   output across `P` passes. `acc_first` clears it, `acc_last` releases the result downstream.

All arithmetic is at `ACC_W = DATA_W + COEF_W + ceil(log2(NTAPS_MAX))` bits (38 by default), the
width proven in `01_requirements.md` NR-04/NR-05 to make **overflow impossible in this block**.
This is worth stating plainly: the accumulator has no saturation logic, no overflow flag, and
needs none, because the width was derived rather than guessed.

### 3.9 `round_sat` — Output Formatting
*Path: `rtl/dsp/round_sat.sv`*

The only place in the design where information is lost, and therefore the only place overflow
can occur. Converts `Q(ACC_W-30).30` to `Q1.15`:

```
   acc[ACC_W-1:0]  (Q8.30)
         |
         v
   +-----------+   ROUND_MODE: TRUNC | HALF_UP | CONVERGENT
   |  rounder  |   drop 15 fractional bits
   +-----+-----+
         |  Q8.15, ACC_W-15 bits
         v
   +-----------+   SAT_MODE: asymmetric [-32768, 32767]
   | saturator |             symmetric  [-32767, 32767]
   +-----+-----+   -> sat_event (sticky in CSR)
         |
         v
   y[n]  Q1.15, 16 bits
```

Separating rounding from saturation into two clearly-named steps (rather than one fused
expression) is a maintainability choice: each is independently testable at its exact boundary
values, which the verification plan requires.

### 3.10 `axis_fir_top` — Integration
*Path: `rtl/top/axis_fir_top.sv`*

Structural only: parameter propagation, elaboration-time legality checks (PR-09), and wiring.
No logic. A top level that contains logic is a top level that cannot be reviewed.

---

## 4. Data Movement

Following one sample through the fully-parallel configuration:

```
 cycle | event
 ------+-------------------------------------------------------------------
   t   | s_axis_tvalid && s_axis_tready  -> sample enters in_fifo
  t+a  | fir_ctrl has credit && in_fifo non-empty -> ISSUE
       |   - sample popped, shifted into delay_line head
       |   - TLAST captured into a parallel delay pipe of depth D
  t+a+1| taps read at runtime offset; symmetric pre-adds computed
  t+a+2| MAC_UNITS multiplies
  t+a+3| adder tree level 0..1
  t+a+4| adder tree level 2..3  (depth depends on MAC_UNITS)
  t+a+5| round + saturate; sat_event -> CSR sticky
  t+a+6| result + TLAST pushed into out_fifo; credit consumed at ISSUE, not here
  t+b  | m_axis_tvalid && m_axis_tready -> beat leaves; credit returned
```

`a` is input-FIFO queueing delay (0 if the FIFO was empty), `b` is output-FIFO queueing delay.
The **deterministic** part — ISSUE to out_fifo push — is the pipeline depth `D` and is a
compile-time constant. Requirement PF-05 (data-independent latency) is met because nothing in
this path branches on data values.

`TLAST` travels in a shift register parallel to the datapath, exactly `D` deep. It is not
recomputed and not derived from a counter, so it cannot drift out of alignment with its sample.

---

## 5. Flow Control and Backpressure

This is the part of a streaming accelerator that is most often designed wrong, so the mechanism
is stated precisely.

### 5.1 The design rule

**The arithmetic pipeline never stalls.** There are no per-stage enables, no global stall net,
and no valid/ready handshaking inside the datapath. Instead, a sample is admitted into the
pipeline only when its output is *already guaranteed* a place to land.

### 5.2 Credit mechanism

```
   credits : counter, reset value = OUT_FIFO_DEPTH

   ISSUE allowed  <=>  (credits != 0) && in_fifo_notempty && (state == RUN)

   credits <= credits - 1   on ISSUE
   credits <= credits + 1   on (m_axis_tvalid && m_axis_tready)
   (both in the same cycle: credits unchanged)
```

Because the core produces exactly one output per issued sample (FR-15), and a credit is held
from ISSUE until the corresponding beat has *left* the IP, the number of samples in flight plus
the number queued in the output FIFO can never exceed `OUT_FIFO_DEPTH`. **The output FIFO
therefore cannot overflow**, independently of pipeline depth, fold factor, or downstream
behaviour. This is a counting argument, not an empirical one, and it is a prime candidate for
formal proof (`14_formal_verification.md`).

Constraint: `OUT_FIFO_DEPTH > D` (pipeline depth), otherwise the credit counter throttles the
pipeline below `II = 1` and PF-02 fails. This is checked at elaboration.

### 5.3 Backpressure scenarios

| Scenario | Behaviour |
|---|---|
| **Downstream stalls** (`m_axis_tready = 0`) | Output FIFO fills; credits exhaust; `fir_ctrl` stops issuing; input FIFO fills; `s_axis_tready` deasserts. Backpressure propagates **upstream through occupancy, never through combinational logic.** |
| **Upstream starves** (`s_axis_tvalid = 0`) | Input FIFO empties; `fir_ctrl` stops issuing; pipeline drains; `m_axis_tvalid` eventually deasserts. No spurious output. |
| **Both stall** | Design holds state indefinitely. No timeout, no watchdog, no data loss. |
| **Stall released** | Issue resumes the next cycle. Recovery is one cycle plus FIFO read latency, satisfying PF-04. |
| **Indefinite downstream stall** | Design parks. There is **no deadlock**: the only cyclic dependency would be `m_axis_tready -> ... -> s_axis_tready`, and that path is broken by the FIFOs by construction (§3.5). |

### 5.4 Why not a global stall?

The alternative — `stall = out_fifo_full` fanned out to every pipeline register's enable — is
simpler to write and is what the existing `axi_aes_engine` does. It was rejected for three
reasons: the stall net becomes a high-fanout, timing-critical signal that worsens as the
pipeline deepens; it creates a combinational cone from downstream `TREADY` toward upstream
`TREADY` unless separately buffered; and enable-gated pipelines are materially harder to prove
lossless because correctness depends on every stage seeing the same enable in the same cycle.
The credit scheme moves the entire flow-control problem into one counter that can be reasoned
about in isolation.

---

## 6. Pipeline Structure

### 6.1 Fully parallel (`MAC_UNITS >= N_eff`), `II = 1`

```
 ISSUE   TAP     MUL     ADD0    ADD1    RND     WB
   |      |       |       |       |       |       |
 [reg]->[reg]->[reg]->[reg]->[reg]->[reg]->[out_fifo]
   S0     S1      S2      S3      S4      S5

 sample n   : S0 S1 S2 S3 S4 S5
 sample n+1 :    S0 S1 S2 S3 S4 S5
 sample n+2 :       S0 S1 S2 S3 S4 S5      <- one new sample per cycle
```

### 6.2 Folded (`MAC_UNITS < N_eff`), `II = P = ceil(N_eff / MAC_UNITS)`

Example: `N_eff = 32`, `MAC_UNITS = 8`, `P = 4`.

```
 pass:      0        1        2        3        0        1   ...
 taps:    0..7     8..15   16..23   24..31    0..7     8..15
 acc:    first     accum    accum    LAST     first    accum
           |                           |
           |                           +--> result released to round_sat
           +--> delay_line HOLDS for all 4 passes, shifts on acc_last
```

The delay line shift enable and the sample-issue rate are both `1/P`. `MAC_UNITS` therefore
trades **area against throughput linearly**, which is exactly the curve the PPA experiment in
`13_ppa_optimization.md` sets out to measure.

### 6.3 Estimated resource scaling

| Configuration | Multipliers | II | Samples/s @ 100 MHz |
|---|---|---|---|
| `MAC_UNITS = 32`, sym, N=64 | 32 | 1 | 100 M |
| `MAC_UNITS = 16`, sym, N=64 | 16 | 2 | 50 M |
| `MAC_UNITS = 8`, sym, N=64 | 8 | 4 | 25 M |
| `MAC_UNITS = 4`, sym, N=64 | 4 | 8 | 12.5 M |
| `MAC_UNITS = 1`, sym, N=64 | 1 | 32 | 3.1 M |

Throughput column is arithmetic from `II`, assuming the 100 MHz floor of TR-02.
**Area, achieved Fmax and power for every row: NOT YET MEASURED** (Phase 8/9/10).

---

## 7. Reset Behaviour

| Element | Reset state | Reset by |
|---|---|---|
| Delay line | All zeros | `aresetn`, `SOFT_RESET`, `FLUSH` |
| Accumulator / pipeline registers | Zero, valid pipe cleared | `aresetn`, `SOFT_RESET` |
| Input / output FIFO pointers | Empty | `aresetn`, `SOFT_RESET` |
| Credit counter | `OUT_FIFO_DEPTH` | `aresetn`, `SOFT_RESET` |
| `fir_ctrl` state | `IDLE` | `aresetn`, `SOFT_RESET` |
| `CFG`, `IRQ_ENABLE` | Zero | `aresetn` only |
| Coefficient banks, `bank_sel` | **Undefined** (not reset) | Neither — see below |
| `s_axis_tready` | 0 | Held low throughout reset |
| `m_axis_tvalid` | 0 | Held low throughout reset |
| Sticky status bits | Cleared | `aresetn` only |

**Coefficient banks are deliberately not reset.** Resetting 64 × 16 flops costs a reset net
across the largest register array in the design for no functional benefit — software must write
coefficients before enabling the block regardless, and `CFG_ERR`/`ENABLE` gating prevents
streaming with an unconfigured filter. This is a conscious area/routing tradeoff, recorded in
the engineering log, and it is the kind of decision a synthesis engineer will ask about.

**Reset style:** asynchronous assert, synchronous de-assert, via a two-flop reset synchroniser
in `rtl/common/reset_sync.sv`. Asynchronous assertion guarantees the IP goes safe even if the
clock is not running; synchronous de-assertion prevents reset-recovery timing violations, which
are otherwise nearly impossible to close on a wide register array.

---

## 8. Error Paths

```
  illegal CFG  --> csr.cfg_illegal --> fir_ctrl blocked in IDLE
                                   --> STATUS.CFG_ERR (sticky)
                                   --> IRQ_STATUS.CFG_ERR --> irq

  CFG write while BUSY --> reg_err --> BRESP = SLVERR   (state unchanged)

  unmapped address     --> reg_err --> BRESP/RRESP = SLVERR

  output saturation    --> sat_event --> STATUS.SAT_STICKY (sticky)
                                     --> IRQ_STATUS.SAT_EVENT --> irq
                                     --> stream CONTINUES (numerical, not fatal)
```

The distinction between *protocol* errors (answered with `SLVERR`, no state change) and
*numerical* conditions (flagged, stream continues) is intentional. Halting a stream because one
sample saturated would make the IP unusable in any system with real signal levels.

---

## 9. Performance Bottleneck Analysis

Predicted, to be confirmed against measurement in Phase 9. Listed most likely first:

1. **Multiplier delay** — a 16×16 signed multiply mapped to standard cells is the deepest single
   combinational element. Expected to dominate at high `MAC_UNITS`.
2. **Adder-tree depth** — `ceil(log2(MAC_UNITS))` levels of `ACC_W`-bit addition. At
   `MAC_UNITS = 32` that is 5 levels of 38-bit carry propagation; if not pipelined it will beat
   the multiplier. Mitigation is already in the architecture (pipeline every two levels).
3. **Runtime-variable tap read** — the `N-1-k` mux tree over the delay line scales with
   `NTAPS_MAX` and is a plausible surprise critical path.
4. **Credit counter and issue logic** — small, but sits in the control path gating a wide
   datapath; worth watching for fanout.
5. **AXI4-Lite path** — expected to be nowhere near critical; if it ever appears in the top ten
   paths, something is wrong.

**All predictions above: NOT YET MEASURED.** They are recorded now specifically so that Phase 9
can compare prediction against reality — a wrong prediction, documented, is more valuable
portfolio evidence than a vague one.

---

## 10. Module Hierarchy

```
axis_fir_top
├── reset_sync              rtl/common/
├── axil_slave              rtl/axi_lite/
├── csr_regfile             rtl/control/
├── coeff_bank              rtl/mem/
├── fir_ctrl                rtl/control/
├── axis_fifo  (in)         rtl/axi_stream/
├── axis_fifo  (out)        rtl/axi_stream/
└── fir_core                rtl/dsp/
    ├── delay_line          rtl/dsp/
    ├── mac_array           rtl/dsp/
    ├── acc_reduce          rtl/dsp/
    └── round_sat           rtl/dsp/
```

Ten leaf modules, each with one responsibility, each independently testable, none larger than
about 150 lines. That is a deliberate target: modules that fit on one screen get reviewed
properly, and modules with one responsibility can be formally verified in isolation.

---

## 11. Phase 0 Design Review

Answering the questions from section 24 of the project brief, at architecture level.

| Question | Answer |
|---|---|
| **What could fail?** | Folded-mode delay-line shift timing (shift must occur on `acc_last`, not every cycle). Runtime-variable `N` tap indexing at the symmetric far end. Coefficient bank swap racing a frame boundary. |
| **What assumptions are made?** | Single clock (Phase 1–6). Coefficients are written before `ENABLE`. Input `TKEEP` is all-ones. One sample per beat — no packing. |
| **Where is the critical path?** | Predicted multiplier → adder tree. Unmeasured; see §9. |
| **Where can data be lost?** | Only at reset during streaming (documented, CR-05) and at the credit counter if its accounting is wrong. Both are explicitly targeted by assertions and formal. |
| **Where can backpressure deadlock?** | Nowhere by construction: the only candidate cycle `m_axis_tready → s_axis_tready` is broken by the FIFOs. To be proved formally, not assumed. |
| **What happens during reset?** | §7. Both handshakes held inactive; all datapath state defined; coefficients deliberately preserved. |
| **Malformed configuration?** | `CFG_ERR`, FSM held in `IDLE`, stream never starts. `SLVERR` on writes during `BUSY`. |
| **Numerical overflow?** | Impossible in the accumulator by width derivation (NR-05). Possible only in `round_sat`, where it saturates and sets a sticky flag. |
| **Maximum throughput?** | `II = 1` fully parallel. Sustained indefinitely; no data-dependent stalls. |
| **Indefinite downstream stall?** | FIFOs fill, credits exhaust, `s_axis_tready` drops, design parks cleanly and resumes in one cycle. |
| **What would a senior RTL reviewer criticise?** | (a) Register-array coefficient storage is area-expensive at large `NTAPS_MAX` — needs the PPA data to defend. (b) Not resetting the coefficient banks needs justification. (c) The runtime-variable tap mux may be an unnecessary generality. All three are acknowledged, not hidden. |
| **What would a verification engineer ask for?** | A reference model that is bit-exact, not tolerance-based (VR-01). Independent `TVALID`/`TREADY` randomisation (VR-03). Access to internal FIFO occupancy for coverage. A way to force saturation deterministically. |
| **What would a synthesis engineer complain about?** | The 38-bit adder tree if it is not pipelined (it is). The unreset coefficient array (deliberate). The runtime `N` mux depth (flagged as a watch item). |

---

## 12. Architecture Change Log

| Rev | Date | Change | Reason |
|---|---|---|---|
| 0.1 | 2026-08-17 | Baseline | Phase 0 |

---

*Next documents: `03_microarchitecture.md`, `04_interfaces.md`, `05_fixed_point.md` (Phase 0 completion).*
