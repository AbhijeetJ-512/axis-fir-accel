# axis_fir_accel

**A programmable fixed-point FIR filter accelerator with AXI4-Stream data plane and AXI4-Lite control plane.**

> **Project status: Phase 1 of 12 complete — AXI4-Lite control plane implemented and verified.**
> The datapath does not exist yet. No performance, area, timing or power figure is claimed; every
> such figure below is labelled **NOT YET MEASURED** and will be replaced only by a value read out
> of a checked-in tool report.

```
$ make regress && make elab-sweep
** TESTS=16 PASS=16 FAIL=0 SKIP=0 **
  PASS  NTAPS_MAX=64   MAC_UNITS=8   SYM=1
  PASS  NTAPS_MAX=32   MAC_UNITS=4   SYM=1
  PASS  NTAPS_MAX=256  MAC_UNITS=1   SYM=0
  PASS  NTAPS_MAX=16   MAC_UNITS=8   SYM=1
  PASS  NTAPS_MAX=128  MAC_UNITS=16  SYM=1
elab_sweep: all configurations clean (no latches, no tri-states, no loops)
```

---

## What makes this different from a textbook FIR

Three things, and they are the reason the project exists:

1. **The accumulator cannot overflow, and that is proved rather than assumed.**
   Its width is *derived* — `ACC_W = DATA_W + COEF_W + ceil(log2(NTAPS_MAX))` — from a
   worst-case bit-growth bound, so the block carries no saturation logic and no overflow flag,
   and the entire numerical failure surface collapses to one clearly-named output stage.

2. **The arithmetic pipeline never stalls.**
   Instead of a global stall net, a sample is admitted only when its output is already guaranteed
   a slot in the output FIFO, enforced by a credit counter with a single invariant:
   `in_flight + out_fifo_occupancy ≤ OUT_FIFO_DEPTH`. Backpressure propagates through
   *occupancy*, never through combinational logic — so `s_axis_tready` has no combinational
   dependence on `m_axis_tready` and the design is safely composable with any upstream or
   downstream block.

3. **One parameter changes the microarchitecture.**
   `MAC_UNITS` folds the datapath continuously from 1 multiplier (`II = N`) to `N/2` multipliers
   (`II = 1`) with no source edit. The resulting area/frequency/throughput curve is the project's
   headline engineering result — measured across two standard-cell libraries, not asserted.

And one commitment: **every number in this README will be reproducible by a command you can run**,
using only open-source tools, on a stock Linux machine.

---

## Architecture

```
                          AXI4-Lite (control plane)
                                   |
                    +--------------v--------------+
                    |  axil_slave -> csr_regfile  |
                    |            coeff_bank       |  shadow | active
                    +--------------+--------------+
                                   |
   AXI4-S in                       v                        AXI4-S out
   ---------> [in_fifo] --> [ fir_core: delay_line -> mac_array ] --> [out_fifo] --->
                  ^                 [ -> acc_reduce -> round_sat ]         |
                  |                                                        |
                  +------------- credit counter <--------------------------+
                                (fir_ctrl FSM)
```

Full detail: [`docs/02_architecture.md`](docs/02_architecture.md).

---

## Feature summary

| Feature | Status |
|---|---|
| AXI4-Lite CSRs, byte-strobed, with `SLVERR` policy and interrupt | **Implemented, verified** |
| Runtime-discoverable `CAPABILITY` register | **Implemented, verified** |
| RW1C interrupt status with set-wins-over-clear | **Implemented, verified** |
| Coefficient region decode with out-of-range rejection | **Implemented, verified** |
| Reset synchroniser (async assert, sync de-assert) | **Implemented, verified** |
| `y[n] = Σ h[k]·x[n-k]`, runtime tap count `1 ≤ N ≤ NTAPS_MAX` | Specified — Phase 3 |
| Q1.15 samples and coefficients, exact Q2.30 products | Specified — Phase 3 |
| Derived, provably overflow-free accumulator | Specified — Phase 3 |
| Truncate / round-half-up / convergent rounding | Specified — Phase 3 |
| Symmetric and asymmetric saturation with sticky status | Specified — Phase 3 |
| Linear-phase symmetric-coefficient folding (halves multipliers) | Specified — Phase 3 |
| Credit-gated, non-stalling pipeline; independent backpressure | Specified — Phase 2/4 |
| Runtime coefficient reload via shadow bank, atomic at frame boundary | Specified — Phase 4 |
| `MAC_UNITS` resource folding parameter | Specified — Phase 4 |
| Dual-clock control/data split with proper CDC | Specified — Phase 7 |

