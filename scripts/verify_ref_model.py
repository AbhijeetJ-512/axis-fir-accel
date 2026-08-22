#!/usr/bin/env python3
"""Cross-check the FIR reference model against floating-point convolution.

WHY THIS EXISTS
---------------
The DUT is compared BIT-EXACTLY against tb/cocotb/model/fir_ref.py. That makes
the model the single point of failure for the whole Phase 3 result: if the model
is wrong in the same way the RTL is wrong, the regression passes and proves
nothing.

So the model is checked a second, independent way -- against a plain float
convolution written directly from the mathematical definition, sharing no code
with the model. This cannot establish bit-exactness (floats cannot), but it
catches every gross error: a wrong shift amount, a doubled centre tap in the
symmetric path, a sign-extension error, an off-by-one in tap indexing.

EXPECTED RESULT
---------------
With convergent (round-to-nearest-even) rounding the model must agree with the
float result to strictly better than 0.5 output LSB -- that is the definition of
correct round-to-nearest. A worst case at ~0.4999 LSB is therefore not merely
"close enough": it is exactly as close as correct rounding permits, and a value
above 0.5 would indicate a real defect.

Run:  python3 scripts/verify_ref_model.py
Exit: 0 if the model is sane, 1 otherwise.
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tb", "cocotb"))

from model.fir_ref import (RND_CONVERGENT, SAT_ASYMMETRIC, FirRef)  # noqa: E402

TRIALS = 400
SAMPLES = 40
SEED = 2024
LSB_LIMIT = 0.5          # correct round-to-nearest can never exceed this


def main() -> int:
    rng = random.Random(SEED)
    worst, worst_case, checked, skipped = 0.0, None, 0, 0

    for _ in range(TRIALS):
        n = rng.randint(1, 32)
        symmetric = rng.random() < 0.5

        # Bound the filter gain so outputs stay in range; a saturated sample
        # tells us nothing about rounding and would mask a real disagreement.
        cmax = max(1, 26000 // n)
        if symmetric:
            half = (n + 1) // 2
            first = [rng.randrange(-cmax, cmax + 1) for _ in range(half)]
            h = [first[min(k, n - 1 - k)] for k in range(n)]
        else:
            h = [rng.randrange(-cmax, cmax + 1) for _ in range(n)]

        ref = FirRef()
        ref.configure(ntaps=n, coeffs=h, round_mode=RND_CONVERGENT,
                      sat_mode=SAT_ASYMMETRIC, symmetric=int(symmetric))

        hist = [0.0] * max(n, 1)
        for i in range(SAMPLES):
            x = rng.randrange(-32768, 32768)
            y, sat = ref.push(x)

            # Independent float convolution, same zero-padded start-up.
            hist = [x / 32768.0] + hist[:-1]
            y_float = sum((h[k] / 32768.0) * hist[k] for k in range(n))

            if sat:
                skipped += 1
                continue
            err_lsb = abs(y / 32768.0 - y_float) * 32768.0
            checked += 1
            if err_lsb > worst:
                worst, worst_case = err_lsb, (n, symmetric, i)

    print(f"cross-checked {checked} outputs over {TRIALS} random filters "
          f"({skipped} saturated, skipped)")
    print(f"worst |model - float| = {worst:.4f} output LSB "
          f"(N={worst_case[0]}, symmetric={worst_case[1]})")
    ok = worst < LSB_LIMIT
    print("RESULT:", "model agrees with float to within half an LSB -- sane"
          if ok else f"*** worst error {worst:.4f} exceeds {LSB_LIMIT} LSB ***")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
