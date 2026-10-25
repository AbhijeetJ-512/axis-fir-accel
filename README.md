# axis_fir_accel

A programmable fixed-point FIR filter accelerator with an AXI4-Stream data path and an AXI4-Lite control interface.

**Where it stands.** The original project got to the end of Phase 4 of 12: the IP is functionally complete and verified end to end.
It never reached its later phases (verification closure, formal, CDC, synthesis, STA and PPA). This portfolio copy (2026-10-21)
picks up part of that:

- a `MAC_UNITS` sweep through Yosys and OpenSTA on Sky130 HD, pre-layout (`ppa/`, `results/ppa/ppa_sweep.md`, `make ppa-sweep`);
- in revision 3 (2026-10-21), bound SVA checkers in `verif/fir_checkers.sv` and `verif/fir_binds.sv`, 105 assertions and 81 cover
  points, that run inside the existing cocotb suites under Verilator 5.052 (`make sva`), and a 95-mutant injected-bug campaign that shows
  each assertion can fail (`make sva-mutants`).

That work found a real bug in the RTL, **a flush that later loses output beats**. It's fixed in `rtl/dsp/fir_core.sv`, and the file as I
found it is kept in `verif/as_found/`. It also showed some gaps in the tests, which I closed with 10 more directed tests. Since the fix
changed the netlist slightly, I re-ran the PPA sweep; the earlier table is kept in `results/ppa/ppa_sweep_before_flush_fix.md`. Apart
from that one RTL file and the tests, the project is as it was, and the original is in `Projects/axis_fir_accel`.

I don't quote an area, timing or power figure unless a checked-in tool report produced it. Anything else is marked NOT YET MEASURED.

```
$ make check
=== Phase 1 suite: AXI4-Lite control plane ===
** TESTS=18 PASS=18 FAIL=0 SKIP=0 **
=== Phase 2 suite: AXI4-Stream path and flow control ===
100000 beats matched exactly | peak out_fifo=8/8 | 170957 cycles
** TESTS=12 PASS=12 FAIL=0 SKIP=0 **
elab_sweep: all configurations clean (no latches, no tri-states, no loops)
self-test: direct-wire module = FOUND | through-a-flop module = clean
  PASS  m_axis_tready -> s_axis_tready  (TR-04)     no combinational path
RESULT: accumulator width derivation holds
```

---

## What's different from a textbook FIR

Three things, and they're the reason the project exists.

1. **The accumulator can't overflow, and there's a proof for it.** Its width is derived from a worst-case bit-growth bound,
   `ACC_W = DATA_W + COEF_W + ceil(log2(NTAPS_MAX))`, so the block needs no saturation logic and no overflow flag in the accumulator.
   The only place the numbers can go wrong is one clearly named output stage.

2. **The arithmetic pipeline never stalls.** There's no global stall net. A sample is only admitted once its result is guaranteed a
   slot in the output FIFO, which a credit counter enforces with one invariant: `in_flight + out_fifo_occupancy ≤ OUT_FIFO_DEPTH`.
   Backpressure travels through FIFO occupancy and never through combinational logic, so `s_axis_tready` has no combinational
   dependence on `m_axis_tready`, and the block can sit next to any upstream or downstream block safely.

3. **One parameter changes the microarchitecture.** `MAC_UNITS` folds the datapath from 1 multiplier (`II = N`) up to `N/2`
   multipliers (`II = 1`) without touching the source, and all six fold factors are bit-identical to a reference model that knows
   nothing about folding. The measured `II` matches `ceil(N_eff/MAC_UNITS)` exactly. The area, frequency and throughput curve that comes
   out of this is the main engineering result, and the portfolio copy measured it (see Results).

And one promise: every number in this README can be reproduced by a command you can run with open-source tools on an ordinary Linux
machine.

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

The full description is in [`docs/02_architecture.md`](docs/02_architecture.md).

---

## Feature summary

