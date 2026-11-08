#!/usr/bin/env python3
"""validate.py <sim.js> <rtlbuild dir>: replay the model's stimulus through the real axis_fir_top (Icarus) and diff every output, every cycle."""
import os, re, subprocess, sys
simjs, build = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
HERE = os.path.dirname(os.path.abspath(__file__))
FIELDS = "awready wready bvalid bresp arready rvalid rdata rresp s_tready m_tvalid m_tdata m_tlast irq credits state dp in_cnt out_cnt bank_sel pending irq_st scnt fcnt".split()
scen = re.findall(r"\{id:'(\w+)',name:", open(simjs).read())
only = sys.argv[3].split(',') if len(sys.argv) > 3 else None
tot_cycles = 0; bad = 0; runs = 0; report = []
for sid in scen:
    if only and sid not in only: continue
    for mac in (1, 2, 4):
        for rnd in ((0, 1, 2) if sid in ('tie', 'lowpass', 'sat') else (2,)):
            tag = "%s_m%d_r%d" % (sid, mac, rnd); wd = os.path.join(HERE, "run", tag); os.makedirs(wd, exist_ok=True)
            o = subprocess.run(["gjs", os.path.join(HERE, "run_model.js"), simjs, sid, str(mac), str(rnd), wd + "/stim.txt", wd + "/exp.txt"], capture_output=True, text=True)
            if o.returncode: print("MODEL ERROR", tag, o.stderr[-400:]); bad += 1; continue
            info = o.stdout.strip()
            if 'done=true' not in info: print('FAIL scenario did not finish:', info); bad += 1; continue
            r = subprocess.run(["vvp", os.path.join(build, "sim_mac%d" % mac)], cwd=wd, capture_output=True, text=True)
            got = [l.split() for l in r.stdout.splitlines() if re.match(r"^[0-9a-fx]+( [0-9a-fx]+){22}$", l)]
            exp = [l.split() for l in open(wd + "/exp.txt").read().splitlines()]
            runs += 1; first = None
            if len(got) != len(exp): first = "length differs: RTL %d cycles, model %d" % (len(got), len(exp))
            else:
                for c, (a, b) in enumerate(zip(got, exp)):
                    if a != b:
                        d = [(FIELDS[i], a[i], b[i]) for i in range(23) if a[i] != b[i]]
                        first = "cycle %d: %s (RTL, model)" % (c + 1, d[:4]); break
            tot_cycles += len(exp)
            print(("OK   " if not first else "FAIL ") + info + ("" if not first else "\n      " + first))
            if first: bad += 1
print("\n%d runs, %d cycles compared (%d outputs per cycle), %d mismatching runs" % (runs, tot_cycles, len(FIELDS), bad))
sys.exit(1 if bad else 0)
