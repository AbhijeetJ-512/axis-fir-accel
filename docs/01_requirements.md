# 01 — Requirements Specification

**IP:** `axis_fir_accel` — Programmable Fixed-Point FIR Filter Accelerator
**Interfaces:** AXI4-Stream (data plane) / AXI4-Lite (control plane)
**Document status:** Phase 0 — Baseline. Approved for Phase 1 entry.
**Revision:** 0.1

---

## 1. Project Objective

Design, verify, synthesise and characterise a reusable, parameterised, runtime-programmable
finite impulse response (FIR) filter accelerator suitable for integration into an SoC as a
stream-processing offload engine.

The IP shall:

- accept a continuous stream of signed fixed-point samples over AXI4-Stream,
- convolve that stream with a software-loaded coefficient set,
- emit the filtered stream over AXI4-Stream with identical framing,
- be configured and monitored entirely through an AXI4-Lite register interface,
- scale from an area-minimal serial implementation to a throughput-maximal parallel
  implementation **through parameters alone, with no source changes**.

### 1.1 Non-Objectives

Explicitly out of scope, to bound the project:

| Excluded | Reason |
|---|---|
| Decimation / interpolation (rate change) | Adds resampling control and phase bookkeeping without adding new *arithmetic* skill; can be layered later. |
| IIR / adaptive (LMS) filtering | Feedback destroys the II=1 property and the exact-bit-match verification story. |
| Multi-channel interleaving (MUST tier) | Requires per-channel delay-line replication; deferred to NICE-TO-HAVE. |
| Complex-valued (I/Q) filtering | 4× multiplier count for no new concept. |
| Full AXI4 (burst) data path | Workload is address-less and sequential; see §4 of `02_architecture.md`. |

---

## 2. Requirement Tiers

Requirements are tagged:

- **[M]** MUST HAVE — the IP is not functional or not sign-off-able without it.
- **[S]** SHOULD HAVE — expected in the final deliverable; may be deferred one phase.
- **[N]** NICE TO HAVE — implemented only if schedule permits; never blocks sign-off.

Each requirement carries an ID of the form `FR-nn` (functional), `NFR-nn` (non-functional),
`VR-nn` (verification), `SR-nn` (synthesis), `TR-nn` (timing), `PR-nn` (power).

---

## 3. Functional Requirements

### 3.1 Core Datapath

| ID | Tier | Requirement |
|---|---|---|
| FR-01 | M | The IP shall compute `y[n] = Σ(k=0..N-1) h[k]·x[n-k]`, where `N` is the runtime-configured active tap count, `h[k]` the coefficient set, `x[n]` the input sample stream. |
| FR-02 | M | `N` shall be runtime-configurable in the range `1 ≤ N ≤ NTAPS_MAX` via the `CFG.NTAPS` field. |
| FR-03 | M | Coefficients shall be runtime-writable via AXI4-Lite while the IP is streaming, without corrupting in-flight data (see FR-10). |
| FR-04 | M | The delay line shall be zero-initialised on reset and on `CTRL.FLUSH`, such that the first `N-1` outputs after a flush correspond to a zero-padded input history. |
| FR-05 | M | Result rounding shall be selectable between **truncation**, **round-half-up**, and **convergent (round-half-to-even)** via `CFG.ROUND_MODE`. |
| FR-06 | M | The output stage shall saturate rather than wrap on overflow. Saturation mode shall be selectable between **asymmetric** (`[-2^15, 2^15-1]`) and **symmetric** (`[-(2^15-1), 2^15-1]`) via `CFG.SAT_MODE`. |
| FR-07 | S | When `CFG.SYMMETRIC = 1`, the IP shall exploit linear-phase coefficient symmetry (`h[k] = h[N-1-k]`) to halve the required multiply count, producing bit-identical results to the asymmetric path. |
| FR-08 | N | An `ACCUM_ONLY` mode shall compute a `TLAST`-delimited dot product (one output per frame) reusing the same MAC array. |
| FR-09 | N | Multi-channel operation with per-channel delay-line state, selected by `TID`. |

### 3.2 Configuration Semantics

