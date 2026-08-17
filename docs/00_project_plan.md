# 00 — Project Plan and Phase Definitions

**IP:** `axis_fir_accel`
**Revision:** 0.1 · 2026-08-17

---

## 1. Repository Structure

```
axis_fir_accel/
├── rtl/
│   ├── top/            axis_fir_top.sv          integration only, no logic
│   ├── axi_lite/       axil_slave.sv            AXI4-Lite protocol engine
│   ├── axi_stream/     axis_fifo.sv             elastic buffers / skid
│   ├── dsp/            fir_core, delay_line, mac_array, acc_reduce, round_sat
│   ├── mem/            coeff_bank.sv            dual-bank coefficient store
│   ├── control/        fir_ctrl.sv, csr_regfile.sv
│   └── common/         fir_pkg.sv, reset_sync.sv
│
├── tb/
│   ├── cocotb/
│   │   ├── model/      Python bit-exact reference model
│   │   ├── env/        AXI4-Stream + AXI4-Lite drivers, monitors, scoreboard, coverage
│   │   └── tests/      one file per test scenario
│   └── sv/             Verilator-only SystemVerilog wrappers for SVA
│
├── assertions/         SVA property files + bind file (Verilator / formal)
├── formal/             SymbiYosys .sby scripts and formal-only wrappers
├── constraints/        SDC per configuration
├── scripts/            build glue, PPA sweep driver, report parsers
├── sim/                simulation build artefacts (gitignored)
├── synthesis/
│   ├── scripts/        Yosys scripts (library path as only external dependency)
│   └── reports/        checked-in synthesis reports
├── sta/
│   ├── scripts/        OpenSTA scripts
│   └── reports/        checked-in timing reports
├── results/ppa/        the measured PPA sweep data + generated tables
├── docs/               this documentation set
└── README.md
```

**Why this structure.** Three principles.

1. **RTL is grouped by architectural role, not by file type.** `rtl/axi_lite/` and
   `rtl/axi_stream/` hold protocol adapters that are reusable in unrelated IP; `rtl/dsp/` holds
   the algorithm; `rtl/control/` holds policy. A reviewer looking for "how does backpressure
   work" goes to one directory, not through a flat list.
2. **Every tool that produces numbers has a `scripts/` and a `reports/` directory, and the
   reports are checked in.** This is what makes the claims auditable — the README can cite a
   file path for every figure.
3. **Verification is separated by what can execute it.** `tb/cocotb/` runs on Icarus today;
   `tb/sv/` and `assertions/` need Verilator; `formal/` needs SymbiYosys. Keeping them apart
   means a reviewer with only Icarus still gets a working regression.

---

## 2. Phase Definitions

Each phase has an explicit **Definition of Done**. A phase is not complete because code exists;
it is complete when its acceptance criteria have been *executed* and the results recorded.

---

### PHASE 0 — Requirements and Architecture

| | |
|---|---|
| **Objective** | Fix the algorithm, the interfaces, the numerical contract and the architecture before any RTL is written. |
| **Deliverables** | `00_project_plan.md`, `01_requirements.md`, `02_architecture.md`, `engineering_log.md`, repository skeleton, `README.md` stub |
| **Verification** | Design review against brief section 24 (recorded in `02_architecture.md` §11) |
| **Definition of Done** | Algorithm selected with written justification against alternatives; every requirement tiered MUST/SHOULD/NICE; architecture diagram with named blocks and stated responsibilities; flow-control mechanism specified precisely enough to implement; every decision logged with tradeoffs; no un-justified numeric constants |

---

### PHASE 1 — AXI4-Lite Control Plane

| | |
|---|---|
| **Objective** | A working, protocol-legal register interface, verified standalone before any datapath exists. |
| **Files** | `rtl/common/fir_pkg.sv`, `rtl/common/reset_sync.sv`, `rtl/axi_lite/axil_slave.sv`, `rtl/control/csr_regfile.sv`, `docs/04_interfaces.md`, `tb/cocotb/env/axil_driver.py`, `tb/cocotb/tests/test_csr.py` |
| **Work** | Five-channel AXI4-Lite slave FSM; internal register bus; full register map; RW1C, sticky and self-clearing bits; `SLVERR` on unmapped access and on `CFG` write while `BUSY`; `CAPABILITY` register reporting synthesised parameters |
| **Tests** | Read/write every implemented register; read-only registers reject writes; RW1C semantics; unmapped address returns `SLVERR`; AW-before-W, W-before-AW, and same-cycle arrival; back-to-back writes; reset values match specification |
| **Done when** | All register tests pass under cocotb+Icarus; `CAPABILITY` correctly reflects the elaborated parameters for at least two different parameter sets; Yosys elaborates the two modules with zero latches |

