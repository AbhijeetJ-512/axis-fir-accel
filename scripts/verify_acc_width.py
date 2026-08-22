#!/usr/bin/env python3
"""Verify the accumulator width derivation of docs/01_requirements.md NR-04/NR-05.

Claim under test
----------------
    ACC_W = DATA_W + COEF_W + ceil(log2(NTAPS_MAX))

is SUFFICIENT (no signed overflow is possible for any legal operand sequence),
MINIMAL (ACC_W - 1 bits would be insufficient), and covers BOTH the asymmetric
datapath and the symmetric (linear-phase pre-adder) datapath.

Both operands are signed Q(1).(W-1), so the extremal values are -2^(W-1) and
+2^(W-1)-1. The worst-case accumulator magnitude arises from NTAPS_MAX products
at their extremes; note the positive extreme comes from (-2^15)*(-2^15) = +2^30,
which is larger in magnitude than any product of two positive operands.

Run:  python3 scripts/verify_acc_width.py
Exit: 0 if the derivation holds for every supported NTAPS_MAX, 1 otherwise.
"""
import sys

DATA_W = 16
COEF_W = 16
SUPPORTED_NTAPS_MAX = (4, 8, 16, 32, 64, 128, 256)


def clog2(n: int) -> int:
    return (n - 1).bit_length()


def signed_range(width: int) -> tuple[int, int]:
    return -(2 ** (width - 1)), 2 ** (width - 1) - 1


def check(ntaps_max: int, data_w: int = DATA_W, coef_w: int = COEF_W) -> bool:
    acc_w = data_w + coef_w + clog2(ntaps_max)

    x_lo, x_hi = signed_range(data_w)
    h_lo, h_hi = signed_range(coef_w)
    products = [a * b for a in (x_lo, x_hi) for b in (h_lo, h_hi)]

    max_sum = ntaps_max * max(products)
    min_sum = ntaps_max * min(products)

    # SYMMETRIC mode uses a pre-adder, so each operand is one bit wider -- but
    # there are only ceil(N/2) products instead of N. Those two effects cancel
    # exactly, so the same ACC_W covers both modes. Verified, not assumed:
    # a wrong answer here would silently corrupt every linear-phase filter.
    pair_lo, pair_hi = 2 * x_lo, 2 * x_hi
    sym_products = [p * h for p in (pair_lo, pair_hi) for h in (h_lo, h_hi)]
    half = (ntaps_max + 1) // 2
    sym_max = half * max(sym_products)
    sym_min = half * min(sym_products)
    max_sum = max(max_sum, sym_max)
    min_sum = min(min_sum, sym_min)

    fits_lo, fits_hi = signed_range(acc_w)
    sufficient = fits_lo <= min_sum and max_sum <= fits_hi

    tight_lo, tight_hi = signed_range(acc_w - 1)
    minimal = not (tight_lo <= min_sum and max_sum <= tight_hi)

    same = (sym_max == ntaps_max * max(products))
    status = "PASS" if (sufficient and minimal and same) else "FAIL"
    print(
        f"[{status}] NTAPS_MAX={ntaps_max:4d}  ACC_W={acc_w:2d}  "
        f"sum=[{min_sum}, {max_sum}]  repr=[{fits_lo}, {fits_hi}]  "
        f"sufficient={sufficient}  minimal={minimal}  sym==asym={same}"
    )
    return sufficient and minimal and same


def main() -> int:
    print(f"Verifying ACC_W = {DATA_W} + {COEF_W} + clog2(NTAPS_MAX)\n")
    ok = all(check(n) for n in SUPPORTED_NTAPS_MAX)
    print("\nRESULT:", "derivation holds" if ok else "DERIVATION BROKEN")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
