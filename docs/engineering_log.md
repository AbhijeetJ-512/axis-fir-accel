# Engineering Log

Chronological record of design decisions. Every entry records what was considered, what was
chosen, why, and what was given up. Entries are never deleted — if a decision is reversed, a
new entry supersedes it and says so.

Format: **Problem → Options → Decision → Reason → Tradeoffs → Result**

---

## D-001 — Selection of the DSP function

**2026-08-17 · Phase 0**

**Problem.** The project brief specified "a DSP accelerator" without fixing the algorithm.
Candidates had to be evaluated on RTL learning value, DSP relevance, verification tractability,
pipeline opportunity, AXI4-Stream fit, synthesis/STA value, interview value and scope risk.

**Options considered.**

1. *Moving average / CIC.* Cheap, elegant bit-growth story.
2. *Vector MAC / dot product.* Frame-oriented accumulate.
3. *FIR filter, programmable and foldable.*
4. *2D convolution.* CNN-adjacent, line-buffer based.
5. *FFT.* Highest surface impressiveness.

**Decision.** Programmable, linear-phase, fixed-point **FIR filter** with a foldable MAC array.

**Reason.** Only the FIR scored high on *all* of: arithmetic (rather than control) learning
value; native `II = 1` mapping onto AXI4-Stream; a verification story permitting **bit-exact**
comparison against an integer reference model; and a genuine, parameterisable PPA tradeoff that
can be measured rather than asserted.

- Moving average was rejected as a primary: with no multiplier and no coefficient storage there
  is nothing to close timing on. It survives as a *directed test case* — a boxcar filter is a
  FIR with equal coefficients.
- Vector MAC was rejected as a primary: one output per frame makes the output stream sparse, so
  output-side backpressure — a skill this project exists to demonstrate — is barely exercised.
  Retained as a NICE-TO-HAVE mode reusing the same MAC array.
- 2D convolution was rejected because line buffers, not arithmetic, would dominate. A 3×3 window
  over 1920-pixel lines is ~61 kbit of storage; as flip-flops on an ASIC flow without memory
  macros that would be the overwhelming majority of cell area and would drown out every
  arithmetic optimisation the project is meant to study.
- FFT was rejected on three concrete grounds: (a) it is block-based, so AXI4-Stream degenerates
  into "fill buffer, stall, compute, drain" and the elastic-pipeline content largely disappears;
  (b) fixed-point FFT verification is *tolerance-based* (SNR bounds), which is materially weaker
  portfolio evidence than bit-exact equality; (c) the effort concentrates in stage sequencing,
  twiddle ROM, memory banking and bit-reversal — control-path work already demonstrated by the
  existing RISC-V core and cache projects.

**Tradeoffs.** FIR is the most conventional choice and will impress least on first glance. That
is accepted deliberately: the differentiation is placed in the *quality* of the implementation
(provable arithmetic, credit-based flow control, measured PPA curve) rather than in algorithm
novelty. A well-executed FIR beats a half-finished FFT in review.

**Result.** Architecture baselined in `02_architecture.md`.

---

## D-002 — Portfolio differentiation against the existing `axi_aes_engine`

**2026-08-17 · Phase 0**

**Problem.** An audit of `Projects/axi_aes_engine/` found an existing IP with AXI4-Stream +
AXI4-Lite, a CSR regfile, a pipelined datapath, SVA files, UVM-style agents, a scoreboard, and
Yosys/Vivado scripts. Structurally this is the same shape as the brief. Building a second one
without deliberate differentiation would produce a duplicate rather than a complement.

A second finding: that project's build artefacts show its verification and synthesis **never
executed** —

```
build/smoke.log   : /bin/bash: line 1: build/simv: No such file or directory
build/random.log  : /bin/bash: line 1: build/simv: No such file or directory
build/yosys.log   : ERROR: No such command: set
```

Its UVM testbench requires VCS or Xcelium, neither of which is installed; its Yosys script is
Tcl fed to Yosys's native script parser. Its README's throughput and "UVM verified" claims are
therefore unsupported.

**Options considered.**
1. Abandon the DSP project and repair `axi_aes_engine` instead.
2. Build the DSP accelerator as specified, accepting the structural overlap.
3. Build the DSP accelerator with differentiation deliberately concentrated on the axes the AES
   engine does not touch.

**Decision.** Option 3.

**Reason.** The overlap is in *interfaces*, which is superficial. The differentiating axes are:

| Axis | AES engine | This project |
|---|---|---|
| Arithmetic | Bitwise; no adders, no rounding | Signed fixed-point, derived widths, rounding modes, saturation |
| Flow control | Global stall | Credit-gated, non-stalling pipeline |
| PPA | Asserted, unmeasured | Measured sweep across a folding parameter |
| Reproducibility | Requires unavailable proprietary tools | Runs end-to-end on open-source tools |

