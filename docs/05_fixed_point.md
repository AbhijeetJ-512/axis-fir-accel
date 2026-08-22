# 05 — Fixed-Point Numerical Model

**IP:** `axis_fir_accel`
**Revision:** 1.0 · Phase 3
**Status:** Implemented and verified bit-exact against `tb/cocotb/model/fir_ref.py`.

This document is the numerical contract. Every width in the RTL is derived here, and every
derivation is backed by an executable check — `scripts/verify_acc_width.py` for the static
argument, and the Phase 3 regression for the dynamic one.

---

## 1. Notation

A signed **Q*m*.*f*** value uses *m* integer bits (including the sign) and *f* fractional bits,
occupying *m + f* bits total. Its numerical value is the two's-complement integer divided by 2^*f*:

```
        value  =  raw / 2^f          raw in [ -2^(m+f-1), 2^(m+f-1) - 1 ]
```

All arithmetic in the RTL is **integer arithmetic on `raw`**. The Q-format is bookkeeping that
lives in this document and in the width parameters; no scaling operation appears in the hardware
except the single deliberate shift in `round_sat`.

---

## 2. The Formats

| Signal | Format | Width | Range | Resolution |
|---|---|---|---|---|
| `x[n]` input sample | **Q1.15** | `DATA_W` = 16 | `[-1.0, +0.999969…]` | 2⁻¹⁵ ≈ 3.05e-5 |
| `h[k]` coefficient | **Q1.15** | `COEF_W` = 16 | `[-1.0, +0.999969…]` | 2⁻¹⁵ |
| pre-adder operand | **Q2.15** | `DATA_W+1` = 17 | `[-2.0, +1.999938…]` | 2⁻¹⁵ |
| `h[k]·operand` product | **Q3.30** | 33 | exact | 2⁻³⁰ |
| accumulator | **Q8.30** | `ACC_W` = 38 | `[-128.0, +128.0)` | 2⁻³⁰ |
| `y[n]` output sample | **Q1.15** | `DATA_W` = 16 | `[-1.0, +0.999969…]` | 2⁻¹⁵ |

**Why Q1.15.** It is the near-universal sample format in audio and communications DSP. It matches
a left-justified 12–16 bit ADC, it makes a 16×16 signed multiply the natural primitive that both
standard-cell libraries and FPGA DSP slices implement efficiently, and it places unity at the top
of the representable range so that a filter with unity DC gain has coefficients summing to just
under 1.0. Choosing an unusual width would complicate every tool interface while demonstrating
nothing.

**Note that +1.0 is not representable.** The maximum positive value is `32767/32768`. This is why
an impulse of `x = 32767` through a filter with `h[0] = 1000` produces `999`, not `1000` — the
input is 0.99997, not 1.0. This is correct Q1.15 behaviour, not a rounding defect, and the
regression asserts the exact value.

---

## 3. The Mathematical Model

### 3.1 Direct form

```
                 N-1
        y[n]  =  SUM  h[k] · x[n-k]
                 k=0
```

with `N = CFG.NTAPS`, `1 ≤ N ≤ NTAPS_MAX`. The delay line is zero-initialised, so for `n < N-1`
the sum is over a zero-padded history — the transient response required by FR-04.

In raw integer terms, with `X = x·2¹⁵` and `H = h·2¹⁵`:

```
                 N-1
        ACC   =  SUM  H[k] · X[n-k]            (exact, Q8.30)
                 k=0

        Y     =  saturate( round( ACC / 2¹⁵ ) )    (Q1.15)
```

### 3.2 Symmetric (linear-phase) form

A linear-phase FIR has `h[k] = h[N-1-k]`. Factoring the common coefficient:

```
                H-1
        y[n] =  SUM  h[k] · ( x[n-k] + x[n-(N-1-k)] ),      H = ceil(N/2)
                k=0
```

which halves the number of multiplications. **For odd N the centre tap `k = (N-1)/2` satisfies
`N-1-k = k`**, so the bracket would become `2·x[n-k]` and count the centre sample twice. The
correct form treats that term separately:

```
                  H-1
        y[n] =    SUM   h[k]·( x[n-k] + x[n-(N-1-k)] )     for k with N-1-k != k
                  k=0
                + h[c]· x[n-c]                              for c = (N-1)/2 when N is odd
```

