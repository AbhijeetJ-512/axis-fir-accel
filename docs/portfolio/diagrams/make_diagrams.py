#!/usr/bin/env python3
"""axis_fir_accel diagrams (dgm.py). Run: python3 docs/portfolio/diagrams/make_diagrams.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # dgm.py sits next to this file
from dgm import Diagram  # noqa: E402


def architecture():
    d = Diagram("axis_fir_accel - architecture", 1540, 800)
    d.legend = ("data", "ctrl")
    d.legend_pos = (1290, 8)
    d.group("g", 175, 45, 1200, 690, "axis_fir_top  (one clock aclk; reset_sync: asynchronous assert, synchronous de-assert, 2 stages)", label_pos="bottom")
    # external
    d.box("m", 20, 95, 130, 100, "AXI4-Lite\nmaster", "io", sub="driver / CPU", size=12)
    d.box("src", 20, 450, 130, 100, "AXI4-Stream\nsource", "io", sub="s_axis_*", size=12)
    d.box("snk", 1400, 450, 120, 100, "AXI4-Stream\nsink", "io", sub="m_axis_*", size=12)
    d.box("irq", 20, 240, 130, 90, "irq", "io", sub="level:\nIRQ_STATUS &\nIRQ_ENABLE", size=11)
    # control plane
    d.box("axl", 225, 100, 150, 90, "axil_slave", "block", sub="AXI4-Lite protocol\nengine, SLVERR", size=12)
    d.box("csr", 445, 100, 160, 90, "csr_regfile", "block", sub="CTRL CFG STATUS IRQ\nCAPABILITY, counters", size=12)
    d.box("cb", 675, 100, 180, 90, "coeff_bank", "mem", sub="shadow | active banks\nswap at frame boundary", size=12)
    d.box("ctl", 890, 100, 250, 90, "fir_ctrl", "fsm", sub="FSM IDLE/RUN/DRAIN/CFGERR\nfold scheduler:\nP = ceil(N_eff / MAC_UNITS) passes", size=11)
    d.box("cr", 1230, 100, 120, 90, "credit_ctr", "ctrl", sub="in flight + out_fifo\n<= OUT_FIFO_DEPTH", size=11)
    # data plane
    d.box("fi", 200, 440, 130, 120, "in_fifo", "block", sub="axis_fifo\nIN_FIFO_DEPTH\ns_axis_tready =\n!full", size=11)
    d.group("core", 430, 395, 770, 210, "fir_core  (the datapath: no stall input, fixed pass structure)", label_pos="bottom")
    d.box("dl", 445, 440, 165, 120, "delay_line", "block", sub="x[n] ... x[n-NTAPS_MAX+1]\nshifts once per sample\nsymmetric pre-adder,\nrun-time mirror mux", size=11)
    d.box("mac", 660, 440, 140, 120, "mac_array", "accent", sub="MAC_UNITS signed\nmultipliers\n(DATA_W+1) x COEF_W", size=11)
    d.box("acc", 855, 440, 145, 120, "acc_reduce", "block", sub="adder tree,\nclog2(MAC_UNITS)\nlevels + fold\naccumulator, ACC_W", size=11)
    d.box("rs", 1050, 440, 130, 120, "round_sat", "accent", sub="TRUNC / HALF_UP /\nCONVERGENT;\nsaturate -> sat_event", size=11)
    d.box("fo", 1230, 440, 120, 120, "out_fifo", "block", sub="axis_fifo\nOUT_FIFO_DEPTH\nm_axis_tvalid =\n!empty", size=11)
    # control-plane wires
    d.arrow("w1", "m", "axl", sside="r", dside="l", sf=0.5, df=0.5, both=True)
    d.text("w1t", 152, 116, "5 channels", 10, color="#1f4e9c", align="left")
    d.arrow("w2", "axl", "csr", sside="r", dside="l")
    d.text("w2t", 378, 116, "reg bus", 10, color="#1f4e9c", align="left")
    d.arrow("w3", "csr", "cb", sside="r", dside="l")
    d.text("w3t", 610, 106, "coef_we,\nwaddr,\nwdata", 10, color="#1f4e9c", align="left")
    d.arrow("w4", "csr", "ctl", kind="ctrl", sside="t", dside="t", sf=0.5, df=0.5, via=[(525, 68), (1015, 68)], label="ENABLE, NTAPS, SYMMETRIC, cmd pulses", lpos=(770, 60))
    d.arrow("w5", "csr", "irq", kind="ctrl", sside="b", dside="t", sf=0.12, df=0.5, via=[(464.2, 218), (85, 218)], label="irq", lpos=(300, 210))
    d.arrow("w6", "ctl", "cr", kind="ctrl", sside="r", dside="l", both=True, label="consume,\nhave_credit", lpos=(1185, 138))
    d.arrow("w7", "ctl", "core", kind="ctrl", sside="b", dside="t", sf=0.5, df=(1015 - 430) / 770, label="shift_en, pass_vld/first/last/tlast, tap_base", lpos=(1180, 340))
    d.arrow("w8", "cb", "mac", sside="b", dside="t", sf=(745 - 675) / 180, df=(745 - 660) / 140, label="coef_flat (active bank)", lpos=(760, 250))
    # data-plane wires
    d.arrow("d0", "src", "fi", label="TDATA,\nTLAST", lpos=(165, 475))
    d.arrow("d1", "fi", "dl", label="in_tdata", lpos=(360, 495))
    d.arrow("d2", "dl", "mac", label="", sside="r", dside="l")
    d.arrow("d3", "mac", "acc")
    d.arrow("d4", "acc", "rs")
    d.arrow("d5", "rs", "fo")
    d.arrow("d6", "fo", "snk", label="TDATA,\nTLAST", lpos=(1375, 475))
    # flow-control wires
    d.arrow("c1", "fo", "cr", kind="ctrl", sside="t", dside="b", sf=0.75, df=0.75, label="out_pop:\ncredit restored", lpos=(1325, 300))
    d.arrow("c2", "fi", "ctl", kind="ctrl", sside="t", dside="b", sf=0.5, df=(916 - 890) / 250, via=[(265, 262), (916, 262)], label="in_tvalid, in_tlast  /  in_tready (pop)", lpos=(430, 252))
    d.arrow("c3", "fo", "cb", kind="ctrl", sside="t", dside="b", sf=0.2, df=(812 - 675) / 180, via=[(1254, 222), (812, 222)], label="frame_boundary = out_pop && TLAST", lpos=(1137, 214))
    d.text("n", 20, 755, "Not drawn, to keep the figure legible: cfg_ntaps / cfg_round_mode / cfg_sat_mode / cfg_symmetric go from csr_regfile straight to fir_core; evt_sample, evt_frame_done and evt_saturate go to csr_regfile (status, counters, irq);\n"
                         "cmd_soft_reset | cmd_flush clears both FIFOs' input side, the delay line and the credit counter. Every box is a separate RTL module under rtl/; axis_fir_top contains wiring only.", 11, italic=True)
    return d


def flowchart():
    d = Diagram("axis_fir_accel - sample flow", 1300, 1130)
    d.legend = ("flow",)
    d.legend_pos = (1100, 12)
    d.box("s", 460, 40, 380, 52, "Sample waiting on s_axis_*", "term", sub="accepted into in_fifo while it is not full", size=12)
    d.box("d1", 470, 120, 360, 72, "state == RUN ?", "dec", sub="ENABLE set and configuration legal", size=12)
    d.box("d2", 470, 222, 360, 72, "in_fifo not empty ?", "dec", size=12)
    d.box("d3", 470, 324, 360, 72, "credit available ?", "dec", sub="credits != 0 (output slot guaranteed)", size=12)
    d.box("d4", 470, 426, 360, 72, "core free ?", "dec", sub="not busy, or last pass of previous sample", size=11)
    d.box("p1", 440, 530, 420, 86, "sample_start", "proc", sub="pop in_fifo, consume one credit,\nshift the delay line ONCE, capture TLAST (tlast_q)", size=12)
    d.box("p2", 440, 650, 420, 86, "pass p  (p = 0 ... P-1)", "proc", sub="tap_base = p * MAC_UNITS; symmetric: pre-add x[n-k] + x[n-(N-1-k)];\ninactive taps: BOTH operands gated to 0 (0 * X would be X)", size=11)
    d.box("p3", 440, 770, 420, 70, "multiply, adder tree, fold accumulate", "proc", sub="acc_first clears the accumulator, acc_last releases it\nACC_W bits: overflow impossible by construction", size=11)
    d.box("d5", 470, 868, 360, 72, "last pass ?", "dec", sub="p == P - 1", size=12)
    d.box("p4", 440, 968, 420, 70, "round_sat", "proc", sub="round (3 modes) then saturate; sat_event sets sticky bit", size=12)
    d.box("p5", 440, 1060, 420, 56, "push {data, TLAST} to out_fifo", "proc", size=12)
    d.box("w", 20, 530, 330, 120, "wait", "note", sub="IDLE / CFGERR: nothing is admitted\nno data: nothing is admitted\nno credit: output side is backed up\ncore busy: next sample waits for its last pass", size=11)
    d.box("o", 940, 530, 340, 130, "Output side", "sub", sub="m_axis_tvalid = !out_fifo empty\nbeat leaves when m_axis_tready\nout_pop: restore one credit\nout_pop && TLAST = frame_boundary:\nshadow -> active swap, FRAME_DONE", size=11)
    cx = 650
    for a, b in [("s", "d1"), ("d1", "d2"), ("d2", "d3"), ("d3", "d4"), ("d4", "p1"), ("p1", "p2"), ("p2", "p3"), ("p3", "d5"), ("d5", "p4"), ("p4", "p5")]:
        yes = a.startswith("d")
        d.arrow("e_" + a, a, b, kind="flow", sside="b", dside="t", label="yes" if yes else "", lpos=(cx + 18, (d.node(a).y + d.node(a).h + d.node(b).y) / 2) if yes else None)
    d.arrow("n1", "d1", "w", kind="flow", sside="l", dside="t", sf=0.5, df=0.5, via=[(185, 156)], label="no", lpos=(420, 148))
    d.arrow("n2", "d2", "w", kind="flow", sside="l", dside="t", sf=0.5, df=0.8, via=[(284, 258)], label="no", lpos=(420, 250))
    d.arrow("n3", "d3", "w", kind="flow", sside="l", dside="r", sf=0.5, df=0.4, via=[(400, 360), (400, 578)], label="no", lpos=(420, 352))
    d.arrow("n4", "d4", "w", kind="flow", sside="l", dside="r", sf=0.5, df=0.8, via=[(380, 462), (380, 626)], label="no", lpos=(420, 454))
    d.arrow("n5", "d5", "p2", kind="flow", sside="r", dside="r", sf=0.5, df=0.5, via=[(900, 904), (900, 693)], label="no: next pass", lpos=(900, 800))
    d.arrow("o1", "p5", "o", kind="flow", sside="r", dside="b", sf=0.5, df=(1250 - 940) / 340, via=[(1250, 1088)], label="", lpos=None)
    d.text("w2", 20, 660, "Re-evaluated every cycle: a sample is\nadmitted in the first cycle all four\nconditions hold.", 11, italic=True)
    d.text("t", 940, 700, "A new sample_start may occur in the\nsame cycle as the last pass of the\nprevious sample: the delay line shifts\non the edge where that pass captures\nits operands. So II = P cycles exactly.", 11, italic=True)
    return d


def control():
    d = Diagram("axis_fir_accel - control and credit flow", 1300, 760)
    d.legend = ("flow", "ctrl")
    d.legend_pos = (1100, 12)
    d.group("fsm", 20, 50, 640, 420, "fir_ctrl run FSM", label_pos="bottom")
    d.box("idle", 60, 100, 180, 70, "IDLE", "fsm", sub="nothing admitted;\nreset state", size=13)
    d.box("run", 450, 100, 190, 70, "RUN", "fsm", sub="admit while credit\nand data are available", size=13)
    d.box("drain", 450, 300, 190, 70, "DRAIN", "fsm", sub="finish admitted samples,\nadmit no new ones", size=13)
    d.box("err", 60, 300, 180, 70, "CFGERR", "fsm", sub="illegal CFG enabled;\nsticky CFG_ERR, irq", size=13)
    d.arrow("t1", "idle", "run", kind="flow", sside="r", dside="l", sf=0.3, df=0.3, label="ENABLE &&\n!cfg_illegal", lpos=(345, 105))
    d.arrow("t2", "run", "drain", kind="flow", sside="b", dside="t", label="!ENABLE or FLUSH", lpos=(545, 235))
    d.arrow("t3", "drain", "idle", kind="flow", sside="l", dside="r", sf=0.5, df=0.8, via=[(330, 335), (330, 156)], label="not busy and\ndp_count == 0", lpos=(330, 245))
    d.arrow("t4", "idle", "err", kind="flow", sside="b", dside="t", sf=0.3, df=0.3, label="ENABLE &&\ncfg_illegal", lpos=(85, 235))
    d.arrow("t5", "err", "idle", kind="flow", sside="t", dside="b", sf=0.75, df=0.75, label="!ENABLE", lpos=(222, 235))
    d.text("f1", 40, 405, "DRAIN exists so that stopping loses nothing: dropping ENABLE mid-stream still delivers every admitted sample\n(measured: beats out == SAMPLE_CNT).  FLUSH and SOFT_RESET discard by definition.", 11, italic=True)
    # credit counter
    d.group("crg", 690, 50, 590, 420, "credit_ctr - why the output FIFO can never overflow", label_pos="bottom")
    d.box("c0", 720, 100, 250, 80, "credits  (reset = OUT_FIFO_DEPTH)", "ctrl", sub="have_credit = (credits != 0)\nconsume and restore in one cycle: unchanged", size=12)
    d.box("c1", 1010, 100, 240, 80, "consume  (-1)", "proc", sub="fir_ctrl admits a sample", size=12)
    d.box("c2", 1010, 215, 240, 80, "restore  (+1)", "proc", sub="out_pop = m_axis_tvalid && m_axis_tready", size=11)
    d.box("c4", 720, 330, 530, 95, "Invariant", "accent", sub="samples in flight + out_fifo occupancy  <=  OUT_FIFO_DEPTH\nso the output FIFO is never written while full - a counting argument, not a stall net.\nCheck at elaboration: OUT_FIFO_DEPTH > 5 + clog2(MAC_UNITS), else II = 1 is lost", size=11)
    d.arrow("k1", "c1", "c0", kind="ctrl", sside="l", dside="r", sf=0.5, df=0.5)
    d.arrow("k2", "c2", "c0", kind="ctrl", sside="l", dside="b", sf=0.5, df=0.5, via=[(845, 255)])
    # coefficient swap
    d.group("cog", 20, 500, 1260, 225, "coefficient reload: atomic at a frame boundary", label_pos="bottom")
    d.box("s1", 50, 545, 250, 80, "write COEFF[i]", "proc", sub="AXI4-Lite, lands in the SHADOW bank", size=12)
    d.box("s2", 350, 545, 250, 80, "CTRL.COEFF_COMMIT", "proc", sub="STATUS.COEFF_PENDING = 1", size=12)
    d.box("s3", 640, 540, 300, 90, "frame_boundary || idle ?", "dec", sub="boundary = a TLAST beat has\nLEFT the IP (output pop)", size=11)
    d.box("s4", 990, 545, 260, 80, "swap  (bank_sel toggles)", "accent", sub="PENDING clears; the next frame\nuses the new coefficients", size=12)
    d.box("s5", 640, 655, 300, 48, "wait", "note", sub="the frame in progress keeps the OLD coefficients", size=11)
    d.arrow("s_1", "s1", "s2", kind="flow")
    d.arrow("s_2", "s2", "s3", kind="flow")
    d.arrow("s_3", "s3", "s4", kind="flow", label="yes", lpos=(965, 560))
    d.arrow("s_4", "s3", "s5", kind="flow", sside="b", dside="t", label="no", lpos=(805, 640))
    return d


if __name__ == "__main__":
    for n, f in (("fir_architecture", architecture), ("fir_flowchart", flowchart), ("fir_control", control)):
        print(n, "overflow:", f().export_all(HERE, n))