The reproducibility axis is the most valuable and the least glamorous: **every number in this
project's README must be produced by a command a reviewer can run.**

**Tradeoffs.** Two AXI-interfaced accelerators on one GitHub profile still invites the "isn't
this the same project twice?" question. Mitigated by making the README's opening paragraph state
what is *architecturally* different, not just what the algorithm is.

**Result.** Recorded as the framing constraint for `README.md` and `15_resume_positioning.md`.

---

## D-003 — Verification stack

**2026-08-17 · Phase 0**

**Problem.** The brief asks for constrained-random stimulus, functional coverage, scoreboarding
and SVA. The available simulator is **Icarus Verilog 12.0**, which supports no classes, no
`randomize()`, no covergroups and no concurrent assertions. A conventional SystemVerilog/UVM
testbench cannot execute on this machine — which is precisely why `axi_aes_engine`'s logs are
empty.

**Options considered.**
1. cocotb (Python) on Icarus only.
2. Install Verilator 5.x and write a native SystemVerilog testbench.
3. cocotb + Icarus as the primary environment, adding Verilator (concurrent SVA, lint) and
   SymbiYosys + a SMT solver (formal) in a later phase.
4. Write a full UVM environment regardless, for resume optics.

**Decision.** Option 3.

**Reason.** cocotb provides everything the dynamic verification requirements need — drivers,
monitors, scoreboard, constrained-random stimulus, functional coverage — and it *runs today*.
Crucially, the Python reference model becomes bit-exact by construction, because Python integers
are arbitrary-precision, so the model can be written to mirror the RTL's arithmetic exactly
without width artefacts. Verilator is then added specifically for the things Icarus cannot do:
concurrent SVA and strict lint. SymbiYosys covers the formal properties.

Option 4 was rejected explicitly: writing a UVM environment that cannot be executed reproduces
the exact credibility failure documented in D-002. Unrunnable code is worse than absent code,
because it implies a claim that isn't true.

**Tradeoffs.** "cocotb" carries less recruiter keyword weight than "UVM". Accepted: the
verification plan will state clearly which methodology maps to which UVM concept, so the
interview answer is available without the false claim. Also, three tools must be installed and
kept working rather than one.

**Result.** Drives `06_verification_plan.md` and `07_testbench_architecture.md`.

---

## D-004 — Synthesis and STA target library

**2026-08-17 · Phase 0**

**Problem.** Yosys 0.33 and OpenSTA 2.7.0 are installed; Nangate45, Sky130HD, ASAP7 and
IHP-SG13G2 Liberty files are present locally. A target had to be chosen before SDC constraints
and timing requirements could be written.

**Options considered.** Nangate45 only; Sky130HD only; Nangate45 + Sky130HD; adding ASAP7.

**Decision.** **Nangate45 primary, Sky130HD secondary.**

**Reason.** Nangate45 is well-behaved with Yosys+OpenSTA and fast enough that the *multiplier*
is the critical path rather than the library — which keeps the timing story about the
microarchitecture, which is the point. Sky130HD is then a library swap that demonstrates
portability and adds real-PDK credibility. ASAP7 was declined: its unit scaling is easy to get
wrong in OpenSTA and a misconfigured 7nm number is worse than no 7nm number.

**Tradeoffs.** Nangate45 is a predictive academic library, so absolute area and frequency figures
are not directly comparable to a commercial 45nm process. This will be stated wherever numbers
appear; the *relative* shape of the PPA curve is the meaningful result and is unaffected.

**Result.** Drives `constraints/`, `synthesis/scripts/`, `sta/scripts/`.

---

## D-005 — Clock domain staging

**2026-08-17 · Phase 0**

**Problem.** How much CDC to build, and when.

**Options considered.** Single clock with documented CDC awareness only; dual clock from day
one; single clock first with a real dual-clock split added later; full three-domain design.

**Decision.** **Single clock through Phase 6; real control/data clock split introduced in
Phase 7** as an explicit, documented architecture change.

**Reason.** CDC-awareness-without-implementation is a weak interview answer, and the target
engineer already has CDC on their résumé — so documenting without building adds nothing. Building
dual-clock from day one, however, loads every early phase with clock-ratio randomisation before
the datapath is even known-good. Staging it gets real synchronisers, a real request/acknowledge
config transfer and a real Gray-pointer FIFO into the design, at a point where the datapath is
already verified and any new failure is unambiguously a CDC failure.

**Tradeoffs.** The Phase 7 change touches `axil_slave`, `csr_regfile` and the top level, and
invalidates part of the Phase 5 regression. Accepted — and it is itself portfolio content, since
"here is how the architecture changed and what it cost" is exactly what section 27 of the brief
asks for.

**Result.** Requirements CR-01 … CR-08; deferred detail to `09_cdc_reset.md`.

---

## D-006 — Flow control: credit-gated pipeline rather than global stall