| ID | Tier | Requirement |
|---|---|---|
| FR-10 | M | Coefficient writes shall target a **shadow bank**. The shadow bank shall be promoted to the active bank atomically, only at a frame boundary (the cycle after a `TLAST` beat is accepted on the output stream) and only after software sets `CTRL.COEFF_COMMIT`. |
| FR-11 | M | `CTRL.COEFF_COMMIT` shall be self-clearing, and `STATUS.COEFF_PENDING` shall read 1 between the commit request and the actual bank swap. |
| FR-12 | M | Writes to `CFG` while `STATUS.BUSY = 1` shall be rejected with an AXI4-Lite `SLVERR` response and shall not modify any configuration state. |
| FR-13 | M | The IP shall expose a read-only `CAPABILITY` register reporting the synthesised values of `NTAPS_MAX`, `MAC_UNITS`, `DATA_W` and `COEF_W`, so that software can discover the build configuration at runtime. |
| FR-14 | S | `CTRL.SOFT_RESET` shall return all datapath state (delay line, accumulator, pipeline, FIFOs) to its post-reset condition without disturbing the coefficient banks or `CFG`. |

### 3.3 Streaming Semantics

| ID | Tier | Requirement |
|---|---|---|
| FR-15 | M | The IP shall be **lossless**: every sample accepted on the input stream shall produce exactly one output beat. No sample shall be dropped or duplicated under any legal handshake sequence. |
| FR-16 | M | The IP shall be **order-preserving**: output beat `n` shall correspond to input beat `n`. |
| FR-17 | M | `TLAST` shall propagate from input to output with the same beat index, delayed by the pipeline latency. |
| FR-18 | M | The IP shall tolerate arbitrary, independent stalling on both stream interfaces, including indefinite stalls, without data loss, data corruption, or deadlock. |
| FR-19 | M | Once `TVALID` is asserted on the output stream, `TVALID`, `TDATA` and `TLAST` shall remain stable until `TREADY` is observed high (AXI4-Stream rule). |
| FR-20 | S | `TKEEP` shall be accepted and ignored on the input (all-ones assumed) and driven all-ones on the output, since `TDATA` is a single, always-valid sample. Deviation from all-ones on input shall be flagged as a protocol error by assertion, not by hardware. |

### 3.4 Error Handling

| ID | Tier | Requirement |
|---|---|---|
| FR-21 | M | An illegal configuration shall set `STATUS.CFG_ERR` and prevent the control FSM leaving `IDLE`. Illegal configurations are: `NTAPS = 0`; `NTAPS > NTAPS_MAX`; a reserved `ROUND_MODE` encoding. |
| FR-22 | M | `CFG_ERR` shall be sticky until cleared by writing 1 to `IRQ_STATUS.CFG_ERR` or by hard reset. |
| FR-23 | M | Output saturation shall set a sticky `STATUS.SAT_STICKY` bit and, if enabled, raise `IRQ_STATUS.SAT_EVENT`. Saturation is a *numerical* condition, not a protocol error: the stream shall continue uninterrupted. |
| FR-24 | M | Accesses to unmapped addresses in the AXI4-Lite space shall return `SLVERR` and shall not modify state. |
| FR-25 | S | A level-sensitive interrupt output `irq` shall be asserted while `(IRQ_STATUS & IRQ_ENABLE) != 0`. |

---

## 4. Numerical Requirements

Full derivation is in `05_fixed_point.md`. Summary of the binding contract:

| ID | Tier | Requirement |
|---|---|---|
| NR-01 | M | Input samples shall be signed **Q1.15** (16-bit, 15 fractional bits, range `[-1.0, +1.0)`). |
| NR-02 | M | Coefficients shall be signed **Q1.15**. |
| NR-03 | M | Products shall be exact signed **Q2.30** (32-bit). No truncation shall occur at the multiplier output. |
| NR-04 | M | The accumulator width shall be `ACC_W = DATA_W + COEF_W + ceil(log2(NTAPS_MAX))`. For the default `NTAPS_MAX = 64`, `ACC_W = 38`. |
| NR-05 | M | **The accumulator shall be provably incapable of overflow.** With NR-04, worst-case magnitude is `NTAPS_MAX · 2^30 = 2^36 < 2^37`, which is representable in a signed 38-bit value. Overflow is therefore possible only at the final output narrowing stage. |
| NR-06 | M | Output samples shall be signed **Q1.15**, obtained by rounding the Q(ACC_W-30).30 accumulator by 15 fractional bits and then saturating to 16 bits. |
| NR-07 | M | RTL results shall be **bit-exact** against an integer-arithmetic Python reference model for all tested stimulus. Tolerance-based comparison shall not be used. |

