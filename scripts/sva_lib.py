#!/usr/bin/env python3
"""sva_lib.py - run the cocotb suites under Verilator with the bound checkers (verif/) and read the results back.

Used by run_sva.py (baseline run, results/sva/) and sva_mutants.py (the injected-bug campaign).

A "run" is one (suite, parameter set). It is built with Verilator 5.052 (cocotb 2.x needs >= 5.036, the checkers need sequence delays),
run with the repository's own cocotb tests, and read back from three things: the cocotb summary line, the `Assertion failed in <scope>.<label>`
lines Verilator prints, and the coverage.dat the simulation writes (cover-property hit counts).
"""
import collections
import concurrent.futures as cf
import os
import re
import signal
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VLT = os.environ.get("VERILATOR_5052", os.path.normpath(os.path.join(ROOT, "..", "..", "_toolchain", "verilator-5.052")))

# The matrix mirrors `make regress` plus the repository's own sweeps (csr-sweep, stream-sweep, fold-sweep): (tag, suite, make arguments)
CONFIGS = (
    [("csr_n64_m8_s1", "csr", "NTAPS_MAX=64 MAC_UNITS=8 SYMMETRIC_EN=1"), ("csr_n32_m4_s1", "csr", "NTAPS_MAX=32 MAC_UNITS=4 SYMMETRIC_EN=1"),
     ("csr_n16_m8_s1", "csr", "NTAPS_MAX=16 MAC_UNITS=8 SYMMETRIC_EN=1"), ("csr_n128_m16_s1", "csr", "NTAPS_MAX=128 MAC_UNITS=16 SYMMETRIC_EN=1"),
     ("csr_n256_m1_s0", "csr", "NTAPS_MAX=256 MAC_UNITS=1 SYMMETRIC_EN=0")]
    + [("stream_d16_i8_o8_p4", "stream", "DATA_W=16 IN_FIFO_DEPTH=8 OUT_FIFO_DEPTH=8 PIPE_DEPTH=4 LONG_BEATS=5000"),
       ("stream_d16_i2_o4_p2", "stream", "DATA_W=16 IN_FIFO_DEPTH=2 OUT_FIFO_DEPTH=4 PIPE_DEPTH=2 LONG_BEATS=5000"),
       ("stream_d16_i4_o32_p16", "stream", "DATA_W=16 IN_FIFO_DEPTH=4 OUT_FIFO_DEPTH=32 PIPE_DEPTH=16 LONG_BEATS=5000"),
       ("stream_d8_i8_o8_p1", "stream", "DATA_W=8 IN_FIFO_DEPTH=8 OUT_FIFO_DEPTH=8 PIPE_DEPTH=1 LONG_BEATS=5000"),
       ("stream_d32_i16_o16_p8", "stream", "DATA_W=32 IN_FIFO_DEPTH=16 OUT_FIFO_DEPTH=16 PIPE_DEPTH=8 LONG_BEATS=5000")]
    + [("fir_n16", "fir", "NTAPS_MAX=16"), ("fir_n64", "fir", "NTAPS_MAX=64")]
    + [("top_m%d" % m, "top", "NTAPS_MAX=32 MAC_UNITS=%d" % m) for m in (1, 2, 4, 8, 16, 32)]
)
QUICK = ("csr_n64_m8_s1", "stream_d16_i8_o8_p4", "fir_n16", "top_m8")   # the four default-parameter suites (`make regress`)

REC = re.compile(r"^C '(.*)' (\d+)$")


def env():
    e = dict(os.environ)
    e["VERILATOR_ROOT"] = VLT
    e["VERILATOR_BIN_DIR"] = VLT + "/bin"
    e["PATH"] = VLT + "/bin:" + e.get("PATH", "")
    e["PYTHONUNBUFFERED"] = "1"   # cocotb's log lines must not land in the middle of a Verilator "Assertion failed in <scope>.<label>" line
    return e


def parse_run(cfg, text, rc, covdat, log):
    """a run's result from its log text (shared by a fresh run and by --resume, which reads logs written earlier)"""
    tag, suite, args = cfg
    m = re.findall(r"TESTS=(\d+) PASS=(\d+) FAIL=(\d+) SKIP=(\d+)", text)
    fired = collections.Counter(re.findall(r"Assertion failed in \S*?\.((?:a|sb)_\w+)", text))
    # the RTL carries its own $error checks (credit_ctr); a firing counts like a checker assertion
    for mm in re.findall(r"%Error: [^\n]*?(credit_ctr: [a-z ]+)", text):
        fired["rtl:" + mm.strip()[:40]] += 1
    timeout = "TIMEOUT" in text.splitlines()[-1:] or text.rstrip().endswith("TIMEOUT")
    res = dict(tag=tag, suite=suite, rc=rc, log=log, fired=dict(fired), timeout=timeout, build_error=("%Error" in text and not m and not timeout))
    if m:
        res["tests"], res["pass"], res["fail"], res["skip"] = (int(x) for x in m[-1])
    else:
        res["tests"] = res["pass"] = res["fail"] = res["skip"] = None
    res["covdat"] = covdat
    res["ok"] = bool(m) and res["fail"] == 0 and not fired and rc == 0
    # the run ended without cocotb's summary and not by a timeout or a build error, with checker firings in the log: the checkers stopped it
    # (the simulator's error limit), which is a catch by an assertion, not by a test
    res["aborted_by_checker"] = (not m) and bool(fired) and not timeout and not res["build_error"]
    # "the tests caught it": a cocotb test failed, the run timed out, or it ended without a summary and nothing but the checkers can explain that
    res["tests_failed"] = (bool(m) and res["fail"] > 0) or timeout or ((not m) and not res["aborted_by_checker"] and not res["build_error"])
    return res


