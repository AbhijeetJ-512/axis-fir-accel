#!/usr/bin/env python3
"""run_sva.py - run every cocotb suite configuration under Verilator with the bound checkers and gate on the result.

    run_sva.py [--quick] [--jobs N] [--out results/sva]

Writes results/sva/{runs.txt, cover_bins.txt, vacuity.txt, summary.txt}. Exit status 1 if any of:
  * a cocotb test fails, or any assertion fires, in any run (the unmodified RTL must pass everything);
  * a hand-written cover bin was never hit in any run (and is not listed with a reason in verif/sva_exclusions.txt);
  * the antecedent of a conditional assertion was never met in any run (the assertion passed vacuously; same exclusion file);
  * a stale exclusion (listed, but the bin or antecedent was in fact hit).
Exit status 2 if the run itself is unusable (build error, no coverage data).
--quick runs only the four default-parameter suites (`make regress` equivalents) and gates nothing but pass / fail.
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sva_lib as L  # noqa: E402

ROOT = L.ROOT
CHK = os.path.join(ROOT, "verif", "fir_checkers.sv")
EXCL = os.path.join(ROOT, "verif", "sva_exclusions.txt")


def read_exclusions():
    ex = {}
    if os.path.exists(EXCL):
        for n, line in enumerate(open(EXCL), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            lab, _, why = line.partition(" ")
            if not why.strip():
                sys.exit("%s:%d: an exclusion needs a reason" % (EXCL, n))
            ex[lab] = why.strip()
    return ex


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "sva"))
    a = ap.parse_args()
    work = os.path.join(ROOT, "build", "sva")
    os.makedirs(work, exist_ok=True)
    os.makedirs(a.out, exist_ok=True)

    vac = os.path.join(work, "fir_checkers_vac.sv")
    msg = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "gen_vacuity.py"), CHK, vac], capture_output=True, text=True).stdout.strip()
    cfgs = [c for c in L.CONFIGS if not a.quick or c[0] in L.QUICK]
    res = L.run_matrix(cfgs, os.path.join(work, "runs"), jobs=a.jobs, checkers=vac)

    lines = ["# one line per (suite, parameter set): cocotb summary, then any assertion that fired", ""]
    bad = False
    for r in res:
        if r["tests"] is None:
            lines.append("%-24s BUILD / RUN ERROR (see %s)" % (r["tag"], os.path.relpath(r["log"], ROOT)))
            bad = True
            continue
        status = "ok" if r["ok"] else "FAILED"
        lines.append("%-24s %-6s tests %d, pass %d, fail %d   assertions fired: %s" % (
            r["tag"], status, r["tests"], r["pass"], r["fail"], ", ".join("%s x%d" % kv for kv in sorted(r["fired"].items())) or "none"))
        bad |= not r["ok"]
    open(os.path.join(a.out, "runs.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    if any(r["tests"] is None for r in res):
        print("run_sva: a run could not be built or run; nothing else is meaningful")
        return 2
    if a.quick:
        return 1 if bad else 0

    tot = L.merge_cov(res)
    if not tot:
        print("run_sva: no cover data in any run (coverage.dat missing)")
        return 2
    agg, inst = L.by_label(tot)
    mod_of = L.module_of(CHK)
    excl = read_exclusions()
    asserts = L.assertion_labels(CHK)
    conditional = sorted(k[1][len("c_ante_"):] for k in agg if k[1].startswith("c_ante_"))
    hand = sorted((m, lab) for (m, lab) in agg if lab.startswith("c_") and not lab.startswith("c_ante_"))

    problems = []
    with open(os.path.join(a.out, "cover_bins.txt"), "w") as out:
        out.write("# hits over every run and instance  checker module  label   (instances that carry the bin)\n")
        for m, lab in hand:
            h = agg[(m, lab)]
            out.write("%10d  %-16s %-18s (%d instance%s)\n" % (h, m, lab, len(inst[(m, lab)]), "s" if len(inst[(m, lab)]) != 1 else ""))
            if h == 0 and lab not in excl:
                problems.append("ZERO-HIT COVER %s.%s" % (m, lab))
    with open(os.path.join(a.out, "vacuity.txt"), "w") as out:
        out.write("# antecedent hits per conditional assertion (0 = the assertion passed vacuously in every run)\n")
        for name in conditional:
            ms = [m for (m, lab) in agg if lab == "c_ante_" + name]
            h = sum(agg[(m, "c_ante_" + name)] for m in ms)
            out.write("%10d  %-16s a_%s\n" % (h, ms[0] if ms else "?", name))
            if h == 0 and ("a_" + name) not in excl:
                problems.append("VACUOUS ASSERTION a_%s (antecedent never met)" % name)
    for lab, why in sorted(excl.items()):
        hit = any(h > 0 for (m, l), h in agg.items() if l == lab or l == "c_ante_" + lab[2:])
        if hit:
            problems.append("STALE EXCLUSION %s (%s) was hit; remove the exclusion" % (lab, why))
    defined_ante = {lab for lab in asserts if lab.startswith("a_")}
    n_hand_hit = sum(1 for k in hand if agg[k] > 0)
    n_ante_hit = sum(1 for n in conditional if any(agg[(m, "c_ante_" + n)] > 0 for (m, l) in agg if l == "c_ante_" + n))
    with open(os.path.join(a.out, "summary.txt"), "w") as out:
        out.write("FIR accelerator: bound checkers under cocotb + Verilator 5.052 (%d runs, %s)\n" % (len(res), "unmodified RTL"))
        out.write("runs                      : %d, all tests passed and no assertion fired: %s\n" % (len(res), "yes" if not bad else "NO"))
        out.write("assertions defined        : %d labelled (%d concurrent, %d procedural scoreboard checks)\n" % (
            len(asserts), sum(1 for x in asserts if x.startswith("a_")), sum(1 for x in asserts if x.startswith("sb_"))))
        out.write("conditional assertions    : %d, antecedent reached in at least one run: %d\n" % (len(conditional), n_ante_hit))
        out.write("cover bins                : %d, hit: %d, zero: %d (excluded with a reason: %d)\n" % (
            len(hand), n_hand_hit, len(hand) - n_hand_hit, sum(1 for k in hand if agg[k] == 0 and k[1] in excl)))
        out.write("note                      : %s\n" % msg)
        out.write("problems                  : %s\n" % ("none" if not problems and not bad else "; ".join(problems) or "a run failed"))
    print(open(os.path.join(a.out, "summary.txt")).read())
    for p in problems:
        print("PROBLEM", p)
    return 1 if (problems or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