**2026-08-17 · Phase 0**

**Problem.** A fixed-latency arithmetic pipeline must interoperate with two independently
stalling AXI4-Stream interfaces without losing, duplicating or reordering data, and without
creating a combinational path from `m_axis_tready` back to `s_axis_tready`.

**Options considered.**
1. **Global stall.** `stall = out_full`, fanned out to every pipeline register enable.
2. **Per-stage elastic handshaking.** Valid/ready plus a skid buffer at every stage.
3. **Credit-gated admission.** Pipeline never stalls; a sample is admitted only when its output
   is already guaranteed a slot in the output FIFO.

**Decision.** Option 3.

**Reason.** The credit scheme reduces the whole flow-control problem to a single counter with a
one-line invariant (`in_flight + out_fifo_occupancy ≤ OUT_FIFO_DEPTH`), which can be argued by
counting and proved formally. Global stall creates a high-fanout timing-critical net that gets
worse as the pipeline deepens, and makes losslessness depend on every stage observing the same
enable in the same cycle. Per-stage elasticity is the most general but adds a skid buffer's area
at every stage for flexibility a fixed-latency datapath does not need.

**Tradeoffs.** Requires `OUT_FIFO_DEPTH > pipeline depth`, else the credit counter throttles
below `II = 1`. This is checked at elaboration. Also costs one output FIFO that a global-stall
design could in principle omit.

**Result.** `02_architecture.md` §5. Primary formal proof target.

---

## D-007 — Accumulator width derived, not chosen

**2026-08-17 · Phase 0**

**Problem.** How wide must the accumulator be, and what should happen if it overflows?

**Options considered.** Pick a round number (40 or 48 bits) and add saturation logic; derive the
minimum width from a worst-case bound and prove overflow impossible.

**Decision.** Derive: `ACC_W = DATA_W + COEF_W + ceil(log2(NTAPS_MAX))` = 38 for the default
`NTAPS_MAX = 64`.

**Reason.** With Q1.15 operands, `|x·h| ≤ 2^30`, so `|Σ| ≤ NTAPS_MAX · 2^30 = 2^36`, which a
signed 38-bit value represents with a bit to spare (a signed 37-bit value would fall short by
exactly one LSB at the positive extreme). Overflow in the accumulator is therefore **impossible
by construction**, so the block needs no saturation logic and no overflow flag, and the entire
numerical failure surface collapses to one place: the output narrowing stage.

**Tradeoffs.** Two fewer bits than a "round" 40 saves negligible area; the value of the decision
is the *argument*, not the bits. It also means `ACC_W` changes with `NTAPS_MAX`, so nothing may
hard-code 38 anywhere in RTL or testbench.

**Result.** Requirements NR-04, NR-05. Full derivation deferred to `05_fixed_point.md`.

---

## D-008 — Shadow coefficient bank with frame-boundary swap

**2026-08-17 · Phase 0**

**Problem.** Software must be able to change the filter while the stream is running. A naive
single bank would let a write to `h[3]` land mid-frame, producing outputs computed from a mixture
of two filters — a silent numerical corruption invisible to any protocol checker.

**Options considered.** Forbid reconfiguration while streaming; single bank with a
software-managed stop/reconfigure/restart sequence; dual bank with atomic swap.

**Decision.** Dual bank, swapped atomically at a frame boundary on explicit software commit.

**Reason.** Forbidding reconfiguration pushes the problem to firmware, where it will eventually
be got wrong, and costs stream downtime. The dual bank makes the hazard structurally impossible
and costs one extra register array plus a mux — cheap relative to the MAC array.

**Tradeoffs.** Doubles coefficient storage, which at large `NTAPS_MAX` is a real area cost and
will show up in the PPA data. Also adds `COEFF_PENDING` state that software must understand.

**Result.** `coeff_bank` block; requirements FR-03, FR-10, FR-11.

---

## D-009 — Coefficient banks are not reset

**2026-08-17 · Phase 0**

**Problem.** Should the coefficient register arrays be included in the reset tree?

**Decision.** No. Coefficient banks and `bank_sel` power up undefined.

**Reason.** Resetting them routes a reset net across the largest register array in the design for
no functional benefit: software must write coefficients before asserting `ENABLE`, and the
`CFG_ERR` / `ENABLE` gating prevents streaming with an unconfigured filter.

**Tradeoffs.** X-propagation in simulation from unwritten coefficients could mask bugs, so the
testbench must explicitly initialise them and an assertion must fire if `ENABLE` is set before
any coefficient write. Recorded as a verification obligation, not an assumption.

**Result.** `02_architecture.md` §7. Flagged as an expected review question.

---

## D-010 — `MAC_UNITS` as the primary PPA knob

**2026-08-17 · Phase 0**

**Problem.** The brief requires genuine, measured PPA optimisation experiments. Ad-hoc
micro-optimisations produce weak, hard-to-interpret data.

