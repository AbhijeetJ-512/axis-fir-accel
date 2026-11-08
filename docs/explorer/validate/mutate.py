#!/usr/bin/env python3
"""mutate.py <sim.js> <rtlbuild>: apply one-line mutations to the MODEL and check that the replay against the real RTL notices each of them."""
import os, subprocess, sys
simjs, build = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]); HERE = os.path.dirname(os.path.abspath(__file__))
M = [
 ("axil: AW accepted while a slot is held", "g.awready=slot&&!S.aw_full;", "g.awready=slot;"),
 ("axil: W accepted while a slot is held", "g.wready=slot&&!S.w_full;", "g.wready=slot;"),
 ("axil: write slot ignores bvalid", "const slot=!S.bvalid;", "const slot=true;"),
 ("csr: unmapped write returns OKAY", "const reg_werr=!w_mapped||cfg_wr_blocked;", "const reg_werr=cfg_wr_blocked;"),
 ("csr: CFG writable while busy", "cfg_wr_blocked=w_csr_mapped&&w12===REG.CFG&&sts_busy;", "cfg_wr_blocked=false;"),
 ("csr: read-only registers writable", "const csr_wr=g.reg_wen&&w_csr_mapped&&!w_is_ro&&!reg_werr;", "const csr_wr=g.reg_wen&&w_csr_mapped&&!reg_werr;"),
 ("csr: byte strobes ignored", "const sel=((s>>b)&1)?n:o;", "const sel=n;"),
 ("csr: IRQ_STATUS never clears", "case REG.IRQ_ST: N.irq_st=S.irq_st&~(wr_clr&7); break;", "case REG.IRQ_ST: break;"),
 ("csr: sat sticky clear loses to a new event", "if((wr_clr&2)&&!evt_sat) N.sat_sticky=0;", "if((wr_clr&2)) N.sat_sticky=0;"),
 ("csr: irq ignores IRQ_ENABLE", "g.irq=(S.irq_st&S.irq_en)!==0;", "g.irq=S.irq_st!==0;"),
 ("csr: coefficient index range unchecked", "w_coef_valid=w_coef_region&&w_idx<P.NMAX;", "w_coef_valid=w_coef_region;"),
 ("csr: coefficient read range unchecked", "r_coef_valid=r_coef_region&&r_idx<P.NMAX;", "r_coef_valid=r_coef_region;"),
 ("csr: CNT_CLR ignored", "if(S.c_cnt){ N.scnt=0; N.fcnt=0; }", "if(false){ N.scnt=0; N.fcnt=0; }"),
 ("csr: SAMPLE_CNT counts outputs not admissions", "N.scnt=S.scnt+(sample_start?1:0);", "N.scnt=S.scnt+(S.out_valid?1:0);"),
 ("csr: CFG_ERR event only from IDLE", "cfg_err_event=!!(S.en&&cfg_illegal);", "cfg_err_event=!!(S.en&&cfg_illegal&&S.state===0);"),
 ("csr: CAPABILITY field order", "((P.LOG2MAC&15)<<4)|(clog2(P.NMAX)&15)", "((P.LOG2MAC&15))|((clog2(P.NMAX)&15)<<4)"),
 ("coef: commit on the swap cycle lost", "N.pending=S.c_commit; }", "N.pending=0; }"),
 ("coef: no swap at a frame boundary", "swap_now=!!(S.pending&&(evt_frame_done||!sts_busy))", "swap_now=!!(S.pending&&(!sts_busy))"),
 ("coef: no swap when idle", "swap_now=!!(S.pending&&(evt_frame_done||!sts_busy))", "swap_now=!!(S.pending&&(evt_frame_done))"),
 ("coef: readback from the active bank", "(S.bank_sel?S.bank0:S.bank1)[r_idx]", "(S.bank_sel?S.bank1:S.bank0)[r_idx]"),
 ("credit: reload on flush ignores the output FIFO", "if(soft_clear) N.credits=reload;", "if(soft_clear) N.credits=P.OUTF;"),
 ("credit: always has credit", "const have_credit=S.credits!==0;", "const have_credit=true;"),
 ("credit: not restored on pop", "else if(!sample_start&&out_pop) N.credits=S.credits+1;", "else if(false) N.credits=S.credits+1;"),
 ("ctrl: admission during pass phase", "can_accept=!S.busy||last_pass;", "can_accept=true;"),
 ("ctrl: no back-to-back admission", "can_accept=!S.busy||last_pass;", "can_accept=!S.busy;"),
 ("ctrl: TLAST not captured", "N.tlast_q=inh.l;", "N.tlast_q=0;"),
 ("ctrl: DRAIN ends without waiting for the pipeline", "case 2: if(!S.busy&&S.dp===0) state_n=0; break;", "case 2: if(!S.busy) state_n=0; break;"),
 ("ctrl: flush does not clear the delay line", "if(soft_clear) N.dl=new Array(P.NMAX).fill(0); else if(sample_start)", "if(false) N.dl=0; else if(sample_start)"),
 ("ctrl: CFGERR not left when ENABLE drops", "case 3: if(!S.en) state_n=0; break;", "case 3: break;"),
 ("core: centre tap counted twice", "!S.sym?near:mid?near:near+far", "!S.sym?near:near+far"),
 ("core: symmetric half-length off by one", "const active=S.sym?(kf<h_taps):(kf<S.ntaps);", "const active=S.sym?(kf<=h_taps):(kf<S.ntaps);"),
 ("core: far operand index off by one", "const farf=(S.ntaps-1-kf)&511", "const farf=(S.ntaps-kf)&511"),
 ("core: delay line shifts every pass", "else if(sample_start) N.dl=", "else if(sample_start||S.busy) N.dl="),
 ("core: tap_base ignores pass number", "const tap_base=(S.pcnt<<P.LOG2MAC)&511;", "const tap_base=0;"),
 ("core: fold accumulator not restarted", "const acc_next=acc_first?tree_acc:S.fold+tree_acc;", "const acc_next=S.fold+tree_acc;"),
 ("core: flush leaves the control chain valid (the as-found bug)", "if(soft_clear){ N.ch_vld=new Array(P.DLY).fill(0); }", "if(false){ N.ch_vld=0; }"),
 ("core: flush leaves result_vld set", "if(soft_clear){ N.res_vld=0; }", "if(false){ N.res_vld=0; }"),
 ("core: last pass flag lost in the chain", "N.ch_last[0]=pass_last?1:0;", "N.ch_last[0]=0;"),
 ("round: half-up needs a sticky bit", "const inc=rm===1?guard:", "const inc=rm===1?(guard&sticky):"),
 ("round: convergent ignores lsb", "rm===2?(guard&(sticky|lsb))", "rm===2?(guard&sticky)"),
 ("round: convergent ignores sticky", "rm===2?(guard&(sticky|lsb))", "rm===2?(guard&lsb)"),
 ("sat: symmetric clamp is -32768", "mn=sm?-mx:-mx-1", "mn=-mx-1"),
 ("sat: asymmetric clamp is -32767", "mn=sm?-mx:-mx-1", "mn=-mx"),
 ("sat: upper boundary off by one", "const hi=r>mx, lo=r<mn;", "const hi=r>mx+1, lo=r<mn;"),
 ("sat: lower boundary off by one", "const hi=r>mx, lo=r<mn;", "const hi=r>mx, lo=r<mn-1;"),
 ("out: TLAST not carried to the output", "N.out_last=S.res_tlast;", "N.out_last=0;"),
]
src = open(simjs).read(); alive = []; bad = 0
for name, old, new in M:
    if old not in src: print("SKIP (pattern not found):", name); continue
    mut = os.path.join(HERE, "mut.js"); open(mut, "w").write(src.replace(old, new, 1))
    r = subprocess.run([sys.executable, os.path.join(HERE, "validate.py"), mut, build], capture_output=True, text=True)
    last = r.stdout.strip().splitlines()[-1]; caught = r.returncode != 0
    print(("CAUGHT   " if caught else "SURVIVED ") + name + "  [" + last.split(",")[-1].strip() + "]"); sys.stdout.flush()
    if not caught: alive.append(name)
print("\n%d mutants, %d caught, %d survived" % (len(M) - 1, len(M) - 1 - len(alive), len(alive)))
for a in alive: print("  survivor:", a)
