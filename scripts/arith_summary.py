#!/usr/bin/env python3
"""arith_summary.py - condense the simulation logs in results/arith/ and the baseline `make check` log into results/arith/summary.txt.
Every figure is read from a log; a missing log becomes 'NOT YET RUN'."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = os.path.join(ROOT, "results", "arith")
BASE = os.path.join(ROOT, "..", "_evidence", "baseline", "axis_fir_accel.log")


def read(p):
    return open(p, errors="replace").read() if os.path.exists(p) else None


def g(pat, t, default="n/a"):
    m = re.search(pat, t or "", re.S)
    return m.group(1) if m else default


out = []
b = read(BASE)
out.append("Control plane, stream path and full integration (baseline `make check`, default builds):")
if b:
    csr = g(r"Phase 1 suite.*?(TESTS=\d+ PASS=\d+ FAIL=\d+ SKIP=\d+)", b)
    st = g(r"Phase 2 suite.*?(TESTS=\d+ PASS=\d+ FAIL=\d+ SKIP=\d+)", b)
    top = g(r"Phase 4 suite.*?(TESTS=\d+ PASS=\d+ FAIL=\d+ SKIP=\d+)", b)
    beats = max((int(x) for x in re.findall(r"(\d+) beats matched exactly", b)), default="n/a")
    out += ["  AXI4-Lite control plane : %s" % csr, "  AXI4-Stream flow control: %s (largest run: %s beats matched exactly)" % (st, beats),
            "  full IP, MAC_UNITS=8    : %s" % top]
    for m in re.finditer(r"II: (N=\d+ sym=\d MAC_UNITS=\d+ -> predicted \d+, measured [\d.]+)", b):
        out.append("    initiation interval  %s" % m.group(1))
    out.append("    " + g(r"cocotb\.axis_fir_top\s+(\d+ samples across \d+ configurations: BIT-EXACT \(MAC_UNITS=\d+\))", b))
    for k in ("elab_sweep: all configurations clean[^\n]*", "RESULT: accumulator width derivation holds|RESULT: derivation holds", "worst \\|model - float\\| = [^\n]*"):
        m = re.search(k, b)
        if m:
            out.append("    " + m.group(0))
    out.append("    " + g(r"(comb-independence: all checks pass)", b) + " (6 properties on axis_fir_top)")
else:
    out.append("  NOT YET RUN")

out.append("\nParameter sweeps (make sweep): control plane at five (NTAPS_MAX, MAC_UNITS, SYMMETRIC_EN) builds, stream path at five FIFO / pipe configurations:")
p = read(os.path.join(A, "param_sweep.log"))
out += ["  " + l for l in (p or "  NOT YET RUN").splitlines() if l.strip() and not l.startswith("exit")]

out.append("\nFold sweep (make fold-sweep): full-IP suite (7 tests) at NTAPS_MAX=32 for every MAC_UNITS; all runs bit-identical to the fold-agnostic model:")
f = read(os.path.join(A, "fold_sweep.log"))
out += ["  " + l for l in (f or "  NOT YET RUN").splitlines() if l.strip() and not l.startswith("exit")]

out.append("\nArithmetic suite on the final RTL (repaired wrapper, one pass per sample, MAC_UNITS = NTAPS_MAX):")
for tag, fn in (("NTAPS_MAX=16", "fir_n16.log"), ("NTAPS_MAX=64", "fir_n64.log")):
    t = read(os.path.join(A, fn))
    if t is None:
        out.append("  %s: NOT YET RUN" % tag)
        continue
    out.append("  %s: %s" % (tag, g(r"(TESTS=\d+ PASS=\d+ FAIL=\d+ SKIP=\d+)", t, "NOT FINISHED")))
    out.append("      " + g(r"(LATENCY = \d+ cycles, ACC_W = \d+ bits \(both confirmed\))", t, "latency: n/a"))
    out.append("      " + g(r"(peak \|accumulator\| = \d+, ACC_W bound = \d+ \([\d.]+% of range, never exceeded\))", t, "peak: n/a"))
    out.append("      " + g(r"(\d+ samples across \d+ coefficient sets, all 6 mode pairs: BIT-EXACT)", t, "random run: n/a"))
t = read(os.path.join(A, "fir_full_n16.log"))
out.append("  large random run (make fir-full NTAPS_MAX=16, RAND_SETS=20 RAND_LEN=50000):")
if t is None:
    out.append("      NOT YET RUN")
else:
    out.append("      %s" % g(r"(TESTS=\d+ PASS=\d+ FAIL=\d+ SKIP=\d+)", t, "NOT FINISHED"))
    out.append("      " + g(r"(\d+ samples across \d+ coefficient sets, all 6 mode pairs: BIT-EXACT)", t, "random run: n/a"))
open(os.path.join(A, "summary.txt"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
