#!/usr/bin/env python3
"""ppa_sweep.py - synthesis, timing and throughput of axis_fir_top across MAC_UNITS (the project's headline trade-off).

    ppa_sweep.py [--nt 64] [--se 1] [--macs 1 2 4 8 16 32] [--out results/ppa/ppa_sweep.md]

Per point: Yosys (timing-driven ABC script, buffering + sizing) -> Sky130 HD, then OpenSTA at a 10 ns base period with constraints/axis_fir_top.sdc.
T_min = 10 ns - WNS(clock path group); verified by re-running STA at +/-2 % of T_min. Throughput = F_max / II where II = ceil(N_eff / MAC_UNITS),
N_eff = NTAPS_MAX/2 for the symmetric build (the formula documented in docs/03_microarchitecture.md and checked by `make fold-sweep`).
Pre-layout: ideal clock, no wires. The asynchronous-reset network is reported separately and excluded (see the SDC header).
"""
import argparse
import concurrent.futures as cf
import math
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = ("rtl/common/fir_pkg.sv rtl/common/reset_sync.sv rtl/axi_lite/axil_slave.sv rtl/axi_stream/axis_fifo.sv rtl/control/credit_ctr.sv "
       "rtl/control/csr_regfile.sv rtl/dsp/delay_line.sv rtl/dsp/mac_array.sv rtl/dsp/acc_reduce.sv rtl/dsp/round_sat.sv rtl/dsp/fir_core.sv "
       "rtl/control/fir_ctrl.sv rtl/mem/coeff_bank.sv rtl/top/axis_fir_top.sv")


def sta(out, period):
    env = dict(os.environ, NETLIST=os.path.join(out, "synth.v"), SDC=os.path.join(ROOT, "constraints", "axis_fir_top.sdc"), CLK_PERIOD_NS=repr(float(period)), OUTDIR=out)
    for f in ("timing.txt", "async.txt", "timing_all.txt"):
        if os.path.exists(os.path.join(out, f)):
            os.remove(os.path.join(out, f))
    r = subprocess.run(["sta", "-no_splash", "-exit", os.path.join(ROOT, "ppa", "sta_template.tcl")], env=env, cwd=ROOT, capture_output=True, text=True)
    t = open(os.path.join(out, "timing.txt")).read()
    m = re.search(r"(-?[\d.]+)\s+slack", t)
    if r.returncode != 0 or not m:
        raise RuntimeError("sta failed: " + (r.stdout + r.stderr)[-300:])
    return float(m.group(1)), t