**Decision.** Make resource folding a first-class parameter: one `MAC_UNITS` value restructures
the datapath from fully-serial (1 multiplier, `II = N_eff`) to fully-parallel
(`N_eff` multipliers, `II = 1`), from a single source.

**Reason.** It produces a *curve* rather than a point — area, Fmax and throughput across five or
six configurations of identical RTL. That is a far stronger engineering artefact than "I moved a
pipeline register and gained 4%", and it demonstrates the property that actually makes RTL
reusable IP: one description, many implementation points.

**Tradeoffs.** Folding complicates `fir_ctrl` (pass scheduling) and `delay_line` (shift enable at
`1/P` rate), and multiplies the verification config matrix. Verification cost is contained by
choosing a specific subset of configurations to test exhaustively rather than all of them.

**Result.** Requirement PR-04; drives `13_ppa_optimization.md`.

---

## D-011 — Repository is a fresh, standalone git repository

**2026-08-17 · Phase 0**

**Problem.** Where the project lives.

**Decision.** `Projects/axis_fir_accel/`, initialised as its own git repository, consistent with
the sibling projects.

**Reason.** The IP is intended to be independently clonable and buildable — a reviewer must be
able to `git clone`, `make`, and see real results without acquiring anything else.

**Result.** Directory structure created; see `10_rtl_coding_plan.md` for the rationale behind
the layout.

---

## D-012 — RTL is written to the Yosys ∩ Icarus SystemVerilog subset

**2026-08-17 · Phase 1**

**Problem.** The first attempt to run the structural gate failed: RTL that Icarus compiled
without complaint was rejected outright by Yosys 0.33. Discovering these one error at a time
would have meant three separate rewrites, so the front end was probed systematically instead.

**Findings** (each verified with a minimal test case, not assumed):

| Construct | Icarus 12 | Yosys 0.33 |
|---|---|---|
| `function` inside a `package` | accepts | **rejects** |
| `import pkg::*` — header, file scope, or module body | accepts | **rejects, all three** |
| `int'(expr)` cast | accepts | **rejects** |
| `pkg::NAME` fully qualified (constants, enum literals, enum types) | accepts | accepts |
| `typedef enum` / `localparam` in a package | accepts | accepts |
| `N'(expr)` size cast, `'0`/`'1`, `+:` part-select | accepts | accepts |
| `always_comb` with `for`, `always_ff` async reset, generate-`if`, function in a *module* | accepts | accepts |

**Decision.** Three project-wide coding rules:

1. **Never `import`.** Always write `fir_pkg::NAME` in full.
2. **No functions in packages.** Width derivations become preprocessor macros in
   `rtl/common/fir_defs.svh`; single-module helpers are declared inside that module.
3. **No `int'()` casts.** Use width-matched localparams and plain unsigned comparisons.

**Reason.** The alternative — carrying RTL only one of two mandatory tools can read — hides a
Phase 8 failure inside Phase 1 work. Since synthesisability is a MUST requirement (SR-01), the
language subset is part of the specification, not a detail. Full qualification also turns out to
read better: every constant visibly announces where it comes from.

**Tradeoffs.** `fir_pkg::` prefixes are verbose (91 of them in `csr_regfile` alone). Macros are
less type-safe than functions. Both are accepted as the cost of a toolchain that actually runs.
If `sv2v` is added later this constraint could be relaxed, but that would add a dependency to
buy back only syntax.

**Result.** `rtl/common/fir_defs.svh` created; imports removed; `scripts/elab_sweep.sh` added so
the gate runs across parameter sets, not just the default build. All five configurations
elaborate with zero latches, zero tri-states and no combinational loops.

---

## D-013 — AXI4-Lite write throughput traded for channel independence

**2026-08-17 · Phase 1**

**Problem.** `AWREADY` could be allowed to depend on `BREADY`, letting a new write be accepted
in the same cycle the previous response retires (1 write per cycle). Or it could depend only on
registered state (1 write per 3 cycles).

**Decision.** Registered-only. `AWREADY = !bvalid_q && !aw_full_q`.

**Reason.** A combinational path from `BREADY` back to `AWREADY` is a genuine integration hazard:
an interconnect whose `BREADY` depends on the slave's `AWREADY` closes a loop that neither side
can see in isolation. The control plane carries one-time configuration traffic — 64 coefficient
writes at 3 cycles each is 192 cycles, roughly 2 µs at 100 MHz, once per reconfiguration. That
bandwidth is irrelevant; the loop risk is not.

**Tradeoffs.** 3× the write latency. Recovering it would need a response FIFO, which costs more
area than the entire CSR block saves.

**Result.** Verified by `test_aw_w_channel_ordering` and continuous protocol monitoring.

---

## D-014 — Writes to read-only registers return OKAY, not SLVERR

**2026-08-17 · Phase 1**