See [`docs/00_project_plan.md`](docs/00_project_plan.md) for the phase plan and each phase's
Definition of Done.

---

## Documentation

| Document | Contents | Status |
|---|---|---|
| [`00_project_plan.md`](docs/00_project_plan.md) | Repository layout, 12 phases, risks, toolchain | Complete |
| [`01_requirements.md`](docs/01_requirements.md) | Tiered requirements (MUST/SHOULD/NICE) | Complete |
| [`02_architecture.md`](docs/02_architecture.md) | Blocks, dataflow, flow control, reset, bottlenecks | Complete |
| [`engineering_log.md`](docs/engineering_log.md) | Every decision with options and tradeoffs (D-001…D-017) | Live |
| [`04_interfaces.md`](docs/04_interfaces.md) | Signal tables, register map, driver sequence | Complete for AXI4-Lite |
| `03_microarchitecture.md` | RTL-level block detail, pipeline, corner cases | Phase 4 |
| `05_fixed_point.md` | Numerical model, derivations, equations | Phase 3 |
| `06_verification_plan.md` | Strategy, verification matrix | Phase 5 |
| `07_testbench_architecture.md` | TB components, UVM justification | Phase 5 |
| `08_assertion_plan.md` | SVA properties and intent | Phase 6 |
| `09_cdc_reset.md` | Clock/reset/CDC strategy | Phase 7 |
| `10_rtl_coding_plan.md` | Conventions, lint expectations | Phase 7 |
| `11_performance_model.md` | Latency, throughput, utilisation | Phase 10 |
| `12_synthesis_sta.md` | Critical path, slack, area | Phase 9 |
| `13_ppa_optimization.md` | Optimisation experiments, before/after | Phase 10 |
| `14_formal_verification.md` | Properties proved and *not* proved | Phase 6 |
| `15_resume_positioning.md` | Interview preparation | Phase 12 |

---

## Toolchain

Fully open source. Verified present on the development machine:

| Tool | Version | Purpose |
|---|---|---|
| Icarus Verilog | 12.0 | Simulation |
| Yosys | 0.33 | Synthesis |
| OpenSTA | 2.7.0 | Static timing analysis |
| GTKWave | — | Waveform viewing |
| Python | 3.13.9 | Reference model, PPA sweep driver |

Required before the relevant phase: `cocotb`, `numpy` (Phase 1/3), Verilator 5.x (Phase 6),
SymbiYosys + SMT solver (Phase 6).

Standard-cell libraries: **Nangate45** (primary) and **Sky130HD** (portability demonstration).

---

## Build and run

Working today:

```bash
make tools           # report which tools are present
make regress         # cocotb regression (16 tests, default parameters)
make sweep           # regression across 5 parameter sets
make elab            # Yosys structural gate: latches / tri-states / loops
make elab-sweep      # the same gate across 5 parameter sets
make verify-widths   # executable proof of the accumulator derivation
make waves           # regression with an FST dump for GTKWave
```

Requires `iverilog`, `yosys`, `python3` and `pip install cocotb`.

Planned for later phases: `make lint` (Verilator), `make synth` (Yosys → Nangate45/Sky130HD),
`make sta` (OpenSTA), `make formal` (SymbiYosys), `make ppa` (`MAC_UNITS` sweep).

---

## Results

**Verified so far** (reproduce with `make regress && make elab-sweep`):

| Result | Value | Evidence |
|---|---|---|
| Control-plane regression | 16/16 tests pass, 5 parameter sets | `make sweep` |
| Inferred latches / tri-states / combinational loops | **0**, all 5 configurations | `synthesis/reports/elab_*.log` |
| Accumulator width derivation | sufficient **and** minimal, `NTAPS_MAX` 4…256 | `make verify-widths` |
| AXI4-Lite protocol violations | **0** (monitor active in every test) | `tb/cocotb/env/axil_driver.py` |

**Not yet measured** — the datapath does not exist until Phase 3:

| Metric | Value |
|---|---|
| Maximum frequency (Nangate45) | **NOT YET MEASURED** |
| Cell area, default configuration | **NOT YET MEASURED** |
| Critical path | **NOT YET MEASURED** |
| Latency (cycles) | **NOT YET MEASURED** |
| Sustained throughput | **NOT YET MEASURED** |
| Functional coverage | **NOT YET MEASURED** |
| Formal properties proved | **NOT YET ATTEMPTED** |
| Power estimate | **NOT YET MEASURED** |

---

## License

TBD.