| Feature | Status |
|---|---|
| AXI4-Lite CSRs, byte-strobed, with `SLVERR` policy and interrupt | Implemented and verified |
| Elastic buffers; lossless, order-preserving, `TLAST`-aligned stream | Implemented and verified |
| Credit-gated admission — output FIFO overflow structurally impossible | Implemented and verified |
| Independent input/output backpressure, no combinational `TREADY` path | Implemented and verified |
| Runtime-discoverable `CAPABILITY` register | Implemented and verified |
| RW1C interrupt status with set-wins-over-clear | Implemented and verified |
| Coefficient region decode with out-of-range rejection | Implemented and verified |
| Reset synchroniser (async assert, sync de-assert) | Implemented and verified |
| `y[n] = Σ h[k]·x[n-k]`, runtime tap count `1 ≤ N ≤ NTAPS_MAX` | Implemented and verified |
| Q1.15 samples and coefficients, exact products | Implemented and verified |
| Derived, provably overflow-free accumulator | Implemented and verified |
| Truncate / round-half-up / convergent rounding | Implemented and verified |
| Symmetric and asymmetric saturation with sticky status | Implemented and verified |
| Linear-phase symmetric pre-adder, incl. odd-length centre tap | Implemented and verified |
| `SYMMETRIC_EN=0` build omits the pre-adder and mirror mux entirely | Implemented and verified |
| Runtime coefficient reload via shadow bank, atomic at frame boundary | Implemented and verified |
| `MAC_UNITS` resource folding, `II = ceil(N_eff/MAC_UNITS)` | Implemented and verified |
| `IDLE/RUN/DRAIN/CFGERR` FSM; graceful stop loses no samples | Implemented and verified |
| Dual-clock control/data split with proper CDC | Specified only, planned for Phase 7 |

[`docs/00_project_plan.md`](docs/00_project_plan.md) has the phase plan and each phase's definition of done.

---

## Bound assertions, cover points and the injected-bug campaign (revision 3)

In revision 3 I added a checking layer and some tests, and the checking led to one RTL fix. `verif/fir_checkers.sv` holds assertions
(`a_*`), cover points (`c_*`) and four procedural scoreboards (`sb_*`). `verif/fir_binds.sv` attaches them with `bind` to the AXI4-Lite
slave, both stream FIFOs, the credit counter, the control FSM, the coefficient banks, the register file, the delay line, the output stage
and the top level, so the same checkers run in every cocotb suite. For each conditional assertion, `scripts/gen_vacuity.py` generates a
cover of its antecedent, which shows whether the precondition ever actually happened. This needs Verilator 5.052 built from source
(cocotb 2.x needs 5.036 or later, and the checkers use sequence delays). `make check` is unchanged and still uses Icarus. The results,
all in `results/sva/`:

| Item | Result | Evidence |
|---|---|---|
| Checkers | **105** assertions (101 concurrent, 4 procedural), **81** cover points | `results/sva/summary.txt` |
| Unmodified RTL | 18 simulation runs (5 control-plane builds, 5 stream builds, 2 arithmetic builds, 6 folded builds of the full IP): every test passes, **no assertion fires** | `results/sva/runs.txt` |
| Vacuity | all **80** conditional assertions had their antecedent reached in at least one run; **81 of 81** cover points hit | `results/sva/vacuity.txt`, `cover_bins.txt` |
| Injected bugs | **95 mutants** (48 chosen from the design's risks, 47 written afterwards, one per assertion that nothing had yet made fire): **93 caught**, 78 by a cocotb test, **15 only by an assertion**; the other 2 are equivalent (below) | `results/sva/mutants.txt` |
| Can each assertion fail? | **105 of 105** fired in at least one mutant run | `results/sva/mutants.txt` |
| The 2 survivors | `Q12` and `Q17` change only the flush term of the control FSM, which is dead logic: the top level ties `soft_clear = cmd_soft_reset \| cmd_flush` and `fir_ctrl` gives `soft_clear` priority. Proved equivalent with Yosys (`equiv_induct`: all 391 paired bits of the registers and outputs, one small parameter set); the same method does **not** prove six mutants that really change behaviour | `results/sva/equivalence.txt`, `make sva-equiv` |

A run only counts as "caught by a test" when a cocotb test failed or the run hit the 300 s hang limit. Nine mutants made a test wait
forever, and each of those also made an assertion fire. A run that the checkers stopped themselves (the simulator's error limit) counts
as caught by an assertion. The simulator output is line-buffered, because cocotb's log lines used to land in the middle of Verilator's
"Assertion failed" lines, which made it unclear which assertion had fired. The campaign now refuses to count a label that doesn't match
a defined assertion.

**The bug.** The first run of the checkers on the unmodified RTL was clean, and so was every test. Then the cover points showed that no
test ever flushed (CTRL.FLUSH or SOFT_RESET) while samples were in the pipeline. A directed test that does
(`test_flush_keeps_credit_invariant`) made `a_credit_inv`, `a_dp_nonneg`, `a_no_drop` and the RTL's own `credit_ctr` `$error` fire.
After a flush, the credit count overstated the free space in the output FIFO, and after three flushes and a long consumer stall only 89
to 95 of 96 offered samples came out (the exact number depends on the fold factor). Beats were being lost at the output FIFO, which
ignores backpressure by design. The cause was that `fir_core`'s `clear` zeroed the delay line but not the pipeline's control chain or
result registers; three small edits fix it. The file as I found it is kept in `verif/as_found/fir_core.sv` and is injected again as the
last mutant of the first group.

**Gaps in the tests, all closed.** Revision 3 added ten tests in `tb/cocotb/tests/`. The first three came from mutants that survived, the
other seven from cover points that no test reached. Two of them offer a second read, or a second write, while the response to the first
is stalled (`test_read_request_while_response_stalled`, `test_write_request_while_response_stalled`); one mutant was visible only to an
assertion because the driver never overlaps requests. The third stops the consumer from reset and requires the IP to accept exactly
`OUT_FIFO_DEPTH + IN_FIFO_DEPTH` samples (`test_full_stall_bounds_buffering`), because a credit pool larger than the output FIFO had
passed every data check. The seven from the cover points exercise flush and soft reset with samples inside the IP, the interrupt output,
leaving the configuration-error state, one-beat frames, commit requests around a bank swap, a register read while a write response is
pending, and credit integrity after a flush.

**What this doesn't show.** The assertions are only checked in simulation; there's no formal proof of the properties. The stimulus is the
repository's own directed and random tests, with no UVM-style functional-coverage model. The 93 / 95 figure describes this list of bugs,
not bugs in general, and the equivalence proof covers one small parameter set. The timing and area figures were measured before the fix
and again after it, and the table under Results is the re-measured one. Run times (measured with `--jobs 7` on 8 cores in the review re-run, `_evidence/rev3_review/timing.txt`; the make targets use the scripts' default of 4 jobs): the checker baseline 96 s from a cold start and 20 s once its simulator models are built, the campaign 1625 s (27 minutes), the equivalence check 64 s.

## Documentation

| Document | Contents | Status |
|---|---|---|
| [`00_project_plan.md`](docs/00_project_plan.md) | Repository layout, 12 phases, risks, toolchain | Complete |
| [`01_requirements.md`](docs/01_requirements.md) | Tiered requirements (MUST/SHOULD/NICE) | Complete |
| [`02_architecture.md`](docs/02_architecture.md) | Blocks, dataflow, flow control, reset, bottlenecks | Complete |
| [`engineering_log.md`](docs/engineering_log.md) | Every decision with options and tradeoffs (D-001…D-030) | Live |
| [`04_interfaces.md`](docs/04_interfaces.md) | Signal tables, register map, driver sequence, stream contract | Complete for both interfaces |
| [`03_microarchitecture.md`](docs/03_microarchitecture.md) | Fold schedule, pipeline, FSM, corner cases | Complete |
| [`05_fixed_point.md`](docs/05_fixed_point.md) | Numerical model, derivations, equations | Complete |
| `06_verification_plan.md` | Strategy, verification matrix | not written yet (Phase 5) |
| `07_testbench_architecture.md` | TB components, UVM justification | not written yet (Phase 5) |
| `08_assertion_plan.md` | SVA properties and intent | not written yet (Phase 6) |
| `09_cdc_reset.md` | Clock/reset/CDC strategy | not written yet (Phase 7) |
| `10_rtl_coding_plan.md` | Conventions, lint expectations | not written yet (Phase 7) |
| `11_performance_model.md` | Latency, throughput, utilisation | not written yet (Phase 10) |
| `12_synthesis_sta.md` | Critical path, slack, area | not written yet (Phase 9) |
| `13_ppa_optimization.md` | Optimisation experiments, before/after | not written yet (Phase 10) |
| `14_formal_verification.md` | Properties proved and *not* proved | not written yet (Phase 6) |
| `15_resume_positioning.md` | Interview preparation | not written yet (Phase 12) |

---

## Toolchain

Everything is open source. These were present on the development machine:

| Tool | Version | Purpose |
|---|---|---|
| Icarus Verilog | 12.0 | Simulation |
| Yosys | 0.33 | Synthesis |
| OpenSTA | 2.7.0 | Static timing analysis |
| GTKWave | — | Waveform viewing |
| Python | 3.13.9 | Reference model, PPA sweep driver |

Before the relevant phase you also need `cocotb` and `numpy` (Phases 1 and 3), Verilator 5.x (Phase 6) and SymbiYosys with an SMT solver
(Phase 6).

The plan named two standard-cell libraries, **Nangate45** as the primary one and **Sky130HD** to show portability. So far only Sky130 HD
has been run.

---

## Build and run

These work today:

```bash
make check           # everything that gates a phase (the block quoted above)
make tools           # report which tools are present
make regress         # both cocotb suites
make csr / stream / fir / top   # one suite at a time
make fold-sweep      # bit-identity across every MAC_UNITS (risk R3)
make fir-full        # the >=1M-sample arithmetic DoD run
make sweep           # both suites across their parameter sets
make elab-sweep      # Yosys structural gate across parameter sets
make comb-check      # structural proof of TR-04 on the elaborated netlist
make verify-widths   # executable proof of the accumulator derivation
make waves SUITE=stream   # regression with an FST dump for GTKWave
make ppa-sweep       # Yosys + OpenSTA over MAC_UNITS (portfolio copy)
make sva             # the suites under Verilator 5.052 with the bound checkers (revision 3)
make sva-mutants     # the 95-mutant injected-bug campaign
make sva-equiv       # Yosys equivalence check of the two surviving mutants
```

You need `iverilog`, `yosys`, `python3` and `pip install cocotb`; the `sva` targets also need Verilator 5.052.

Still planned for later phases: `make lint` (Verilator), `make synth` (Yosys → Nangate45/Sky130HD), `make sta` (OpenSTA) and `make
formal` (SymbiYosys). The `MAC_UNITS` sweep that `make ppa` was meant to run exists now as `make ppa-sweep`.

---

## Results

**Verified so far** (reproduce with `make regress && make elab-sweep`):

| Result | Value | Evidence |
|---|---|---|
| Control-plane regression | 18/18 tests (16 originally, 2 added in revision 3), 5 parameter sets | `make csr-sweep` |
| Stream regression | 12/12 tests, 5 FIFO/pipe configurations | `make stream-sweep` |
| Lossless streaming | **100 000 beats**, exact sequence + `TLAST` match | `make stream` |
| Sustained throughput | **II = 1.000** under continuous streaming | `test_continuous_stream_ii1` |
| Credit invariant | output FIFO **never** written while full | per-cycle checker, every test; since revision 3 also `a_credit_inv` and `a_no_drop` in every simulation and a directed test of the flush case (`test_flush_keeps_credit_invariant`) |
| Full-IP regression | 15/15 tests at `MAC_UNITS` = 8 (7 originally, 8 added in revision 3) | `make top` |
| Protocol violations (AXI4-Lite + AXI4-Stream) | **0** | monitors active in every test |
| TR-04: `m_axis_tready` → `s_axis_tready` | **no combinational path** | `make comb-check` (self-testing) |
| Inferred latches / tri-states / comb. loops | **0**, all 5 configurations | `synthesis/reports/elab_*.log` |
| Accumulator width derivation | sufficient **and** minimal, `NTAPS_MAX` 4…256 | `make verify-widths` |
| FIR arithmetic vs Python model | **bit-exact**, 12/12 tests at `NTAPS_MAX=64` | `make fir NTAPS_MAX=64` |
| Peak accumulator under extreme operands | `2³⁶` — exactly the derived worst case, 50.0% of range | `make fir` |
| Pipeline latency | **11 cycles** at 64 taps = `5 + clog2(64)` (the folded core), measured in the test, not taken from the parameter | `test_latency_and_widths`, `results/arith/` logs |
| Symmetric ≡ asymmetric, N = 1…64 odd and even | bit-identical | `test_symmetric_identity` |
| Reference model vs float convolution | worst **0.4999 LSB** = the exact bound for correct rounding | `make verify-model` |
| **Phase 3 DoD** | **1 000 000 samples**, 20 coefficient sets, 6 mode pairs — **bit-exact** | `make fir-full` |
| **Risk R3 gate** | `MAC_UNITS` ∈ {1,2,4,8,16,32} all bit-identical to a fold-agnostic model | `make fold-sweep` |
| Measured initiation interval | 4.027 vs predicted 4 · 2.027 vs 2 · 1.028 vs 1 | `make top` |
| Coefficient swap timing | spanning frame keeps OLD coefficients; next frame uses new | `test_coefficient_swap_at_frame_boundary` |
| Graceful stop | beats out == `SAMPLE_CNT` exactly when `ENABLE` drops mid-stream | `test_drain_loses_nothing` |
| TR-04 on the **shipping** top level | 6 properties, no combinational path | `make comb-check` |

**Measured in the portfolio copy** (Sky130 HD, pre-layout, ideal clock; `results/ppa/ppa_sweep.md` has the table and the method):

| MAC_UNITS | II (cycles/sample) | cells | area (um^2) | flip-flops | T_min (ns) | F_max (MHz) | throughput (Msamples/s) | method check |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 32 | 18662 | 200006 | 3860 | 5.392 | 185.5 | 5.80 | verified (+0.107 / -0.108 ns) |
| 2 | 16 | 20743 | 222904 | 3964 | 5.460 | 183.2 | 11.45 | verified (+0.109 / -0.109 ns) |
| 4 | 8 | 25497 | 265551 | 4169 | 5.286 | 189.2 | 23.65 | verified (+0.105 / -0.106 ns) |
| 8 | 4 | 33370 | 344493 | 4578 | 5.198 | 192.4 | 48.10 | verified (+0.104 / -0.104 ns) |
| 16 | 2 | 49537 | 486684 | 5392 | 5.266 | 189.9 | 94.95 | verified (+0.105 / -0.106 ns) |
| 32 | 1 | 77787 | 760277 | 7016 | 5.405 | 185.0 | 185.01 | verified (+0.108 / -0.108 ns) |

**Not yet measured:**

| Metric | Value |
|---|---|
| Maximum frequency (Nangate45) | **NOT YET MEASURED** (Sky130 HD is what was run) |
| Critical path (named blocks) | **NOT YET MEASURED** (the netlist is flattened; the table gives the period only) |
| Frequency after place and route | **NOT YET MEASURED** |
| UVM-style functional-coverage model | **NOT BUILT** (cover properties exist, see "Bound assertions" below: 81 of 81 hit) |
| Formal properties proved | **NOT YET ATTEMPTED** |
| Power estimate | **NOT YET MEASURED** |

---

## License

TBD.