**Problem.** Should a write to `ID`, `CAPABILITY`, `STATUS` or the counters be an error?

**Decision.** Silently ignored, answered `OKAY`. `SLVERR` is reserved for unmapped addresses and
for `CFG` writes while `BUSY`.

**Reason.** Driver code routinely read-modify-writes across a register block; making a harmless
write to a status register a bus fault turns ordinary driver patterns into crashes. `SLVERR`
should mean "your software has a bug that will lose data" — a wrong address, or a configuration
change at a moment that would corrupt an in-flight frame.

**Tradeoffs.** A driver bug that writes to a RO register goes unreported. Accepted: it is
harmless by construction, whereas the false-positive cost is real.

**Result.** Documented in `04_interfaces.md` §3.3 as a contract. Verified by
`test_ro_registers_ignore_writes`.

---

## D-015 — `CFG_ERR` fires on enable, not on reset

**2026-08-17 · Phase 1**

**Problem.** `NTAPS` resets to 0, which is an illegal tap count. The first implementation
therefore raised sticky `CFG_ERR` and an interrupt on the first clock edge after reset, before
software had written anything.

**Decision.** Separate the two ideas. `cfg_illegal` stays a pure combinational validity
indicator (used to gate the FSM). The *sticky* status bit and the interrupt are driven by
`cfg_err_event = ctrl_enable && cfg_illegal`.

**Reason.** An unconfigured block is not a faulted block. A status bit that is always set at
reset carries no information and trains software to ignore it — which is worse than not having
it. Requirement FR-21 asks for the error when an illegally-configured block is *enabled*, which
is exactly what this implements.

**Tradeoffs.** Software cannot use `CFG_ERR` to pre-validate a configuration without enabling.
Acceptable: it can read `CFG` back and check the value itself, and `cfg_illegal` is available to
the FSM regardless.

**Result.** `test_reset_values` asserts `CFG_ERR == 0` out of reset;
`test_illegal_configuration` asserts it sets on enable and not before.

---

## D-016 — Simulation build directory is keyed on the parameter set

**2026-08-17 · Phase 1**

**Problem.** The first parameter sweep reported four "failures" in three configurations. All
were false: `make NTAPS_MAX=32` reused the compiled `NTAPS_MAX=64` simulation, because changing
a make *variable* does not invalidate a target whose *sources* are unchanged. Every build in the
sweep was the same 64-tap binary.

**Decision.** `SIM_BUILD` and the results file are keyed on the full parameter tuple.

**Reason.** A regression that silently tests the wrong build is worse than no regression: it
produces confident green results for configurations that were never elaborated. This is exactly
the class of failure this project exists to avoid.

**Tradeoffs.** One build directory per configuration, so `make clean` matters and disk use grows
with the sweep. Trivially worth it.

**Result.** The sweep now reports a distinct, correct `CAPABILITY` for each of five
configurations. The `CAPABILITY` register turned out to be the thing that caught this — worth
noting, since it was added for software discoverability, not for testbench self-checking.

---

## D-017 — Protocol monitor samples on the falling edge

**2026-08-17 · Phase 1**

**Problem.** The randomised traffic test failed with `156 AW accepted, 157 B responses`. The RTL
was checked first: `reg_wen` requires `!bvalid_q` and the response-retire arm requires
`bvalid_q`, so the two are mutually exclusive and a duplicate response is structurally
impossible. The fault was in the monitor.

**Options considered.** Sample at `RisingEdge` then `ReadOnly` (the common idiom); reorder the
driver's signal updates; sample mid-cycle at `FallingEdge`.

**Decision.** Sample at `FallingEdge` then `ReadOnly`.

**Reason.** `RisingEdge` + `ReadOnly` places the monitor in the *same timestep* as the clock
edge, racing the drivers, which update their signals in that same timestep in response to the
same edge. Depending on coroutine scheduling order the monitor sees pre-edge or post-edge
values, so a handshake can be double-counted or missed. Every driver in this environment acts
only on rising edges, so mid-cycle is quiescent: the values at the falling edge are exactly the
values the next rising edge will transfer.

**Tradeoffs.** Assumes a roughly 50% duty cycle and that no driver acts on the falling edge —
both true here, and both stated in the monitor's docstring so a future change cannot silently
break the assumption.

**Result.** AW and B counts now agree exactly (157/157). The episode is worth keeping: the first
instinct on a count mismatch is to suspect the DUT, and checking the RTL's structural argument
*before* changing anything is what identified it as a testbench bug in one step.

---

## D-018 — `credit_ctr` pulled forward from Phase 4 into Phase 2

**2026-08-17 · Phase 2**

**Problem.** The plan scheduled `axis_fifo` for Phase 2 and the credit counter for Phase 4. But
the credit invariant is only testable on a path that *has* a pipeline in it, so under the
original split Phase 2 would prove FIFO correctness and nothing about flow control, and Phase 4
would have to bring up the fold scheduler and the flow control simultaneously.