`fir_core` detects `far_idx == g` and selects the near tap alone. This is the single most likely
silent-wrong-answer bug in the datapath, and `test_symmetric_identity` targets it directly by
requiring bit-identical results from the symmetric and asymmetric paths across odd *and* even
lengths from 1 to 64.

Symmetric mode reads only the first `H` coefficients. If the loaded set is **not** actually
symmetric, the result legitimately differs from asymmetric mode — that is the defined meaning of
the mode, not a defect.

---

## 4. Accumulator Width — Derivation

This is the central numerical claim of the IP: **the accumulator cannot overflow, by
construction.**

### 4.1 Bound for the direct form

Operands are signed `DATA_W` and `COEF_W` values, so

```
        |X|  ≤  2^(DATA_W-1)  = 2¹⁵          (attained at X = -32768)
        |H|  ≤  2^(COEF_W-1)  = 2¹⁵
```

The largest-magnitude product comes from **two negative extremes**, not from two positives:

```
        (-2¹⁵) · (-2¹⁵) = +2³⁰      vs      (2¹⁵-1)² = 2³⁰ - 2¹⁶ + 1
```

so `|H·X| ≤ 2³⁰`, and over `N ≤ NTAPS_MAX` terms:

```
        |ACC|  ≤  NTAPS_MAX · 2³⁰
```

For `NTAPS_MAX = 64` that is `2⁶ · 2³⁰ = 2³⁶`. A signed `W`-bit value represents
`[-2^(W-1), 2^(W-1)-1]`, so we need `2^(W-1) - 1 ≥ 2³⁶`, i.e. `W ≥ 38`.

```
        ACC_W  =  DATA_W + COEF_W + ceil(log2(NTAPS_MAX))
```

**The bound is tight.** At `W = 37` the range top is `2³⁶ - 1`, which falls short of the
worst-case `2³⁶` by exactly one LSB. The derivation is therefore not merely safe — it is
*minimal*, and `scripts/verify_acc_width.py` asserts both properties for every supported
`NTAPS_MAX` from 4 to 256.

### 4.2 The symmetric form needs no extra bits

This is not obvious and was verified rather than assumed. The pre-adder widens each operand by
one bit, so each product can reach `2³¹` instead of `2³⁰` — but there are only `ceil(N/2)`
products instead of `N`. The two effects cancel **exactly**:

```
        asymmetric:   N        · 2³⁰  =  N · 2³⁰
        symmetric:    ceil(N/2) · 2³¹  =  N · 2³⁰        (N even)
```

so the same `ACC_W` covers both datapaths. `verify_acc_width.py` checks this equality directly
and fails if it ever ceases to hold.

### 4.3 Consequence

`acc_reduce` contains **no saturation logic and no overflow flag, and needs none.** The entire
numerical failure surface of the IP collapses into one clearly-named module, `round_sat`. That is
the practical payoff of deriving a width instead of picking a round number and bolting on
saturation "to be safe".

**Measured evidence.** Under the extreme-operand stimulus (all inputs at `-2^(W-1)`, all
coefficients at `-2^(W-1)`), the peak observed `|ACC|` reaches exactly the predicted worst case
and never exceeds it. The regression reports the peak as a percentage of the representable range
on every run.

---

## 5. Output Narrowing

The accumulator holds 30 fractional bits; the output keeps 15. `SHIFT = COEF_W - 1 = 15`
fractional bits are discarded.

### 5.1 Rounding

All three modes are one predicate applied to the same floored value. Let

```
        floored = ACC >>> SHIFT           arithmetic shift right == floor, exactly
        guard   = ACC[SHIFT-1]            the one-half bit
        sticky  = |ACC[SHIFT-2:0]         anything strictly below one half
        lsb     = ACC[SHIFT]              parity of `floored`
```

| Mode | `inc` | Behaviour | Bias |
|---|---|---|---|
| `RND_TRUNC` (0) | `0` | floor | toward −∞ |
| `RND_HALF_UP` (1) | `guard` | ties away from −∞ | toward +∞ |
| `RND_CONVERGENT` (2) | `guard & (sticky \| lsb)` | ties to **even** | **none** |
| `RND_RESERVED` (3) | — | illegal, raises `CFG_ERR` | — |

