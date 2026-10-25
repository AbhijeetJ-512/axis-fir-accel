#!/usr/bin/env python3
"""check_equivalent.py - check with Yosys whether a mutant that survived the campaign differs from the original top level.

    check_equivalent.py Q12 Q17 [--method induct|bmc] [--depth 20] [--out results/sva/equivalence.txt]

A survivor is either a gap in the verification or an edit that changes nothing observable. Reading the RTL gives a hypothesis; this script tests it
against the netlist. The whole axis_fir_top is elaborated twice (original, and the mutated copy of rtl/), flattened and mapped to flip-flops and gates.

  induct (default)  an unbounded proof by induction. Every register and every port of the two designs is paired by name (`equiv_make`); nothing else is
                    paired, because a difference between two internal combinational wires is not observable by itself and the induction would otherwise
                    assume such pairs equal while proving the registers. `equiv_simple` and `equiv_induct` then try to prove every pair equal, i.e. the
                    two designs have the same outputs and the same next register values for every state and every input. Verdict PROVED EQUIVALENT only if
                    every pair is proved; anything else is NOT PROVED (which does not by itself mean different).
  bmc               a bounded search: `miter -equiv` and Yosys' SAT engine look for an input sequence of up to DEPTH cycles from the reset state (all
                    flip-flops zero, reset asserted for the first four cycles) that makes any output differ. DIFFERENT = such a sequence exists;
                    NO DIFFERENCE = none within DEPTH cycles (a bounded result, not a proof).

The method is tested by running it on mutants that do change behaviour (they must not come out PROVED / NO DIFFERENCE). The proof is for one small parameter set
(the edits listed as equivalent are in control logic that does not depend on the sizes); the result file says which.
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sva_mutants as SM  # noqa: E402

ROOT = SM.ROOT
FILES = ["common/fir_pkg.sv", "common/reset_sync.sv", "axi_lite/axil_slave.sv", "axi_stream/axis_fifo.sv", "control/credit_ctr.sv", "control/csr_regfile.sv",
         "dsp/delay_line.sv", "dsp/mac_array.sv", "dsp/acc_reduce.sv", "dsp/round_sat.sv", "dsp/fir_core.sv", "control/fir_ctrl.sv", "mem/coeff_bank.sv", "top/axis_fir_top.sv"]
PARAMS = dict(DATA_W=4, COEF_W=4, NTAPS_MAX=2, MAC_UNITS=1, IN_FIFO_DEPTH=2, OUT_FIFO_DEPTH=8, SYMMETRIC_EN=1)   # OUT_FIFO_DEPTH must exceed the core latency (top-level check)


def design(rtl_dir, name, keep_regs):
    # both trees are read through the same relative path (a symbolic link switched between the two reads), so that source paths inside names agree
    link = "build/equiv/cur"
    rd = "exec -q -- ln -sfn %s %s\nread_verilog -sv -I %s/common %s" % (rtl_dir, link, link, " ".join("%s/%s" % (link, f) for f in FILES))
    P = PARAMS
    # the parameters are set the way scripts/elab_sweep.sh sets them (chparam before any `hierarchy -top`, which would discard the unparameterised modules)
    ch = ["chparam -set NTAPS_MAX %d -set MAC_UNITS %d -set DATA_W %d -set COEF_W %d -set SYMMETRIC_EN %d csr_regfile" % (P["NTAPS_MAX"], P["MAC_UNITS"], P["DATA_W"], P["COEF_W"], P["SYMMETRIC_EN"]),
          "chparam -set DATA_W %d -set COEF_W %d -set NTAPS_MAX %d -set SYMMETRIC_EN %d -set MAC_UNITS %d fir_core" % (P["DATA_W"], P["COEF_W"], P["NTAPS_MAX"], P["SYMMETRIC_EN"], P["MAC_UNITS"]),
          "chparam -set NTAPS_MAX %d -set MAC_UNITS %d fir_ctrl" % (P["NTAPS_MAX"], P["MAC_UNITS"]),
          "chparam -set NTAPS_MAX %d -set COEF_W %d coeff_bank" % (P["NTAPS_MAX"], P["COEF_W"]),
          "chparam " + " ".join("-set %s %s" % kv for kv in P.items()) + " axis_fir_top"]
    steps = [rd] + ch + ["hierarchy -check -top axis_fir_top", "proc", "flatten", "memory_map",
                         "select -assert-none t:axil_slave t:axis_fifo t:coeff_bank t:credit_ctr t:csr_regfile t:fir_core t:fir_ctrl t:reset_sync %u"]   # the check must see the logic inside
    if keep_regs:
        # name the outputs of every flip-flop before the resets and enables are unmapped, then hide every other internal name
        steps += ["select -set ffs t:$dff t:$adff t:$dffe t:$adffe t:$sdff t:$sdffe t:$dffsr %u", "select -set regq @ffs %co:+[Q] w:* %i"]
    steps += ["async2sync", "dffunmap", "opt_clean"]
    if keep_regs:
        steps += ["select -set keep @regq x:* %u", "rename -hide w:* @keep %d", "opt_clean"]
    return "\n".join(steps + ["rename axis_fir_top %s" % name, "design -stash %s" % name, ""])


def run_one(mid, d, work, method, depth):
    ys = os.path.join(work, mid + ".ys")
    log = os.path.join(work, mid + ".log")
    sat = os.path.join(work, mid + ".sat.txt")
    if os.path.exists(sat):
        os.remove(sat)
    keep = method == "induct"
    with open(ys, "w") as f:
        f.write(design(os.path.join(ROOT, "rtl"), "gold", keep) + design(d, "gate", keep))
        f.write("design -copy-from gold -as gold gold\ndesign -copy-from gate -as gate gate\n")
        if keep:
            f.write("equiv_make gold gate equiv\nhierarchy -top equiv\nequiv_simple -seq 4\nequiv_induct -seq 4\nequiv_status -assert\n")
        else:
            f.write("miter -equiv -flatten -make_outputs gold gate miter\nhierarchy -top miter\nflatten\nopt_clean\n")
            resets = " ".join("-set-at %d in_aresetn 0" % k for k in range(1, 5))
            f.write("tee -o %s sat -seq %d -set-init-zero %s -prove trigger 0 -verify -show-inputs miter\n" % (sat, depth, resets))
    r = subprocess.run(["yosys", "-q", "-l", log, "-s", ys], capture_output=True, text=True, cwd=ROOT)
    text = open(log).read()
    if method == "induct":
        if r.returncode == 0:
            n = [l.strip() for l in text.splitlines() if "are proven and 0 are unproven" in l]
            return "PROVED EQUIVALENT", (n[-1] if n else "")
        unp = [l.strip() for l in text.splitlines() if "are proven and" in l]
        return "NOT PROVED", (unp[-1] if unp else "yosys exit %d" % r.returncode)
    out = open(sat).read() if os.path.exists(sat) else ""
    if r.returncode == 0 and "SUCCESS" in out:
        return "NO DIFFERENCE within %d cycles" % depth, ""
    if "FAIL" in out or "Failed to prove" in text:
        return "DIFFERENT (a distinguishing input sequence exists)", ""
    return "INCONCLUSIVE (yosys exit %d)" % r.returncode, ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--method", choices=["induct", "bmc"], default="induct")
    ap.add_argument("--depth", type=int, default=20)
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "sva", "equivalence.txt"))
    a = ap.parse_args()
    work = os.path.join(ROOT, "build", "equiv")
    os.makedirs(work, exist_ok=True)
    byid = {m[0]: m for m in SM.MUTANTS}
    lines = ["# Yosys 0.33 check of mutants against the original axis_fir_top (flattened): method %s" % (
        "induct: every register and port paired by name, equiv_simple + equiv_induct (an unbounded proof when every pair is proved)" if a.method == "induct" else
        "bmc: miter + sat -seq %d from the all-zero state with reset for 4 cycles (bounded)" % a.depth),
        "# parameters: " + ", ".join("%s=%s" % kv for kv in PARAMS.items()), ""]
    for mid in a.ids:
        d, err = SM.apply(byid[mid], work)
        if err:
            sys.exit("%s: %s" % (mid, err))
        verdict, detail = run_one(mid, d, work, a.method, a.depth)
        lines.append("%s  %-50s %s" % (mid, verdict, byid[mid][4]))
        if detail:
            lines.append("      " + detail)
    open(a.out, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