**Decision.** Build `credit_ctr` in Phase 2. The Phase 2 device under test becomes the Phase 4
stream skeleton with the arithmetic replaced by a fixed-latency dummy pipe.

**Reason.** The dummy pipe has exactly the three properties flow control depends on — fixed
latency, one output per input, never stalls — and no others. Everything proved about
losslessness, ordering, `TLAST` alignment and the credit invariant therefore transfers unchanged
when the real datapath replaces it. Phase 4 then adds only the fold scheduler.

**Tradeoffs.** Phase 2 is larger than planned. Accepted, and declared rather than made silently
(cross-phase rule 3).

**Result.** 12 stream tests passing across five FIFO/pipe configurations.

---

## D-019 — `TREADY` during reset: the requirement was wrong, not the RTL

**2026-08-17 · Phase 2**

**Problem.** `test_reset_midstream` failed on the assertion that `s_axis_tready` is low
throughout reset. Investigation: resetting the FIFO pointers makes the buffer empty, and
`s_tready = !full`, so `s_axis_tready` is **high** during reset.

**Options considered.**
1. Change the RTL: `s_tready = !full && rst_n`.
2. Change the requirement.

**Decision.** Change the requirement (CR-04).

**Reason.** The AXI4-Stream specification constrains the *master* during reset — `TVALID` must be
low, and may not be asserted until a rising edge after `ARESETn` is high. It places **no
requirement on `TREADY`**, precisely because a compliant master presents nothing for a high
`TREADY` to accept. `m_axis_tvalid` *is* correctly low during reset (the output FIFO is empty),
so the IP is compliant. Gating `s_tready` with `rst_n` would insert the reset net into the ready
path — extra logic and extra load on a timing-relevant signal — to satisfy a rule that does not
exist.

**Tradeoffs.** A requirement changed to match an implementation always deserves suspicion. The
justification here is external to the project (the AMBA specification), not a rationalisation of
convenience, and the corrected requirement is *stricter about the thing that matters*: it now
names `m_axis_tvalid` explicitly as the property under test.

**Result.** CR-04 rewritten with the reasoning inline. Test now asserts `m_axis_tvalid == 0`
on every reset cycle.

---

## D-020 — `soft_clear` must not flush the output FIFO

**2026-08-17 · Phase 2**

**Problem.** The output protocol monitor caught a genuine violation:
`m_axis: TVALID withdrawn without TREADY`. Asserting `soft_clear` while the output FIFO held a
beat the consumer had not yet accepted reset the FIFO pointers, dropping `TVALID` mid-transfer.

**Options considered.**
1. Document it: `SOFT_RESET` is a local reset, and software must quiesce the stream first.
2. Delay the flush until the presented beat is accepted — deadlocks if the consumer never is.
3. Flush the input FIFO and the pipeline but **not** the output FIFO, reconciling the credits.

**Decision.** Option 3.

**Reason.** The downstream consumer has no visibility of our soft reset. Having offered it a
beat, we are not entitled to withdraw it — that is what the protocol rule exists to prevent, and
"the driver should have known better" is not a design. Option 3 keeps the interface contract
intact at the cost of one subtractor.

The subtlety is credits. A flush discards in-flight samples, whose credits would otherwise never
be returned, so the counter would leak downward on every flush until throughput collapsed. The
counter is therefore *reloaded* on flush with the exact number of free output slots after the
cycle's push and pop settle: `MAX_CREDITS - (out_count + push - pop)`. That restores the
invariant precisely rather than approximately. `credit_ctr` gained a `clear_value` port for this.

**Tradeoffs.** `FLUSH` no longer means "the IP is completely empty afterwards" — already-computed
results still drain out. That is the correct semantic anyway: those results are valid outputs of
the *old* configuration and the downstream asked for them.

**Result.** `test_soft_clear` now asserts the reconciled credit value against the retained
occupancy. Zero protocol violations across all five stream configurations.

---

## D-021 — TR-04 is established structurally, and the checker self-tests

**2026-08-17 · Phase 2**

**Problem.** "No combinational path from `m_axis_tready` to `s_axis_tready`" cannot be
established by simulation. Simulation samples behaviour; the hazard is a loop that only closes
when a particular interconnect is attached, which the testbench never is.

**Decision.** Check it structurally on the elaborated netlist with Yosys `%cie*` — cone of
influence through combinational cells only — intersected with the source port.

**Reason.** The property is structural, so the evidence should be too.

Two mistakes were made getting there, both worth recording. First, `%ci*:-$dff` (the obvious
"don't traverse flops" syntax) does not work; `%cie` is the purpose-built operator. Second, and
more dangerous: `select @cone i:m_axis_tready` computes a **union**, which is never empty, so the
check passed vacuously on a module that was deliberately built to fail. The intersection
operator `%i` is required.