**Why Q1.15?** It is the near-universal fixed-point sample format in audio and communications
DSP: it matches the output of 12–16 bit ADCs after left-justification, it makes unity gain
representable as a coefficient set summing to `< 1.0`, and it makes the multiplier a standard
16×16 signed cell that synthesis tools and FPGA DSP slices both map efficiently. Choosing an
odd width would demonstrate nothing extra while complicating every tool interface.

---

## 5. Performance Requirements

### 5.1 Throughput

| ID | Tier | Requirement |
|---|---|---|
| PF-01 | M | The IP shall sustain an initiation interval of `II = ceil(N_eff / MAC_UNITS)` cycles per output sample, where `N_eff = ceil(N/2)` when symmetric folding is active and `N_eff = N` otherwise. |
| PF-02 | M | In the fully-parallel configuration (`MAC_UNITS ≥ N_eff`), the IP shall sustain `II = 1` — one input sample and one output sample per clock — indefinitely, given `TVALID` held high and `TREADY` held high. |
| PF-03 | M | Sustained throughput shall be independent of data values. No data-dependent stalling is permitted. |
| PF-04 | S | Backpressure recovery shall be immediate: the cycle after a stall is released, the pipeline shall resume at full rate with no bubble beyond the skid-buffer depth. |

**Application anchor.** The MUST-tier target is **≥ 100 MSPS in the fully-parallel
configuration**. Justification: this covers the intermediate-frequency and baseband filtering
band that motivates a hardware FIR in the first place — software-defined radio channelisation
(typically 10–100 MSPS), high-rate sensor front-ends, and ultrasound beamforming. Below
~10 MSPS a CPU or DSP core suffices and the accelerator is not economically justified; above
~500 MSPS the design would require a fundamentally different (multi-sample-per-cycle,
polyphase) microarchitecture that is out of scope. 100 MSPS at `II = 1` implies a **100 MHz
minimum clock**.

### 5.2 Latency

| ID | Tier | Requirement |
|---|---|---|
| PF-05 | M | End-to-end latency shall be deterministic and independent of data values. |
| PF-06 | M | Latency shall be documented exactly (in cycles, as a function of parameters) and verified by simulation. |
| PF-07 | S | Fully-parallel latency shall not exceed **10 cycles** from input beat acceptance to the corresponding output beat becoming valid. |

*Rationale for PF-07:* the estimated pipeline is input skid (1) → delay line + symmetric
pre-add (1) → multiply (1) → adder-tree reduction (1–2) → round/saturate (1) → output skid (1)
≈ 6 cycles. A 10-cycle ceiling leaves headroom for pipeline stages added during timing closure
without violating the requirement. **Actual latency: NOT YET MEASURED** — to be filled from
simulation in Phase 4.

### 5.3 Observability

| ID | Tier | Requirement |
|---|---|---|
| PF-08 | S | `SAMPLE_CNT` and `FRAME_CNT` performance counters shall be readable over AXI4-Lite, enabling software to measure achieved throughput without external instrumentation. |
| PF-09 | N | An input-FIFO occupancy high-water mark shall be readable, to support FIFO right-sizing in system integration. |

---

## 6. Clock and Reset Requirements

| ID | Tier | Requirement |
|---|---|---|
| CR-01 | M | Phase 1–6: the design shall be **single-clock**, driven by `aclk`, positive-edge triggered. |
| CR-02 | M | Reset `aresetn` shall be **active-low, asynchronously asserted, synchronously de-asserted**, per the reset synchroniser in `rtl/common/`. |
| CR-03 | M | All architectural state (delay line, accumulator, FIFO pointers, FSM, CSRs) shall be reset to a defined value. No state shall rely on power-up randomness. |
| CR-04 | M | The IP shall be functional and protocol-legal from the first clock edge after reset de-assertion. Specifically, `TREADY` on the input and `TVALID` on the output shall be deasserted throughout reset. |
| CR-05 | M | Reset assertion during active streaming is architecturally legal and shall leave the IP in a clean idle state. Data in flight is discarded — this is documented behaviour, not data loss under FR-15, which applies only to reset-free operation. |
| CR-06 | S | Phase 7: the control plane shall be moved to an independent `s_axi_aclk` domain, with a documented CDC strategy (`09_cdc_reset.md`). No unsafe synchronisers (no multi-bit buses through 2-flop synchronisers). |
| CR-07 | S | Phase 7: control-plane→datapath configuration transfer shall use a request/acknowledge handshake with the multi-bit payload held stable, not per-bit synchronisation. |
| CR-08 | N | Independent clock domains on the input and output stream interfaces via Gray-pointer asynchronous FIFOs. |