def point(mu, nt, se):
    out = os.path.join(ROOT, "build", "ppa", "n%d_m%d_s%d" % (nt, mu, se))
    os.makedirs(out, exist_ok=True)
    lib = os.path.join(ROOT, "build", "ppa", "sky130hd_synth.lib")
    abc = "abc -constr %s -script %s -liberty %s" % (os.path.join(ROOT, "ppa", "abc_speed.constr"), os.path.join(ROOT, "ppa", "abc_speed.script"), lib)
    ys = open(os.path.join(ROOT, "ppa", "synth_template.ys")).read()
    for k, v in {"@ROOT@": ROOT, "@SOURCES@": " ".join(os.path.join(ROOT, f) for f in SRC.split()), "@LIB@": lib, "@MU@": str(mu), "@NT@": str(nt),
                 "@SE@": str(se), "@ABC@": abc, "@OUT@": out}.items():
        ys = ys.replace(k, v)
    open(os.path.join(out, "synth.ys"), "w").write(ys)
    r = subprocess.run(["yosys", "-l", os.path.join(out, "yosys.log"), os.path.join(out, "synth.ys")], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        return dict(mu=mu, error=(r.stdout + r.stderr)[-300:])
    stat = open(os.path.join(out, "stat.txt")).read()
    cells = int(re.search(r"Number of cells:\s+(\d+)", stat).group(1))
    area = float(re.search(r"Chip area for module '[^']+': ([\d.]+)", stat).group(1))
    flops = sum(int(m.group(1)) for m in re.finditer(r"sky130_fd_sc_hd__df\w*\s+(\d+)", stat))
    wns, _ = sta(out, 10.0)
    tmin = 10.0 - wns
    res = dict(mu=mu, cells=cells, area=area, flops=flops, tmin=tmin)
    # method check at +/-2 %
    hi, _ = sta(out, tmin * 1.02)
    lo, _ = sta(out, tmin * 0.98)
    res["hi"], res["lo"] = hi, lo
    res["verified"] = hi >= 0 and lo < 0
    sta(out, 10.0)   # restore the 10 ns reports
    async_txt = open(os.path.join(out, "async.txt")).read() if os.path.exists(os.path.join(out, "async.txt")) else ""
    m = re.search(r"(-?[\d.]+)\s+slack", async_txt)
    res["async_slack"] = float(m.group(1)) if m else None
    neff = nt // 2 if se else nt
    res["ii"] = math.ceil(neff / mu)
    res["msps"] = (1000.0 / tmin) / res["ii"]
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--nt", type=int, default=64)
    ap.add_argument("--se", type=int, default=1)
    ap.add_argument("--macs", nargs="+", type=int, default=[1, 2, 4, 8, 16, 32])
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "ppa", "ppa_sweep.md"))
    a = ap.parse_args()
    os.makedirs(os.path.join(ROOT, "build", "ppa"), exist_ok=True)
    subprocess.check_call([sys.executable, os.path.join(ROOT, "ppa", "prune_lib.py"), os.path.join(ROOT, "ppa", "lib", "sky130hd_tt.lib"),
                           os.path.join(ROOT, "build", "ppa", "sky130hd_synth.lib"), "--drop", "sky130_fd_sc_hd__lpflow_", "--drop", "sky130_fd_sc_hd__probe"], stdout=subprocess.DEVNULL)
    with cf.ThreadPoolExecutor(3) as ex:
        res = list(ex.map(lambda mu: point(mu, a.nt, a.se), a.macs))
    L = ["# axis_fir_top: area, timing and throughput versus MAC_UNITS (measured: Yosys 0.33 + OpenSTA, Sky130 HD tt_025C_1v80, pre-layout)", "",
         "Build: NTAPS_MAX=%d, DATA_W=COEF_W=16, SYMMETRIC_EN=%d. Timing-driven ABC mapping. Boundary: inputs 0.35 ns after the edge, outputs 0.15 ns before the next edge "
         "(assumption). The asynchronous-reset network is excluded (see constraints/axis_fir_top.sdc). T_min = 10 ns - WNS, verified at +/-2 %%." % (a.nt, a.se), "",
         "| MAC_UNITS | II (cycles/sample) | cells | area (um^2) | flip-flops | T_min (ns) | F_max (MHz) | throughput (Msamples/s) | method check |",
         "|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in res:
        if "error" in r:
            L.append("| %d | | ERROR | | | | | | %s |" % (r["mu"], r["error"][:60].replace("|", "/")))
            continue
        L.append("| %d | %d | %d | %.0f | %d | %.3f | %.1f | %.2f | %s |" % (r["mu"], r["ii"], r["cells"], r["area"], r["flops"], r["tmin"], 1000 / r["tmin"], r["msps"],
                                                                                 "verified (%+.3f / %+.3f ns)" % (r["hi"], r["lo"]) if r["verified"] else "NOT VERIFIED (%+.3f / %+.3f)" % (r["hi"], r["lo"])))
    L += ["", "Throughput = F_max / II, a derived figure: II = ceil(N_eff / MAC_UNITS) with N_eff = NTAPS_MAX/2 for the symmetric build "
          "(documented in docs/03_microarchitecture.md; the cocotb fold-sweep checks bit-exactness at every MAC_UNITS). "
          "Worst asynchronous-reset recovery slack at 10 ns, unbuffered reset net (informational, NOT a pass/fail figure): " +
          ", ".join("M=%d: %s ns" % (r["mu"], r.get("async_slack")) for r in res if "error" not in r)]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