That second mistake is the reason the script now **self-tests**: before checking the design it
runs the same probe against two tiny modules where the answer is known — one with a direct wire,
one with a flop in the path — and aborts unless it gets exactly one FOUND and one clean. A check
that cannot demonstrate a failure is not evidence of anything.

**Result.** `scripts/check_comb_independence.sh`. Three properties pass on
`axis_loopback_wrap`: TR-04, plus no `TVALID` and no `TDATA` feedthrough, so chained stream
instances cannot accumulate an unbounded combinational path.

---

## D-022 — The symmetric datapath needs no extra accumulator bits

**2026-08-17 · Phase 3**

**Problem.** The symmetric (linear-phase) form pre-adds two samples before multiplying, so each
operand is `DATA_W+1` bits and each product can reach `2³¹` instead of `2³⁰`. The obvious
conclusion is that `ACC_W` must grow by one bit in symmetric mode — which would mean two
different accumulator widths, or one width sized for the worse case and wasted in the other.

**Decision.** Neither. The existing `ACC_W = DATA_W + COEF_W + ceil(log2(NTAPS_MAX))` covers both
datapaths unchanged.

**Reason.** The extra operand bit is cancelled exactly by there being half as many products:

```
    asymmetric:   N         · 2³⁰  =  N · 2³⁰
    symmetric:    ceil(N/2) · 2³¹  =  N · 2³⁰
```

**This was verified before being relied on, not reasoned about and assumed.**
`scripts/verify_acc_width.py` now computes the symmetric bound independently and asserts it
equals the asymmetric one for every supported `NTAPS_MAX`. If a future width change ever breaks
the cancellation, the check fails rather than the filter silently corrupting.

**Tradeoffs.** None found. The result is a genuine simplification: one accumulator width, one
no-overflow proof, both modes.

---

## D-023 — Icarus silently ignores part-selects inside `always_*` processes

**2026-08-17 · Phase 3**

**Problem.** `round_sat` originally saturated inside an `always_comb`, assigning
`y = max_pos[DATA_W-1:0]`. Icarus 12 reported:

```
sorry: constant selects in always_* processes are not currently supported
       (all bits will be included)
```

**This is a correctness hazard wearing the clothes of a cosmetic warning.** "All bits will be
included" means the part-select is *discarded* — the tool does not refuse the code, it silently
compiles something else. Any behaviour it produced would have been an artefact of the simulator
rather than of the design, and would not have matched synthesis.

**Decision.** Rewrite the saturation as continuous assignments, with the three narrowed candidate
values (`max_pos_t`, `min_neg_t`, `rounded_t`) computed outside any process and the process
reduced to a three-way select — which then needs no process at all.

**Reason.** Continuous assignments have no such limitation. The block is a pure function of
`(acc, round_mode, sat_mode)`, so expressing it as one is also more honest about what it is.

**Tradeoffs.** None. The result is shorter and has no latch-inference surface at all.

**Lesson worth keeping.** A simulator warning that says "not supported, proceeding anyway" is a
build-breaking error in disguise. This is the second time in the project that a tool limitation
would have produced a confidently wrong result if taken at face value (the first was Yosys and
package functions, D-012).

---

## D-024 — cocotb discovery moved into the Makefile

**2026-08-17 · Phase 3**

**Problem.** `make` worked in one shell and failed in the next with
`cocotb-config: No such file or directory`. The cause: `python3` on PATH is the *system*
interpreter, which has no cocotb; cocotb lives in a conda prefix that is on PATH only sometimes.

**Options considered.** Document "activate your environment first"; hard-code the conda path;
discover the interpreter in the Makefile.

**Decision.** Discover it, in the **top-level** Makefile.

**Reason.** "Works if your shell happens to be set up right" is precisely the failure this
project exists to avoid (D-002). A reviewer who clones the repository and types `make` must get
a result, not an environment puzzle.

The placement matters and cost an experiment to establish: cocotb's own `Makefile.sim` resolves
`cocotb-config` with a **parse-time** `$(shell ...)`, and GNU Make's `export PATH` affects
**recipes only, not parse-time shell calls**. Verified directly:

```
shell-sees:  NOTFOUND
recipe-sees: /home/abhi/miniconda3/bin/cocotb-config
```

So exporting PATH inside `tb/cocotb/Makefile` cannot work — by the time that file is parsed it is
already too late. Doing it in the top-level Makefile does work, because the sub-make is launched
from a *recipe* and inherits the corrected environment. `tb/cocotb/Makefile` now detects the
situation and emits an actionable error instead of failing three layers down inside cocotb.

**Tradeoffs.** The discovery loop hard-codes a few conventional locations
(`$CONDA_PREFIX`, `~/miniconda3`, `~/anaconda3`) as fallbacks. `PYTHON=` overrides it. Verified by
running the full suite from `PATH=/usr/bin:/bin`.