---

## 7. Parameterisation Requirements

| ID | Tier | Parameter | Default | Legal range | Requirement |
|---|---|---|---|---|---|
| PR-01 | M | `DATA_W` | 16 | 8, 16, 24, 32 | Input/output sample width. |
| PR-02 | M | `COEF_W` | 16 | 8, 16, 18 | Coefficient width. |
| PR-03 | M | `NTAPS_MAX` | 64 | 4 … 256, power of 2 | Maximum synthesised tap count. |
| PR-04 | M | `MAC_UNITS` | 8 | 1 … `NTAPS_MAX/2`, power of 2 | Physical multiply-accumulate units. **This is the primary PPA knob.** |
| PR-05 | M | `IN_FIFO_DEPTH` | 8 | 2 … 64, power of 2 | Input elastic buffer depth. |
| PR-06 | M | `OUT_FIFO_DEPTH` | 8 | 2 … 64, power of 2 | Output elastic buffer depth. |
| PR-07 | M | `AXIL_ADDR_W` | 12 | 12 … 16 | AXI4-Lite address width. |
| PR-08 | S | `SYMMETRIC_EN` | 1 | 0, 1 | Synthesise the symmetric pre-adder path. When 0, `CFG.SYMMETRIC` is tied off. |
| PR-09 | M | — | — | — | **Illegal parameter combinations shall be caught at elaboration** with `$fatal` in a generate block, not silently mis-synthesised. |
| PR-10 | M | — | — | — | Changing any parameter shall require **no source edit**. Every width shall be derived, never hard-coded. |

---

## 8. Verification Requirements

Full plan in `06_verification_plan.md`. Binding requirements:

| ID | Tier | Requirement |
|---|---|---|
| VR-01 | M | A **bit-exact Python reference model** using integer arithmetic shall be the golden reference. Floating-point comparison is prohibited. |
| VR-02 | M | All RTL output shall be automatically checked against the reference model by a scoreboard. No test shall pass on visual waveform inspection. |
| VR-03 | M | The testbench shall randomise `TVALID` and `TREADY` assertion independently, including the pathological cases of both-always-high, both-sparse, and indefinite one-sided stall. |
| VR-04 | M | The following shall each have at least one directed test: reset, idle, single transaction, continuous streaming, input backpressure, output backpressure, simultaneous stall, min/max sample values, min/max coefficient values, overflow, saturation at exact boundary, each rounding mode, mid-stream reconfiguration, illegal configuration, long random stream, pipeline bubbles, sustained throughput, reset during operation. |
| VR-05 | M | Protocol assertions for AXI4-Stream and AXI4-Lite shall be written and shall be active in every simulation. |
| VR-06 | M | Functional coverage shall be defined and collected for: tap counts, rounding modes, saturation modes, symmetric/asymmetric, handshake stall patterns, saturation events, TLAST positions, reconfiguration timing. |
| VR-07 | S | Coverage closure criteria shall be defined before sign-off. Reported coverage shall be measured, never estimated. |
| VR-08 | S | Formal proofs shall be attempted for: no-loss, no-duplication, output stability under stall, FIFO pointer safety, FSM reachability. Any property that cannot be proved shall be documented as such with the reason — never reported as proved. |
| VR-09 | M | The regression shall be runnable via a single `make` target on a clean Linux machine with no proprietary tools. |

---

## 9. Synthesis Requirements

| ID | Tier | Requirement |
|---|---|---|
| SR-01 | M | The RTL shall be synthesisable by **Yosys 0.33** with zero errors. |
| SR-02 | M | Synthesis shall produce **zero inferred latches** and **zero inferred tri-states**. |
| SR-03 | M | Synthesis shall produce no warnings about width mismatch, incomplete sensitivity, or multiply-driven nets. |
| SR-04 | M | The primary technology target shall be **Nangate45** (`Nangate45_typ.lib`). |
| SR-05 | S | The identical RTL and scripts shall additionally map to **Sky130HD** (`sky130_fd_sc_hd__tt_025C_1v80.lib`) to demonstrate library portability. |
| SR-06 | M | No vendor primitives, no `(* ... *)` vendor attributes, no black boxes in the RTL. Technology mapping shall be the tool's job. |
| SR-07 | M | The design shall contain no clock gating, no latches, and no combinational feedback loops. |
| SR-08 | M | Synthesis shall be reproducible: a single script, checked in, with the library path as the only external dependency. |

