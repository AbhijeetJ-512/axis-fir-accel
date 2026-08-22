"""Bit-exact reference model for axis_fir_accel.

This is the golden reference. It is written in INTEGER arithmetic only -- no
float appears anywhere in this file. Python integers are arbitrary precision, so
the model can mirror the RTL's exact arithmetic without width artefacts of its
own, and comparison against the DUT is exact equality rather than a tolerance.

That property is the reason a FIR was chosen over an FFT (engineering log
D-001): "bit-identical on 10 million samples" is far stronger portfolio evidence
than "within 0.01% of numpy", and it catches whole classes of bug -- an off-by-
one in a rounding predicate, a mis-sign-extended operand, a doubled centre tap --
that a tolerance check would quietly absorb.

The model mirrors rtl/dsp/fir_core.sv and rtl/dsp/round_sat.sv line for line.
Where the two disagree, one of them is wrong and the test says so.
"""

from collections import deque

# Rounding modes -- must match fir_pkg::round_mode_e
RND_TRUNC = 0
RND_HALF_UP = 1
RND_CONVERGENT = 2
RND_RESERVED = 3

# Saturation modes -- must match fir_pkg::sat_mode_e
SAT_ASYMMETRIC = 0
SAT_SYMMETRIC = 1


def to_signed(value: int, width: int) -> int:
    """Interpret the low `width` bits of `value` as two's complement."""
    value &= (1 << width) - 1
    return value - (1 << width) if value & (1 << (width - 1)) else value


def to_unsigned(value: int, width: int) -> int:
    return value & ((1 << width) - 1)


class FirRef:
    """Reference FIR filter.

    Usage:
        ref = FirRef(data_w=16, coef_w=16, ntaps_max=64)
        ref.configure(ntaps=8, coeffs=[...], round_mode=..., sat_mode=..., symmetric=0)
        y, sat = ref.push(x)
    """

    def __init__(self, data_w: int = 16, coef_w: int = 16, ntaps_max: int = 64):
        self.data_w = data_w
        self.coef_w = coef_w
        self.ntaps_max = ntaps_max
        self.shift = coef_w - 1          # fractional bits discarded, == FIR_SHIFT
        self.acc_w = data_w + coef_w + (ntaps_max - 1).bit_length()
        self.ntaps = 1
        self.coeffs = [0] * ntaps_max    # signed
        self.round_mode = RND_TRUNC
        self.sat_mode = SAT_ASYMMETRIC
        self.symmetric = 0
        self.reset()

    # -- configuration --------------------------------------------------------

    def reset(self):
        """Zero the delay line. Coefficients and configuration are untouched,
        exactly as CTRL.SOFT_RESET / CTRL.FLUSH behave in hardware."""
        self.hist = deque([0] * self.ntaps_max, maxlen=self.ntaps_max)

    def configure(self, ntaps=None, coeffs=None, round_mode=None,
                  sat_mode=None, symmetric=None):
        if ntaps is not None:
            self.ntaps = ntaps
        if coeffs is not None:
            # Accept either signed values or the raw unsigned register words.
            self.coeffs = [to_signed(c, self.coef_w) for c in coeffs]
            self.coeffs += [0] * (self.ntaps_max - len(self.coeffs))
        if round_mode is not None:
            self.round_mode = round_mode
        if sat_mode is not None:
            self.sat_mode = sat_mode
        if symmetric is not None:
            self.symmetric = symmetric

    # -- arithmetic -----------------------------------------------------------

    def _tap(self, k: int) -> int:
        """x[n-k]. The hardware delay line is NTAPS_MAX deep and reset to zero,
        so taps beyond the samples pushed so far read as zero -- which is what
        gives the zero-padded start-up response required by FR-04."""
        return self.hist[k] if 0 <= k < self.ntaps_max else 0

    def accumulate(self) -> int:
        """Exact accumulator value, before any narrowing."""
        acc = 0
        if self.symmetric:
            half = (self.ntaps + 1) // 2
            for k in range(half):
                far = self.ntaps - 1 - k
                # Centre tap of an odd-length filter: far == k, so the pair
                # collapses and the tap must be counted ONCE, not doubled.
                operand = self._tap(k) if far == k else self._tap(k) + self._tap(far)
                acc += self.coeffs[k] * operand
        else:
            for k in range(self.ntaps):
                acc += self.coeffs[k] * self._tap(k)
        return acc

    def round_shift(self, acc: int) -> int:
        """Discard `shift` fractional bits. Mirrors round_sat.sv exactly."""
        shift = self.shift
        floored = acc >> shift                      # arithmetic shift == floor
        guard = (acc >> (shift - 1)) & 1
        if self.round_mode == RND_HALF_UP:
            inc = guard
        elif self.round_mode == RND_CONVERGENT:
            sticky = 1 if (acc & ((1 << (shift - 1)) - 1)) else 0
            lsb = floored & 1
            inc = guard & (sticky | lsb)
        else:                                        # TRUNC and RESERVED
            inc = 0
        return floored + inc

    def saturate(self, value: int):
        """Clamp to the sample format. Returns (clamped, saturated_flag)."""
        hi = (1 << (self.data_w - 1)) - 1
        lo = -hi if self.sat_mode == SAT_SYMMETRIC else -(1 << (self.data_w - 1))
        if value > hi:
            return hi, True
        if value < lo:
            return lo, True
        return value, False

    def push(self, x: int):
        """Feed one sample. Returns (y, saturated) with y signed."""
        self.hist.appendleft(to_signed(x, self.data_w))
        acc = self.accumulate()
        self.check_acc_fits(acc)
        return self.saturate(self.round_shift(acc))

    # -- self-check -----------------------------------------------------------

    def check_acc_fits(self, acc: int):
        """The accumulator must never overflow ACC_W -- that is the entire
        claim of NR-05. Checked on every sample here, so that a stimulus which
        would have broken the bound is caught in the MODEL as well as by the
        RTL assertion, rather than silently agreeing with a broken DUT."""
        lo = -(1 << (self.acc_w - 1))
        hi = (1 << (self.acc_w - 1)) - 1
        if not (lo <= acc <= hi):
            raise AssertionError(
                f"reference accumulator {acc} exceeds ACC_W={self.acc_w} "
                f"range [{lo}, {hi}] -- the width derivation is wrong")

    def run(self, samples):
        """Convenience: filter a whole sequence, returning [(y, sat), ...]."""
        return [self.push(x) for x in samples]