---

## D-025 — Rounding as one predicate, plus a test that the modes are distinguishable

**2026-08-17 · Phase 3**

**Problem.** Three rounding modes could be three separate expressions, or one shared floor plus a
mode-dependent increment.

**Decision.** One shared floor, one predicate:

```
    TRUNC       inc = 0
    HALF_UP     inc = guard
    CONVERGENT  inc = guard & (sticky | lsb)
```

**Reason.** Cheaper in gates (one adder, one mux on the increment) and far easier to argue about:
the three modes differ *only* in when they add one, so a reviewer checks one line rather than
three expressions for consistency.

**The second half of the decision matters more.** A suite in which all three modes pass proves
nothing if the stimulus never produced a tie — the modes would be indistinguishable and the tests
would be checking the same behaviour three times. `test_rounding_modes_differ` therefore asserts
that TRUNC, HALF_UP and CONVERGENT produce **different** output on the chosen stimulus. If a
future change made the stimulus miss the tie cases, that test fails and says so, rather than the
other rounding tests quietly becoming vacuous.

This is the same principle as the self-test in `check_comb_independence.sh` (D-021): a check that
cannot demonstrate a failure is not evidence of anything.

**Tradeoffs.** `RND_RESERVED` maps to `inc = 0` in the `default` arm rather than being unreachable.
It is prevented from being selected by `CFG_ERR`, so the arm is defensive only — but leaving the
case statement without a default would infer a latch, which is worse.

---

## D-026 — Simulation speed of the fully-parallel datapath: hypothesis raised, measured, refuted

**2026-08-17 · Phase 3**

**Problem.** The Phase 3 suite at `NTAPS_MAX = 64` took **784 seconds for 31 227 cycles — about
40 cycles/second**. At that rate the ≥1M-sample Definition-of-Done run would take roughly eight
hours, which makes it effectively unrunnable and would push people to skip the regression.

**Measured scaling** (identical stimulus, varying only `NTAPS_MAX`):

| `NTAPS_MAX` | rate | change per doubling |
|---|---|---|
| 8 | 662 cyc/s | — |
| 16 | 525 cyc/s | 1.3× slower |
| 32 | 200 cyc/s | 2.6× slower |
| 64 | 36 cyc/s | **5.6× slower** |

Superlinear, and sharply so at the top.

**Hypothesis.** The superlinearity looked like the O(N²) structure in the design: `NTAPS_MAX`
instances each selecting one of `NTAPS_MAX` taps at a runtime index, for the symmetric mirror
tap. At N=64 that is 4096 select paths. `02_architecture.md` §9 had independently predicted this
mux as the third-most-likely critical path, so the two suspicions reinforced each other.

**Test.** Gate the mirror mux out entirely at elaboration and re-measure the same stimulus.

**Result — the hypothesis was WRONG.** Removing the mux gained only 13% (34.1 → 38.4 cyc/s).
The cost is spread across the whole 64-wide datapath — 64 multipliers, a 63-node 38-bit adder
tree, a 64×16 shift register — not concentrated in any one structure. Recorded here rather than
quietly dropped: the prediction in the architecture document now has a measurement against it,
and a wrong prediction with evidence is worth more than a vague one.

*(This says nothing yet about the hardware critical path. Simulation cost and gate delay are
different quantities; Phase 9 measures the real one, and the §9 prediction stands until then.)*

**What the experiment did find.** `fir_core` ignored `SYMMETRIC_EN` altogether — it always built
the pre-adder and the mirror mux, regardless. Requirement **PR-08** specifies that when
`SYMMETRIC_EN = 0` the symmetric path is not synthesised and `CFG.SYMMETRIC` is tied off. The
parameter existed in the specification and in `csr_regfile`, and the datapath simply did not
honour it. Now implemented, and `test_symmetric_identity` asserts that a `SYMMETRIC_EN=0` build
genuinely ignores the `cfg_symmetric` pin rather than merely being untested on it.

**Decision on the DoD run.** The arithmetic under test does not depend on `NTAPS_MAX` except
through `ACC_W` and the array size; every rounding, saturation, symmetry and tap-count path is
exercised identically at 16 taps. The ≥1M-sample run is therefore executed at `NTAPS_MAX = 16`,
with the full 64-wide datapath covered separately by the complete 12-test suite. **Both figures
are reported with their tap count attached** — "1M samples" without saying at what width would be
the kind of unqualified claim this project exists to avoid.

**Tradeoffs.** A reviewer may reasonably want ≥1M samples at full width. The honest answer is
that Icarus cannot deliver it in reasonable time; Verilator (Phase 6) is perhaps 10–100× faster
on this kind of datapath and is the right tool to revisit it with. Recorded as a Phase 6
follow-up rather than papered over.

---

*Entries D-027 onward will be added during Phase 4.*