---

## 10. Timing Requirements

| ID | Tier | Requirement |
|---|---|---|
| TR-01 | M | STA shall be performed with **OpenSTA 2.7.0** against the synthesised netlist and the target library. |
| TR-02 | M | The design shall meet setup timing at **≥ 100 MHz** on Nangate45 in the default configuration (`NTAPS_MAX=64, MAC_UNITS=8`). This is the throughput-derived floor from §5.1. |
| TR-03 | S | Maximum achievable frequency shall be determined empirically by constraint sweep, not asserted. |
| TR-04 | M | There shall be **no combinational path from any input handshake signal to any output handshake signal** — specifically, `s_axis_tready` shall not combinationally depend on `m_axis_tready`. This is a skid-buffer requirement and is what makes the IP safely composable. |
| TR-05 | M | The critical path shall be identified, reported, and explained in `12_synthesis_sta.md`. |
| TR-06 | M | All timing numbers in all documents shall be traceable to a checked-in report file. Unmeasured values shall be labelled **NOT YET MEASURED**. |

**Current status of all timing figures: NOT YET MEASURED.** Phase 8/9 deliverable.

---

## 11. Area and Power Requirements

| ID | Tier | Requirement |
|---|---|---|
| SR-09 | S | Cell area shall be reported per configuration across the `MAC_UNITS` sweep. |
| SR-10 | S | Register count and combinational cell count shall be reported separately, so that the flop-vs-logic split of the folding tradeoff is visible. |
| PR-11 | S | Power shall be estimated with OpenSTA's `report_power` using the switching activity defaults, and **explicitly labelled as a static estimate, not a simulation-driven (SAIF/VCD) measurement**. |
| PR-12 | N | VCD-driven power estimation using activity from a representative streaming testcase. |

**Rationale for the soft area target:** no absolute area budget is imposed, because without a
target die and a competing IP list, any number would be invented. The *meaningful* requirement
is the **shape of the area/throughput curve** across `MAC_UNITS`, which is measurable and is the
actual engineering result. **All area and power figures: NOT YET MEASURED.**

---

## 12. Documentation and Deliverable Requirements

| ID | Tier | Requirement |
|---|---|---|
| DR-01 | M | Every architectural decision shall be recorded in `docs/engineering_log.md` with options considered, decision, reason, and tradeoff. |
| DR-02 | M | The register map shall be documented to a level sufficient for a firmware engineer to write a driver without reading RTL. |
| DR-03 | M | The README shall state exactly how to reproduce every number it reports. |
| DR-04 | M | No number shall appear in any document without either a report file reference or a **NOT YET MEASURED** label. |

---

## 13. Requirement Summary by Tier

| Tier | Count | Blocking for |
|---|---|---|
| MUST | 47 | RTL sign-off |
| SHOULD | 19 | Final release |
| NICE | 7 | Neither |

---

## 14. Open Questions

| # | Question | Owner | Resolve by |
|---|---|---|---|
| OQ-1 | Should the sample delay line be a shift register (parallel tap access, ~`NTAPS_MAX·DATA_W` flops) or a circular RAM (area-efficient, restricts parallel access)? Affects PPA sweep shape. | Design | Phase 3 |
| OQ-2 | Is `MAC_UNITS = 1` (fully serial) worth supporting given it makes the adder tree degenerate and the control FSM a plain counter? Adds a datapoint at the extreme of the PPA curve. | Design | Phase 3 |
| OQ-3 | Should coefficients be packed two-per-32-bit-word to halve configuration time? Config is one-time, so probably not worth the partial-write complexity. | Design | Phase 1 |
| OQ-4 | Will Yosys+ABC synthesis runtime for `MAC_UNITS = 32` be tractable on this machine? Determines the upper bound of the PPA sweep. | Impl | Phase 8 |

---

*Next document: `02_architecture.md`*