`rounded = floored + inc`.

**Why convergent matters.** Truncation and round-half-up both inject a DC offset of about half an
LSB on every sample. Through a filter chain that offset accumulates and appears as a spurious
tone at DC rather than as noise — a real, audible/measurable artefact, not a theoretical one.
Round-half-to-even is unbiased because ties are split evenly between rounding up and down.

*Worked example*, `SHIFT = 15`, so one LSB of output is 2¹⁵ accumulator counts. An accumulator of
`1.5 LSB` (raw 49152) and of `2.5 LSB` (raw 81920):

| ACC (LSBs) | TRUNC | HALF_UP | CONVERGENT |
|---|---|---|---|
| +1.5 | 1 | 2 | **2** (2 is even) |
| +2.5 | 2 | 3 | **2** (2 is even) |
| −1.5 | −2 | −1 | **−2** (−2 is even) |
| −2.5 | −3 | −2 | **−2** (−2 is even) |

`test_rounding_modes_differ` asserts that the three modes actually produce different output on
tie-hitting stimulus — a suite where every mode passes is worthless if the stimulus never
distinguished them.

### 5.2 Saturation

`rounded` is `ACC_W - SHIFT + 1 = 24` bits and must be clamped to `DATA_W = 16`:

| Mode | Range | Rationale |
|---|---|---|
| `SAT_ASYMMETRIC` (0) | `[-32768, +32767]` | full two's-complement range |
| `SAT_SYMMETRIC` (1) | `[-32767, +32767]` | `-y` is always representable |

Symmetric saturation exists because `-(-32768)` is not representable in 16 bits, so any
downstream negation, subtraction or magnitude operation on an asymmetrically-saturated sample can
itself overflow. Costing one comparator here removes that hazard from every consumer.

Saturation sets a **sticky** `STATUS.SAT_STICKY` bit and, if enabled, raises `IRQ_STATUS.SAT_EVENT`.
It is a *numerical* condition, not a protocol error: the stream continues uninterrupted. Halting a
filter because one sample clipped would make the IP unusable with real signal levels.

---

## 6. Correspondence Between Model and RTL

| Mathematical step | RTL | Reference model |
|---|---|---|
| `x[n-k]` history | `delay_line.sv` | `FirRef._tap` |
| pre-add / centre-tap | `fir_core.sv` `g_prep` | `FirRef.accumulate` |
| `h[k] · operand` | `mac_array.sv` | `self.coeffs[k] * operand` |
| Σ | `acc_reduce.sv` | `acc +=` |
| `round(ACC / 2^SHIFT)` | `round_sat.sv` | `FirRef.round_shift` |
| clamp | `round_sat.sv` | `FirRef.saturate` |

The model uses Python's arbitrary-precision integers, so it has no width artefacts of its own and
comparison is **exact equality**. It also re-checks the `ACC_W` bound on every sample
(`check_acc_fits`), so a stimulus that would break the derivation is caught in the model as well
as in the DUT — the two cannot silently agree on a wrong answer.

---

## 7. Verification Status

| Property | Status | Test |
|---|---|---|
| Impulse response equals coefficients | verified | `test_impulse_response` |
| Boxcar / moving average closed form | verified | `test_boxcar_moving_average` |
| Extreme operands (±full scale) | verified | `test_extreme_operands` |
| Rounding at exact ties, all 3 modes | verified | `test_rounding_boundaries` |
| The 3 rounding modes are distinguishable | verified | `test_rounding_modes_differ` |
| Saturation at exact clamp points, both modes | verified | `test_saturation_boundaries` |
| Symmetric ≡ asymmetric, odd and even N | verified | `test_symmetric_identity` |
| Every tap count 1…`NTAPS_MAX` | verified | `test_ntaps_sweep` |
| Zero-padded history after flush | verified | `test_flush_zeroes_history` |
| `LATENCY` and `ACC_W` match derivation | verified | `test_latency_and_widths` |
| Accumulator never exceeds derived bound | verified | every test; peak reported |
| ≥1M random samples, ≥20 sets, 6 mode pairs | verified | `test_random_exhaustive` |

Reproduce with:

```bash
make fir
```