def run_one(cfg, outdir, rtl_dir=None, checkers=None, timeout=300, reuse=False):
    """build + run one configuration; returns a dict (never raises). A run that does not finish within `timeout` seconds is killed with its whole
    process group (the simulator is a grandchild of make) and recorded as a timeout: a mutant that makes a test wait forever is detected, not waited for."""
    tag, suite, args = cfg
    build = os.path.join(outdir, tag)
    log = os.path.join(outdir, tag + ".log")
    covdat = os.path.join(build, "coverage.dat")
    if reuse and os.path.exists(log):
        text = open(log, errors="replace").read()
        if re.search(r"TESTS=\d+ PASS=\d+ FAIL=\d+", text) or text.rstrip().endswith("TIMEOUT"):
            return parse_run(cfg, text, 0, covdat, log)
    cmd = ["make", "-s", "-C", os.path.join(ROOT, "tb", "cocotb"), "SIM=verilator", "SUITE=" + suite, "SIM_BUILD=" + build,
           "COCOTB_RESULTS_FILE=" + os.path.join(outdir, tag + ".xml"), "SIM_CMD_PREFIX=stdbuf -oL -eL"] + args.split()
    if rtl_dir:
        cmd.append("RTL_DIR=" + rtl_dir)
    if checkers:
        cmd.append("CHECKERS=" + checkers)
    os.makedirs(outdir, exist_ok=True)
    p = subprocess.Popen(cmd, env=env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        text, _ = p.communicate(timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        text, _ = p.communicate()
        text = (text or "") + "\nTIMEOUT"
        rc = -9
    open(log, "w").write(text)
    return parse_run(cfg, text, rc, covdat, log)


def run_matrix(cfgs, outdir, jobs=4, **kw):
    with cf.ThreadPoolExecutor(jobs) as ex:
        return list(ex.map(lambda c: run_one(c, outdir, **kw), cfgs))


def read_cov(path):
    """cover-property bins of one coverage.dat: {(checker module, label, instance path): hits}"""
    out = {}
    if not os.path.exists(path):
        return out
    for raw in open(path, "rb").read().decode("latin-1").splitlines():
        m = REC.match(raw)
        if not m:
            continue
        f = {}
        for part in m.group(1).split("\x01")[1:]:
            k, _, v = part.partition("\x02")
            f[k] = v
        page = f.get("page", "")
        if page.startswith("v_user/"):
            mod = page.split("/", 1)[1].split("__")[0]
            out[(mod, f["o"], f["h"])] = out.get((mod, f["o"], f["h"]), 0) + int(m.group(2))
    return out


def merge_cov(results):
    tot = collections.defaultdict(int)
    for r in results:
        for k, v in read_cov(r["covdat"]).items():
            tot[k] += v
    return tot


def by_label(tot):
    """{(checker module, label): total hits over all instances and runs}, plus the instance count"""
    agg = collections.defaultdict(int)
    inst = collections.defaultdict(set)
    for (mod, lab, h), v in tot.items():
        agg[(mod, lab)] += v
        inst[(mod, lab)].add(h)
    return agg, inst


def assertion_labels(checker_file):
    """the labelled concurrent assertions and procedural asserts defined in a checker source"""
    labs = []
    for line in open(checker_file).read().splitlines():
        m = re.match(r"^\s*((?:a|sb)_\w+):\s*assert\b", line)
        if m:
            labs.append(m.group(1))
    return labs


def module_of(checker_file):
    """{label: checker module} for every assertion / cover label in a checker source"""
    mod, d = None, {}
    for line in open(checker_file).read().splitlines():
        m = re.match(r"^module (\w+)", line)
        if m:
            mod = m.group(1)
        m = re.match(r"^\s*((?:a|sb|c)_\w+):\s*(?:assert|cover)\b", line)
        if m and mod:
            d[m.group(1)] = mod
    return d


if __name__ == "__main__":
    sys.exit("use run_sva.py or sva_mutants.py")