---

### PHASE 2 — AXI4-Stream Path and Elastic Buffers

| | |
|---|---|
| **Objective** | Prove losslessness and protocol legality on a pass-through path before adding arithmetic. |
| **Files** | `rtl/axi_stream/axis_fifo.sv`, a temporary loopback top, `tb/cocotb/env/axis_driver.py`, `axis_monitor.py`, `tb/cocotb/tests/test_stream_passthrough.py` |
| **Work** | Synchronous FIFO with `{TDATA, TLAST}` payload; `tready`/`tvalid` derived from occupancy only; verify no combinational path between the two `TREADY`s |
| **Tests** | Single beat; continuous stream; input backpressure; output backpressure; simultaneous stall; indefinite one-sided stall; randomised independent `TVALID`/`TREADY`; `TLAST` position preservation; reset mid-stream |
| **Done when** | Byte-for-byte input/output match over ≥ 100 k randomised beats with randomised handshakes; zero protocol violations; combinational independence of `s_axis_tready` from `m_axis_tready` confirmed by inspection of the synthesised netlist, not by assertion alone |

---

### PHASE 3 — DSP Datapath (unfolded)

| | |
|---|---|
| **Objective** | Bit-exact FIR arithmetic, fully parallel, no folding yet. |
| **Files** | `rtl/dsp/delay_line.sv`, `mac_array.sv`, `acc_reduce.sv`, `round_sat.sv`, `fir_core.sv`, `docs/05_fixed_point.md`, `tb/cocotb/model/fir_ref.py`, `tb/cocotb/tests/test_fir_math.py` |
| **Work** | Q1.15 datapath; derived `ACC_W`; three rounding modes; two saturation modes; symmetric pre-add path |
| **Tests** | Impulse response equals coefficients; step response; boxcar (all-equal coefficients) against closed form; all-min and all-max operands; exact rounding-boundary values for each mode; saturation at exact boundary in both modes; symmetric vs asymmetric bit-identity; random streams vs Python model |
| **Done when** | Bit-exact match against the Python reference on ≥ 1 M random samples across ≥ 20 random coefficient sets and every rounding/saturation mode combination; zero mismatches; accumulator no-overflow argument confirmed by an assertion that never fires |

---

### PHASE 4 — Folding, Control FSM and Integration

| | |
|---|---|
| **Objective** | The complete IP: folded datapath, credit-based flow control, full control plane wired to full data plane. |
| **Files** | `rtl/control/fir_ctrl.sv`, `rtl/mem/coeff_bank.sv`, `rtl/top/axis_fir_top.sv`, `docs/03_microarchitecture.md` |
| **Work** | Fold scheduler; credit counter; `IDLE/RUN/DRAIN/CFGERR` FSM; shadow-bank atomic swap; delay-line shift at `1/P` rate |
| **Tests** | Every `MAC_UNITS` in {1, 2, 4, 8, 16, 32} produces bit-identical results to the unfolded reference; measured `II` matches `ceil(N_eff/MAC_UNITS)`; coefficient swap takes effect exactly at the frame boundary and never mid-frame; `DRAIN` loses no samples; illegal configuration blocks `RUN`; sustained-throughput test achieves the predicted rate |
| **Done when** | All of the above pass; **measured** latency and initiation interval recorded in `11_performance_model.md`; every sample accounted for across a 10 M-sample randomised regression |

---

### PHASE 5 — Verification Environment Completion

