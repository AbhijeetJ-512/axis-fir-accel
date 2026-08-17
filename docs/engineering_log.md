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

*Entries D-012 onward will be added during Phase 1.*