| | |
|---|---|
| **Objective** | Turn the growing test collection into a real regression with coverage. |
| **Files** | `docs/06_verification_plan.md`, `docs/07_testbench_architecture.md`, `tb/cocotb/env/scoreboard.py`, `coverage.py`, full `tb/cocotb/tests/` |
| **Work** | Scoreboard with end-of-test drain checking; functional coverage model; constrained-random configuration generation; regression runner |
| **Tests** | All 20 scenarios listed in brief section 17 |
| **Done when** | Single `make regress` runs the full suite on a clean machine; **measured** functional coverage reported with the closure criteria stated in advance; no test passes by absence of checking |

---

### PHASE 6 — Assertions and Formal

| | |
|---|---|
| **Objective** | Static proof of the properties that simulation can only sample. |
| **Files** | `docs/08_assertion_plan.md`, `docs/14_formal_verification.md`, `assertions/*.sv`, `formal/*.sby` |
| **Work** | Install Verilator 5.x and SymbiYosys + solver; AXI4-Stream and AXI4-Lite protocol SVA; FIFO safety; FSM reachability; credit-counter invariant |
| **Proof targets** | No data loss; no duplication; output stability under stall; output FIFO never overflows (credit invariant); no unreachable/deadlock FSM state |
| **Done when** | Each property is either **proved** (with the solver output checked in) or **explicitly documented as not proved, with the reason**. No property is reported as proved without a bounded-depth or unbounded result to cite |

---

### PHASE 7 — CDC, Reset and Lint

| | |
|---|---|
| **Objective** | Move the control plane to its own clock; pass strict lint. |
| **Files** | `docs/09_cdc_reset.md`, `docs/10_rtl_coding_plan.md`, `rtl/common/cdc_*.sv` |
| **Work** | Two-flop synchronisers; request/acknowledge configuration transfer with stable multi-bit payload; Gray-pointer asynchronous FIFO if stream-domain separation is added; Verilator `-Wall` clean |
| **Tests** | Randomised clock ratios across the control/data boundary; configuration transfer integrity under adverse ratios; reset assertion in each domain independently |
| **Done when** | Verilator lint clean at `-Wall`; no multi-bit bus crosses a domain without a handshake; CDC boundaries documented with a diagram; regression from Phase 5 re-passes with randomised clock ratios |

---

### PHASE 8 — Synthesis

| | |
|---|---|
| **Objective** | Real gate-level results on a real library. |
| **Files** | `synthesis/scripts/synth.ys`, `synthesis/reports/`, `docs/12_synthesis_sta.md` |
| **Work** | Yosys flow to Nangate45; zero latches; area and cell reports; repeat on Sky130HD |
| **Done when** | Synthesis completes with zero errors and zero latch inferences; reports checked in; **measured** area and cell counts recorded; resolves open question OQ-4 (synthesis runtime at high `MAC_UNITS`) |

---

### PHASE 9 — Static Timing Analysis

| | |
|---|---|
| **Objective** | Know the critical path, not guess it. |
| **Files** | `constraints/*.sdc`, `sta/scripts/sta.tcl`, `sta/reports/`, `docs/12_synthesis_sta.md` |
| **Work** | SDC with clock, I/O delays and false paths; OpenSTA setup/hold; Fmax by constraint sweep |
| **Done when** | Worst slack, critical path and logic depth reported from checked-in report files; the §9 architecture prediction compared against measurement and the discrepancy explained; TR-02 (≥ 100 MHz) either met or the gap quantified |

---

### PHASE 10 — PPA Optimisation

| | |
|---|---|
| **Objective** | Treat optimisation as measured experiment. |
| **Files** | `scripts/ppa_sweep.py`, `results/ppa/`, `docs/13_ppa_optimization.md`, `docs/11_performance_model.md` |
| **Work** | Sweep `MAC_UNITS` × library; then at least three targeted RTL optimisations driven by the Phase 9 critical path |
| **Done when** | Each optimisation documented as *original → problem → hypothesis → change → measured result → tradeoff*, with before/after numbers from checked-in reports. Optimisations that **failed** are documented too — a hypothesis that did not hold is a legitimate and valuable result |

---

### PHASE 11 — Final Integration and Regression

| | |
|---|---|
| **Objective** | Everything green simultaneously, from a clean clone. |
| **Done when** | Fresh clone → `make regress`, `make synth`, `make sta`, `make formal` all succeed on a machine with only the open-source toolchain; no stale reports; no requirement left un-traced |

---

### PHASE 12 — Documentation and Portfolio Packaging

| | |
|---|---|
| **Objective** | Make the work legible to a reviewer in ten minutes. |
| **Files** | `README.md`, `docs/15_resume_positioning.md`, waveform captures |
| **Done when** | Every number in the README cites a report file or is labelled NOT YET MEASURED; build/simulate/synthesise instructions verified by following them literally on a clean checkout |

---

## 3. Cross-Phase Rules

1. **No number without provenance.** Any figure in any document either cites a checked-in report
   file or carries the label **NOT YET MEASURED**. This rule is not negotiable and applies
   retroactively to earlier documents.
2. **No phase closes on inspection.** Acceptance criteria are executed, and their output is
   recorded.
3. **Architecture changes are documented before they are implemented.** If a better structure is
   found mid-project, the sequence is: log the reason → update `02_architecture.md` → update
   affected documents → then change RTL.
4. **The engineering log is updated in the phase the decision is made**, not reconstructed later.
5. **Failed experiments are kept.** A documented failed optimisation is stronger evidence of
   engineering judgement than a suspiciously clean success record.

---

## 4. Risks and Technical Challenges

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | Yosys+ABC synthesis runtime explodes at `MAC_UNITS = 32` (32 × 16×16 multipliers) | PPA sweep truncated | **High** | Bring the flow up at `MAC_UNITS = 1`; scale upward recording runtime at each step; publish the runtime data as a result rather than hiding a truncated sweep (OQ-4) |
| R2 | SymbiYosys and an SMT solver fail to install (none present; `yosys-smtbmc` is available but unproven here) | Formal phase blocked | Medium | Attempt install early in Phase 6, not at the end. If it fails, `14_formal_verification.md` states exactly which properties were *not* proved and gives the exact commands to reproduce the attempt |
| R3 | Folded-mode delay-line shift timing bug (shift on `acc_last`, not every cycle) | Silent wrong results | **High** | Directed test comparing every `MAC_UNITS` value against the unfolded golden path for bit-identity — this is the single highest-value test in the project |
| R4 | Runtime-variable `N` tap indexing produces an unexpected critical path | Fmax miss | Medium | Predicted in advance (`02_architecture.md` §9); if confirmed, restricting `N` to a small legal set is a documented fallback |
| R5 | Coefficient shadow-bank swap races the frame boundary | Rare numerical corruption | Medium | Swap condition defined off the *output* FIFO pop of a `TLAST` beat, not the input; targeted directed test plus an assertion that no swap occurs mid-frame |
| R6 | Verilator's SVA subset does not cover the intended properties | Assertion phase weakened | Medium | Write properties in the common subset; anything unsupported moves to formal or to a cocotb checker, and is documented as such |
| R7 | Scope creep from NICE-TO-HAVE features | Project unfinished | Medium | NICE tier never blocks sign-off; Phase 11 gate closes the feature set |
| R8 | Two AXI accelerators on one profile read as duplicates | Portfolio value diluted | Medium | README opens with architectural differentiation, not algorithm description (D-002) |

---

## 5. Toolchain — Verified State

Checked on this machine, 2026-08-17:

| Tool | Status | Version | Use |
|---|---|---|---|
| Icarus Verilog | **installed** | 12.0 | Primary simulator |
| Yosys | **installed** | 0.33 | Synthesis |
| OpenSTA | **installed** | 2.7.0 | Timing analysis |
| GTKWave | **installed** | — | Waveform viewing |
| Python | **installed** | 3.13.9 | Reference model, sweep driver |
| Nangate45 Liberty | **present** | — | Primary target |
| Sky130HD Liberty | **present** | — | Secondary target |
| cocotb | **not installed** | — | Phase 1 prerequisite (`pip install cocotb`) |
| numpy | **not installed** | — | Phase 3 prerequisite (`pip install numpy`) |
| Verilator | **not installed** | — | Phase 6 prerequisite |
| SymbiYosys + solver | **not installed** | — | Phase 6 prerequisite (risk R2) |

Notable constraint: **Icarus 12 supports no classes, no `randomize()`, no covergroups and no
concurrent SVA.** This is the reason the verification environment is cocotb-based rather than
UVM-based (D-003).

---

*See `engineering_log.md` for the reasoning behind each decision referenced above.*
