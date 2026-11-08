// Cycle-accurate model of axis_fir_top (rtl/top/axis_fir_top.sv and every module under it), plus the testbench-side AXI4-Lite master,
// AXI4-Stream source and sink. fir_step() mirrors the always_comb / always_ff blocks; its comments name the RTL block it copies.
// Instance modelled: DATA_W=COEF_W=16, NTAPS_MAX=16, IN_FIFO_DEPTH=4, OUT_FIFO_DEPTH=8, SYMMETRIC_EN=1, MAC_UNITS selectable (1, 2, 4).
const clog2=n=>{let r=0;while((1<<r)<n)r++;return r;};
const sx=(v,b)=>{v&=(1<<b)-1;return v>=(1<<(b-1))?v-(1<<b):v;};
const u16=v=>v&0xffff, h4=v=>(v&0xffff).toString(16).padStart(4,'0'), h8=v=>(v>>>0).toString(16).padStart(8,'0');
const hx=v=>'0x'+(v>>>0).toString(16), mod=(x,m)=>((x%m)+m)%m;
function applyStrb(o,n,s){ let m=0; for(let b=0;b<4;b++){ const sel=((s>>b)&1)?n:o; m|=((sel>>>(8*b))&255)<<(8*b); } return m>>>0; }
const REG={ID:0,CAP:4,CTRL:8,CFG:12,STATUS:16,IRQ_ST:20,IRQ_EN:24,SCNT:28,FCNT:32};
const CSRA=[0,4,8,12,16,20,24,28,32], ROA=[0,4,16,28,32];
const STN=['IDLE','RUN','DRAIN','CFGERR'];
function mkP(mac){ const L=clog2(mac); return {DW:16,CW:16,NMAX:16,MAC:mac,INF:4,OUTF:8,LOG2MAC:L,LEVELS:L,NPAD:1<<L,DLY:2+L,IDXW:4,ACC_W:36,SHIFT:15,CNTW:clog2(8+1),SYM_EN:1}; }
const fifoNew=D=>({mem:new Array(D).fill(null),wr:0,rd:0,D,L:clog2(D)});
const fCnt=f=>(f.wr-f.rd)&(2*f.D-1), fEmpty=f=>f.wr===f.rd, fFull=f=>(((f.wr>>f.L)&1)!==((f.rd>>f.L)&1))&&((f.wr&(f.D-1))===(f.rd&(f.D-1)));
const fCopy=f=>({mem:f.mem.slice(),wr:f.wr,rd:f.rd,D:f.D,L:f.L});
function mkS(P){
  const z=n=>new Array(n).fill(0);
  return {P,aw_full:0,w_full:0,awaddr_q:0,wdata_q:0,wstrb_q:0,bvalid:0,bresp:0,rvalid:0,rdata_q:0,rresp_q:0,
    en:0,ntaps:0,round:0,sat:0,sym:0,irq_st:0,irq_en:0,sat_sticky:0,cfgerr_sticky:0,scnt:0,fcnt:0,c_rst:0,c_commit:0,c_flush:0,c_cnt:0,
    bank0:new Array(P.NMAX).fill(null),bank1:new Array(P.NMAX).fill(null),bank_sel:0,pending:0,
    inf:fifoNew(P.INF),outf:fifoNew(P.OUTF),credits:P.OUTF,
    state:0,busy:0,pcnt:0,tlast_q:0,dp:0,cur_tag:-1,
    dl:z(P.NMAX),opnd:z(P.MAC),coefq:z(P.MAC),prod:z(P.MAC),node:z(P.NPAD),
    ch_vld:z(P.DLY),ch_first:z(P.DLY),ch_last:z(P.DLY),ch_tlast:z(P.DLY),ch_tag:z(P.DLY).fill(-1),
    fold:0,res_acc:0,res_vld:0,res_tlast:0,res_tag:-1,out_valid:0,out_data:0,out_last:0,out_sat:0,out_tag:-1};
}
// rtl/dsp/round_sat.sv
function roundSat(acc,rm,sm,SH){
  const f=Math.floor(acc/2**SH), guard=Math.floor(mod(acc,2**SH)/2**(SH-1)), sticky=mod(acc,2**(SH-1))!==0?1:0, lsb=mod(f,2);
  const inc=rm===1?guard:rm===2?(guard&(sticky|lsb)):0, r=f+inc, mx=2**15-1, mn=sm?-mx:-mx-1;
  const hi=r>mx, lo=r<mn;
  return {y:hi?mx:lo?mn:r,sat:hi||lo,floor:f,guard,sticky,lsb,inc};
}
// One clock cycle. inp: awvalid awaddr wvalid wdata wstrb bready arvalid araddr rready tvalid tdata tlast tready (+ tag for display)
function fir_step(S,inp){
  const P=S.P, N={...S}, g={};
  // ---------------------------------------------------------------- axil_slave
  const slot=!S.bvalid; g.awready=slot&&!S.aw_full; g.wready=slot&&!S.w_full;
  g.aw_hs=!!(inp.awvalid&&g.awready); g.w_hs=!!(inp.wvalid&&g.wready);
  const aw_have=S.aw_full||g.aw_hs, w_have=S.w_full||g.w_hs;
  g.reg_wen=aw_have&&w_have&&slot;
  const waddr=S.aw_full?S.awaddr_q:inp.awaddr, wd=S.w_full?S.wdata_q:inp.wdata, ws=S.w_full?S.wstrb_q:inp.wstrb;
  g.arready=!S.rvalid; g.ar_hs=!!(inp.arvalid&&g.arready); g.reg_ren=g.ar_hs; const raddr=inp.araddr;
  // ---------------------------------------------------------------- csr_regfile: decode
  const w12=waddr&0xfff, r12=raddr&0xfff;
  const w_csr_region=((w12>>8)&15)===0, w_coef_region=((w12>>10)&3)===1, w_idx=(w12>>2)&255, w_coef_valid=w_coef_region&&w_idx<P.NMAX;
  const w_csr_mapped=w_csr_region&&CSRA.includes(w12), w_is_ro=ROA.includes(w12);
  const r_csr_region=((r12>>8)&15)===0, r_coef_region=((r12>>10)&3)===1, r_idx=(r12>>2)&255, r_coef_valid=r_coef_region&&r_idx<P.NMAX;
  const r_csr_mapped=r_csr_region&&CSRA.includes(r12);
  const sts_busy=S.state!==0, sts_drain=S.state===2;
  const cfg_illegal=(S.ntaps===0||S.ntaps>P.NMAX)||S.round===3;
  const w_mapped=w_csr_mapped||w_coef_valid, cfg_wr_blocked=w_csr_mapped&&w12===REG.CFG&&sts_busy;
  const reg_werr=!w_mapped||cfg_wr_blocked;
  const csr_wr=g.reg_wen&&w_csr_mapped&&!w_is_ro&&!reg_werr;
  const ctrl_rd=S.en, cfg_rd=(S.ntaps|(S.round<<16)|(S.sat<<20)|(S.sym<<24))>>>0;
  const wr_ctrl=applyStrb(ctrl_rd,wd,ws), wr_cfg=applyStrb(cfg_rd,wd,ws), wr_ie=applyStrb(S.irq_en,wd,ws), wr_clr=applyStrb(0,wd,ws);
  const cap=(((P.SYM_EN<<31)|((P.ACC_W&127)<<24)|(P.CW<<16)|(P.DW<<8)|((P.LOG2MAC&15)<<4)|(clog2(P.NMAX)&15))>>>0);
  g.cap=cap;
  // ---------------------------------------------------------------- shared signals
  const soft_clear=!!(S.c_rst||S.c_flush);
  const in_tvalid=!fEmpty(S.inf), inh=S.inf.mem[S.inf.rd&(P.INF-1)]||{d:0,l:0,id:-1};
  const out_tvalid=!fEmpty(S.outf), outh=S.outf.mem[S.outf.rd&(P.OUTF-1)]||{d:0,l:0,id:-1};
  const out_pop=!!(out_tvalid&&inp.tready), out_push=!!S.out_valid;
  const have_credit=S.credits!==0;
  // ---------------------------------------------------------------- fir_ctrl (comb)
  const h_taps=((S.ntaps+1)>>1)&511, n_eff=S.sym?h_taps:S.ntaps, n_pass=((n_eff+P.MAC-1)&511)>>P.LOG2MAC, p_last=n_pass===0?0:(n_pass-1)&511;
  const last_pass=!!S.busy&&S.pcnt===p_last, can_accept=!S.busy||last_pass;
  const sample_start=S.state===1&&in_tvalid&&have_credit&&can_accept;
  const tap_base=(S.pcnt<<P.LOG2MAC)&511;
  let state_n=S.state;
  switch(S.state){ case 0: if(S.en) state_n=cfg_illegal?3:1; break; case 1: if(!S.en||S.c_flush) state_n=2; break;
    case 2: if(!S.busy&&S.dp===0) state_n=0; break; case 3: if(!S.en) state_n=0; break; }
  // events
  const evt_frame_done=out_pop&&!!outh.l, evt_sat=!!(S.out_valid&&S.out_sat), cfg_err_event=!!(S.en&&cfg_illegal);
  // ---------------------------------------------------------------- csr: read mux
  const status_rd=((!sts_busy&&!sts_drain?1:0)|(sts_busy?2:0)|(S.pending?4:0)|(S.cfgerr_sticky?8:0)|(S.sat_sticky?16:0)|(fEmpty(S.inf)?32:0)|(fFull(S.outf)?64:0)|(sts_drain?128:0));
  let rdata=0, rerr=1;
  if(r_csr_mapped){ rerr=0; rdata={0:0x46495230,4:cap,8:ctrl_rd,12:cfg_rd,16:status_rd,20:S.irq_st,24:S.irq_en,28:S.scnt,32:S.fcnt}[r12]>>>0; }
  else if(r_coef_valid){ rerr=0; rdata=((S.bank_sel?S.bank0:S.bank1)[r_idx])||0; }
  g.rdata_comb=rdata; g.rerr=rerr; g.reg_werr=reg_werr;
  // ---------------------------------------------------------------- axil_slave: registers
  N.aw_full=S.aw_full; N.w_full=S.w_full; N.bvalid=S.bvalid; N.bresp=S.bresp; N.awaddr_q=S.awaddr_q; N.wdata_q=S.wdata_q; N.wstrb_q=S.wstrb_q;
  if(g.reg_wen){ N.aw_full=0; N.w_full=0; N.bvalid=1; N.bresp=reg_werr?2:0; }
  else { if(g.aw_hs){ N.aw_full=1; N.awaddr_q=inp.awaddr; } if(g.w_hs){ N.w_full=1; N.wdata_q=inp.wdata; N.wstrb_q=inp.wstrb; } }
  if(S.bvalid&&inp.bready) N.bvalid=0;
  if(g.ar_hs){ N.rvalid=1; N.rdata_q=rdata; N.rresp_q=rerr?2:0; } else if(S.rvalid&&inp.rready) N.rvalid=0;
  // ---------------------------------------------------------------- csr: registers
  N.c_rst=0; N.c_commit=0; N.c_flush=0; N.c_cnt=0; N.en=S.en; N.ntaps=S.ntaps; N.round=S.round; N.sat=S.sat; N.sym=S.sym; N.irq_en=S.irq_en; N.irq_st=S.irq_st;
  if(csr_wr){ switch(w12){
    case REG.CTRL: N.en=wr_ctrl&1; N.c_rst=(wr_ctrl>>1)&1; N.c_commit=(wr_ctrl>>2)&1; N.c_flush=(wr_ctrl>>3)&1; N.c_cnt=(wr_ctrl>>4)&1; break;
    case REG.CFG: N.ntaps=wr_cfg&511; N.round=(wr_cfg>>16)&3; N.sat=(wr_cfg>>20)&1; N.sym=P.SYM_EN?(wr_cfg>>24)&1:0; break;
    case REG.IRQ_EN: N.irq_en=wr_ie&7; break;
    case REG.IRQ_ST: N.irq_st=S.irq_st&~(wr_clr&7); break; } }
  if(evt_frame_done) N.irq_st|=1; if(evt_sat) N.irq_st|=2; if(cfg_err_event) N.irq_st|=4;
  N.sat_sticky=S.sat_sticky; N.cfgerr_sticky=S.cfgerr_sticky;
  if(evt_sat) N.sat_sticky=1; if(cfg_err_event) N.cfgerr_sticky=1;
  if(csr_wr&&w12===REG.IRQ_ST){ if((wr_clr&2)&&!evt_sat) N.sat_sticky=0; if((wr_clr&4)&&!cfg_err_event) N.cfgerr_sticky=0; }
  if(S.c_cnt){ N.scnt=0; N.fcnt=0; } else { N.scnt=S.scnt+(sample_start?1:0); N.fcnt=S.fcnt+(evt_frame_done?1:0); }
  g.irq=(S.irq_st&S.irq_en)!==0;
  // ---------------------------------------------------------------- coeff_bank
  const coef_we=!!(g.reg_wen&&w_coef_valid);
  N.bank0=S.bank0; N.bank1=S.bank1;
  if(coef_we){ if(S.bank_sel){ N.bank0=S.bank0.slice(); N.bank0[w_idx]=wd&0xffff; } else { N.bank1=S.bank1.slice(); N.bank1[w_idx]=wd&0xffff; } }
  const act=S.bank_sel?S.bank1:S.bank0, swap_now=!!(S.pending&&(evt_frame_done||!sts_busy));
  N.bank_sel=S.bank_sel; N.pending=S.pending;
  if(swap_now){ N.bank_sel=S.bank_sel^1; N.pending=S.c_commit; } else if(S.c_commit) N.pending=1;
  // ---------------------------------------------------------------- fifos, credit
  N.inf=fCopy(S.inf); N.outf=fCopy(S.outf);
  const in_wr=!!(inp.tvalid&&!fFull(S.inf)); g.s_tready=!fFull(S.inf);
  if(in_wr) N.inf.mem[S.inf.wr&(P.INF-1)]={d:inp.tdata,l:inp.tlast?1:0,id:inp.tag};
  if(soft_clear){ N.inf.wr=0; N.inf.rd=0; } else { if(in_wr) N.inf.wr=(S.inf.wr+1)&(2*P.INF-1); if(in_tvalid&&sample_start) N.inf.rd=(S.inf.rd+1)&(2*P.INF-1); }
  const out_wr=!!(out_push&&!fFull(S.outf));
  if(out_wr) N.outf.mem[S.outf.wr&(P.OUTF-1)]={d:S.out_data,l:S.out_last,id:S.out_tag};
  if(out_wr) N.outf.wr=(S.outf.wr+1)&(2*P.OUTF-1); if(out_pop) N.outf.rd=(S.outf.rd+1)&(2*P.OUTF-1);
  const out_cnt=fCnt(S.outf), out_cnt_n=(out_cnt+(out_push?1:0)-(out_pop?1:0))&(2*P.OUTF-1);
  const reload=((P.OUTF-(out_cnt_n&((1<<P.CNTW)-1)))&((1<<P.CNTW)-1));
  if(soft_clear) N.credits=reload; else if(sample_start&&!out_pop) N.credits=S.credits-1; else if(!sample_start&&out_pop) N.credits=S.credits+1;
  // ---------------------------------------------------------------- fir_ctrl (regs)
  N.cur_tag=S.cur_tag;
  if(soft_clear){ N.state=0; N.busy=0; N.pcnt=0; N.tlast_q=0; N.dp=0; }
  else { N.state=state_n; if(sample_start){ N.tlast_q=inh.l; N.cur_tag=inh.id; }
    if(S.busy){ if(last_pass){ N.busy=sample_start?1:0; N.pcnt=0; } else N.pcnt=S.pcnt+1; } else { N.busy=sample_start?1:0; N.pcnt=0; }
    N.dp=S.dp+(sample_start?1:0)-(S.out_valid?1:0); }
  const pass_vld=S.busy, pass_first=!!S.busy&&S.pcnt===0, pass_last=last_pass;
  // ---------------------------------------------------------------- fir_core: delay line
  N.dl=S.dl;
  if(soft_clear) N.dl=new Array(P.NMAX).fill(0); else if(sample_start) N.dl=[inh.d].concat(S.dl.slice(0,P.NMAX-1));
  // stage 1: operand / coefficient select (pre-adder)
  const opn=[], cfc=[], act_=[];
  for(let k=0;k<P.MAC;k++){
    const kf=(tap_base+k)&511, ki=kf&(P.NMAX-1), near=S.dl[ki];
    const active=S.sym?(kf<h_taps):(kf<S.ntaps);
    if(S.busy&&active&&act[ki]===null) throw new Error('active tap '+ki+' accumulates a coefficient that was never written');   // the RTL would propagate X
    cfc.push(active?sx(act[ki]||0,16):0);
    const farf=(S.ntaps-1-kf)&511, fi=farf&(P.NMAX-1), far=S.dl[fi], mid=fi===ki;
    opn.push(!active?0:!S.sym?near:mid?near:near+far); act_.push(active);
  }
  N.opnd=opn; N.coefq=cfc; N.prod=S.opnd.map((a,i)=>a*S.coefq[i]);
  // stage 3: adder tree (one register per level)
  const NP=P.NPAD; let tree_acc;
  if(P.LEVELS===0){ tree_acc=S.prod[0]; N.node=S.node; }
  else { N.node=S.node.slice(); for(let i=NP/2;i<NP;i++) N.node[i]=S.prod[2*i-NP]+S.prod[2*i+1-NP]; for(let i=1;i<NP/2;i++) N.node[i]=S.node[2*i]+S.node[2*i+1]; tree_acc=S.node[1]; }
  // control chain
  N.ch_vld=S.ch_vld.slice(); N.ch_first=S.ch_first.slice(); N.ch_last=S.ch_last.slice(); N.ch_tlast=S.ch_tlast.slice(); N.ch_tag=S.ch_tag.slice();
  if(soft_clear){ N.ch_vld=new Array(P.DLY).fill(0); }
  else { N.ch_vld[0]=pass_vld?1:0; N.ch_first[0]=pass_first?1:0; N.ch_last[0]=pass_last?1:0; N.ch_tlast[0]=S.tlast_q; N.ch_tag[0]=S.cur_tag;
    for(let i=1;i<P.DLY;i++){ N.ch_vld[i]=S.ch_vld[i-1]; N.ch_first[i]=S.ch_first[i-1]; N.ch_last[i]=S.ch_last[i-1]; N.ch_tlast[i]=S.ch_tlast[i-1]; N.ch_tag[i]=S.ch_tag[i-1]; } }
  const acc_vld=!!S.ch_vld[P.DLY-1], acc_first=!!S.ch_first[P.DLY-1], acc_last=!!S.ch_last[P.DLY-1], acc_tlast=S.ch_tlast[P.DLY-1], acc_tag=S.ch_tag[P.DLY-1];
  // stage 4: temporal accumulation
  const acc_next=acc_first?tree_acc:S.fold+tree_acc;
  N.fold=S.fold; N.res_acc=S.res_acc; N.res_vld=S.res_vld; N.res_tlast=S.res_tlast; N.res_tag=S.res_tag;
  if(soft_clear){ N.res_vld=0; }
  else { if(acc_vld) N.fold=acc_next; N.res_vld=(acc_vld&&acc_last)?1:0; if(acc_vld&&acc_last){ N.res_acc=acc_next; N.res_tlast=acc_tlast; N.res_tag=acc_tag; } }
  // stage 5: round / saturate / register
  const rs=roundSat(S.res_acc,S.round,S.sat,P.SHIFT);
  N.out_valid=S.out_valid; N.out_data=S.out_data; N.out_last=S.out_last; N.out_sat=S.out_sat; N.out_tag=S.out_tag;
  if(soft_clear){ N.out_valid=0; N.out_sat=0; }
  else { N.out_valid=S.res_vld?1:0; if(S.res_vld){ N.out_data=rs.y; N.out_last=S.res_tlast; N.out_sat=rs.sat?1:0; N.out_tag=S.res_tag; } else N.out_sat=0; }
  Object.assign(g,{waddr,wd,ws,w12,r12,w_idx,r_idx,csr_wr,coef_we,w_csr_mapped,w_is_ro,w_mapped,cfg_wr_blocked,soft_clear,in_tvalid,inh,out_tvalid,outh,out_pop,out_push,have_credit,
    h_taps,n_eff,n_pass,p_last,last_pass,sample_start,tap_base,state_n,evt_frame_done,evt_sat,cfg_err_event,sts_busy,sts_drain,swap_now,cfg_illegal,
    acc_vld,acc_first,acc_last,acc_tag,tree_acc,acc_next,rs,in_wr,out_wr,out_cnt,in_cnt:fCnt(S.inf),reload,pass_vld,pass_first,pass_last,act:act_,opn,cfc});
  return {g,N};
}
// ============================================================================================ testbench-side models
const Q=x=>Math.max(-32768,Math.min(32767,Math.round(x*32768)));
const cfgw=(n,rm,sat,sym)=>(n|(rm<<16)|(sat<<20)|(sym<<24))>>>0;
const CO=i=>0x400+4*i;
const W=(a,d,x={})=>({k:'w',a,d:d>>>0,...x}), Rd=(a,x={})=>({k:'r',a,...x});
function regName(a){ const n={0:'ID',4:'CAPABILITY',8:'CTRL',12:'CFG',16:'STATUS',20:'IRQ_STATUS',24:'IRQ_ENABLE',28:'SAMPLE_CNT',32:'FRAME_CNT'};
  if(n[a]!==undefined) return n[a]; if(a>=0x400&&a<0x800) return 'COEF['+((a-0x400)>>2)+']'; return hx(a); }
function coefOps(list){ return list.map((v,i)=>W(CO(i),u16(v),i%3===1?{wlate:2}:i%3===2?{alate:2}:{})); }
function items(vals,lastAt,d0=0){ return vals.map((v,i)=>({v,last:lastAt.includes(i)?1:0,d:i===0?d0:0})); }
const SC=[
 {id:'lowpass',name:'Programmed low-pass, N = 5',desc:'Software writes CFG and five coefficients over AXI4-Lite, commits the bank, sets ENABLE, then streams one 8-sample frame. Read CAPABILITY first, and SAMPLE_CNT, FRAME_CNT and STATUS at the end; CTRL.CNT_CLR then zeroes the counters.',
  mk:o=>({ops:[Rd(REG.CAP),W(REG.CFG,cfgw(5,o.rnd,0,0)),...coefOps([Q(.1),Q(.2),Q(.4),Q(.2),Q(.1)]),W(REG.CTRL,4),W(REG.CTRL,1),Rd(REG.FCNT,{when:'drained'}),Rd(REG.SCNT),Rd(REG.STATUS),W(REG.CTRL,0x11),Rd(REG.SCNT),Rd(REG.FCNT)],
    src:items([0,16384,16384,16384,16384,16384,0,0],[7]),sink:[]})},
 {id:'sym',name:'Symmetric N = 5 (pre-adder)',desc:'Linear-phase filter with CFG.SYM = 1: only the first 3 coefficients are written (h[2] is the centre tap), and CFG is set one byte at a time with byte strobes (the other bytes of each write are junk that must be ignored). Each MAC adds x[n-k] and x[n-(N-1-k)] before multiplying; the centre tap is counted once.',
  mk:o=>({ops:[W(REG.CFG,0x13370005,{s:1}),W(REG.CFG,(0xAB00FF00|(o.rnd<<16))>>>0,{s:4}),W(REG.CFG,0x01AA5500,{s:8}),...coefOps([2000,6000,12000]),W(REG.CTRL,4),W(REG.CTRL,1),Rd(REG.STATUS,{when:'drained'}),Rd(REG.SCNT)],
    src:items([8000,-8000,12000,4000,-16000,2000,0,0],[7]),sink:[]})},
 {id:'fold',name:'Resource folding, N = 8',desc:'Eight taps through MAC_UNITS multipliers need P = ceil(8 / MAC_UNITS) passes per sample. Change MAC_UNITS above: the initiation interval follows, and the input FIFO fills when P > 1.',
  mk:o=>({ops:[W(REG.CFG,cfgw(8,o.rnd,0,0)),...coefOps(new Array(8).fill(4096)),W(REG.CTRL,4),W(REG.CTRL,1),Rd(REG.SCNT,{when:'drained'}),Rd(REG.FCNT)],
    src:items([16000,16000,16000,16000,16000,16000],[5]),sink:[]})},
 {id:'sat',name:'Saturation: symmetric then asymmetric',desc:'Gain close to 2 overflows the output format. Frame 1 clamps to ±32767 (symmetric); pairs of samples land exactly on the clamp boundaries. Then ENABLE is dropped, CFG switched to asymmetric and frame 2 clamps to −32768. SAT_EVENT is enabled in IRQ_ENABLE; software clears it with write-1-to-clear, and one clear lands on the very cycle of a new saturation, which must win.',
  mk:o=>({ops:[W(REG.IRQ_EN,7),W(REG.CFG,cfgw(2,o.rnd,1,0)),...coefOps([32767,32767]),W(REG.CTRL,4),W(REG.CTRL,1),Rd(REG.IRQ_ST,{out:9}),W(REG.CTRL,0),Rd(REG.STATUS,{when:'idle'}),W(REG.IRQ_ST,7),W(REG.CFG,cfgw(2,o.rnd,0,0)),W(REG.CTRL,1),
      W(REG.IRQ_ST,2,{when:'satlast'}),Rd(REG.IRQ_ST),Rd(REG.STATUS),W(REG.IRQ_ST,2),Rd(REG.STATUS),Rd(REG.IRQ_ST)],
    src:[...items([30000,30000,-30000,-30000,0,0,-16385,-16384,0],[8]),...items([-16385,-16384,0,16385,16384,0,-30000,-30000,0,30000,30000],[10],60)],sink:[]})},
 {id:'tie',name:'Rounding of exact ties',desc:'One tap of 0.5 on odd inputs gives results exactly half an LSB apart. The Rounding option (truncate, half-up, convergent) decides each of them; convergent rounds ties to even.',
  mk:o=>({ops:[W(REG.CFG,cfgw(1,o.rnd,0,0)),W(CO(0),16384),W(REG.CTRL,4),W(REG.CTRL,1),Rd(REG.SCNT,{when:'drained'})],
    src:items([1,3,5,-1,-3,-5,2,4],[7]),sink:[]})},
 {id:'bp',name:'Output back-pressure and credits',desc:'The sink holds m_axis_tready low. The credit counter admits at most OUT_FIFO_DEPTH samples; then admission stops, the input FIFO fills and s_axis_tready falls. in_flight + out FIFO occupancy never exceeds 8.',
  mk:o=>({ops:[W(REG.CFG,cfgw(1,o.rnd,0,0)),W(CO(0),24576),W(REG.CTRL,4),W(REG.CTRL,1),Rd(REG.STATUS,{t:40}),Rd(REG.STATUS,{when:'drained'}),Rd(REG.SCNT)],
    src:items([100,200,300,400,500,600,700,800,900,1000,1100,1200,1300,1400,1500,1600,1700,1800,1900,2000],[19]),sink:[[1,70,0]]})},
 {id:'reload',name:'Coefficient reload at a frame boundary',desc:'While frame A is in the filter, software writes new coefficients to the shadow bank and requests a commit. STATUS.COEFF_PENDING stays high until the TLAST beat of frame A leaves the IP, then the banks swap; frame B uses the new gain. A second commit is timed to land on the swap cycle itself: it is kept, so COEFF_PENDING stays high and the banks swap again at the next frame boundary.',
  mk:o=>({ops:[W(REG.CFG,cfgw(1,o.rnd,0,0)),W(CO(0),16384),W(REG.CTRL,4),W(REG.CTRL,1),W(CO(0),32767,{t:13}),Rd(CO(0)),W(REG.CTRL,5),W(REG.CTRL,5,{when:'tlastq'}),Rd(REG.STATUS),Rd(REG.STATUS,{when:'drained'})],
    src:[...items([16000,16000,16000,16000,16000,16000],[5]),...items([16000,16000,16000,16000,16000,16000],[5],34)],sink:[]})},
 {id:'cfgerr',name:'Errors: bad config and SLVERR',desc:'ENABLE with NTAPS = 0 enters CFGERR. In that state a CFG write is refused (SLVERR), as are writes to unmapped addresses, coefficient indices at or above NTAPS_MAX, and reads of unmapped addresses; a write to a read-only register is accepted and ignored. Clearing CFG_ERR while ENABLE is still high fails: the condition re-asserts it every cycle.',
  mk:o=>({ops:[W(REG.IRQ_EN,7),W(REG.CTRL,1),Rd(REG.STATUS),W(REG.IRQ_ST,4),Rd(REG.IRQ_ST),W(REG.CFG,cfgw(4,0,0,0)),W(0x0F0,1),Rd(0x300),W(CO(20),1),Rd(CO(20)),W(REG.ID,0x1234),Rd(REG.ID),W(REG.CTRL,0),Rd(REG.IRQ_ST),W(REG.IRQ_ST,4),Rd(REG.STATUS)],
    src:[],sink:[],endWhen:'ops'})},
 {id:'drain',name:'Dropping ENABLE mid-stream',desc:'ENABLE is cleared while samples are in flight. The FSM goes RUN → DRAIN and carries every admitted sample to the output before returning to IDLE; SAMPLE_CNT then equals the number of beats that left.',
  mk:o=>({ops:[W(REG.CFG,cfgw(4,o.rnd,0,0)),...coefOps([8192,8192,8192,8192]),W(REG.CTRL,4),W(REG.CTRL,1),W(REG.CTRL,0,{adm:5}),Rd(REG.STATUS),Rd(REG.STATUS,{when:'idle'}),Rd(REG.SCNT),Rd(REG.FCNT)],
    src:items([4000,4000,4000,4000,4000,4000,4000,4000,4000,4000,4000,4000],[11]),sink:[],endWhen:'ops'})},
 {id:'flush',name:'Flush with samples in the pipeline',desc:'CTRL.FLUSH while samples are in flight and the sink is stalled. The input FIFO, delay line and pipeline are cleared, beats already in the output FIFO stay, and the credit counter is reloaded to OUT_FIFO_DEPTH minus what the output FIFO still holds. This is the case where the as-found RTL lost beats. A later CTRL.SOFT_RESET clears the pipeline the same way.',
  mk:o=>({ops:[W(REG.CFG,cfgw(2,o.rnd,0,0)),...coefOps([16384,16384]),W(REG.CTRL,4),W(REG.CTRL,1),W(REG.CTRL,9,{adm:6}),Rd(REG.STATUS),W(REG.CTRL,3,{t:40}),Rd(REG.SCNT,{when:'drained'}),Rd(REG.FCNT)],
    src:items([1000,2000,3000,4000,5000,6000,7000,8000,9000,10000,11000,12000],[11]),sink:[[1,52,0]]})},
];
// stream source / sink process (the testbench idiom: wait a delay, raise VALID, hold until the partner signal is seen at an edge)
function srcArm(x){ if(!x.q.length){x.sig=0;x.cur=null;x.cnt=-1;return;} x.cur=x.q.shift(); x.cnt=x.cur.d; x.sig=x.cnt===0?1:0; }
const sinkReady=(M,c)=>{ for(const r of M.sc.sink) if(c>=r[0]&&c<=r[1]) return r[2]; return 1; };
function mstReady(M,op,S,cyc){ if(cyc<(op.t||0)) return false; if(op.adm!==undefined&&M.n.adm<op.adm) return false; if(op.out!==undefined&&M.n.out<op.out) return false;
  const srcEnd=M.sc.src.length===0||(M.src.open&&!M.src.cur&&M.src.q.length===0);
  if(op.when==='satlast') return !!(S.out_valid&&S.out_sat&&S.dp===1&&srcEnd&&fEmpty(S.inf));   // issue so that the write lands on the cycle of the final saturation event
  if(op.when==='tlastq') return !!(S.out_valid&&S.out_last&&(fEmpty(S.outf)||(fCnt(S.outf)===1&&sinkReady(M,cyc)))&&sinkReady(M,cyc+1)); // issue so that the commit pulse lands on the cycle the TLAST beat leaves
  if(op.when==='drained'){ const srcDone=M.sc.src.length===0||(M.src.open&&!M.src.cur&&M.src.q.length===0);
    return srcDone&&S.dp===0&&fEmpty(S.inf)&&fEmpty(S.outf)&&!S.busy&&S.ch_vld.every(v=>!v)&&!S.res_vld&&!S.out_valid; }
  if(op.when==='idle') return S.state===0&&S.dp===0;
  return true; }
function mstLoad(M,cyc){
  const m=M.mst; if(m.ph!=='gap'&&m.ph!=='start') return;
  if(m.i>=M.sc.ops.length){ m.ph='end'; m.awv=m.wv=m.arv=m.bready=m.rready=0; return; }
  if(m.timer>0){ m.timer--; return; }
  const op=M.sc.ops[m.i];
  if(!mstReady(M,op,M.S,cyc)) return;
  m.ph='issue'; m.awdone=0; m.wdone=0; op.start=op.start||cyc;
  if(op.k==='w'){ m.acnt=op.alate||0; m.wcnt=op.wlate||0; m.awv=m.acnt?0:1; m.wv=m.wcnt?0:1; m.bready=1; m.rready=0; m.arv=0; m.awa=op.a; m.wd=op.d; m.ws=op.s===undefined?15:op.s; }
  else { m.arv=1; m.rready=1; m.bready=0; m.awv=0; m.wv=0; m.ara=op.a; }
}
const SIM={
 scenarios:SC.map(s=>({id:s.id,name:s.name,desc:s.desc})),
 options:[
  {id:'mac',label:'MAC_UNITS',def:4,choices:[{v:1,label:'1'},{v:2,label:'2'},{v:4,label:'4'}],desc:v=>v===4?'II = ceil(N_eff / 4)':'II = ceil(N_eff / '+v+')'},
  {id:'rnd',label:'Rounding',def:2,choices:[{v:0,label:'trunc'},{v:1,label:'half-up'},{v:2,label:'convergent'}],desc:v=>['floor','ties toward +inf','ties to even'][v]}],
 chartTitle:'Sample timeline',rowLabel:'sample',chartWindow:64,maxCycles:400,preroll:3,
 chartHint:'one row per stream sample · in = into input FIFO, q = waiting, A = admitted, P = fold pass, X M T Σ = operand reg, product, adder tree, fold add, R = round/sat, Q = output reg, o = output FIFO, O = popped',
 reset(id,o){
  const sc=SC.find(x=>x.id===id)||SC[0], P=mkP(o.mac||4), spec=sc.mk({rnd:o.rnd===undefined?2:o.rnd});
  spec.src=spec.src||[]; spec.sink=spec.sink||[];
  const M={sc:spec,P,S:mkS(P),mst:{i:0,ph:'start',timer:0,awv:0,wv:0,arv:0,bready:0,rready:0,awa:0,wd:0,ws:0,ara:0,awdone:0,wdone:0,acnt:0,wcnt:0},
   src:{q:spec.src.map((s,i)=>({...s,id:i})),open:false,sig:0,cur:null,cnt:-1},txns:[],rows:{},n:{out:0,adm:0,sat:0,slverr:0},prev:null,rec:[],spec};
  M.spec.src.forEach((s,i)=>{ const t={id:i,label:`x[${i}] = ${s.v}${s.last?'  (TLAST)':''}`,color:`var(--t${i%8})`,cells:{},notes:[],status:'waiting for the stream to start',sq:false}; M.txns.push(t); });
  mstLoad(M,1);
  return M;
 },
 eval(M){
  const S=M.S, P=M.P, cyc=M.cyc, ms=M.mst;
  const inp={awvalid:ms.awv,awaddr:ms.awv?ms.awa:0,wvalid:ms.wv,wdata:ms.wv?ms.wd:0,wstrb:ms.wv?ms.ws:0,bready:ms.bready,arvalid:ms.arv,araddr:ms.arv?ms.ara:0,rready:ms.rready,
    tvalid:M.src.sig,tdata:M.src.sig&&M.src.cur?M.src.cur.v:0,tlast:M.src.sig&&M.src.cur?M.src.cur.last:0,tag:M.src.sig&&M.src.cur?M.src.cur.id:-1,tready:sinkReady(M,cyc)};
  const {g,N}=fir_step(S,inp); const ev=[], act=new Set(), A=(...x)=>x.forEach(i=>act.add(i));
  const sample_start=g.sample_start;
  // ---- chart rows (display only)
  const cell=(id,t,col,cls,title)=>{ if(id<0||!M.txns[id]) return; M.txns[id].cells[cyc]={t,col,cls:cls||'',title:title||''}; };
  const note=(id,col,t,txt)=>{ if(id>=0&&M.txns[id]) M.txns[id].notes.push({c:cyc,t,col,txt}); };
  for(let i=S.inf.rd,n=0;n<fCnt(S.inf);n++,i=(i+1)&(2*P.INF-1)){ const e=S.inf.mem[i&(P.INF-1)]; if(!e) continue; const adm=n===0&&sample_start;
    if(adm){ cell(e.id,'A','--b2','','admitted: popped from the input FIFO, delay line shifts'); note(e.id,'--b2','A',`admitted (pop from input FIFO, delay line shifts once); ${g.n_pass} pass${g.n_pass===1?'':'es'} follow`); }
    else { const why=n===0?(S.state!==1?'FSM not in RUN':!g.have_credit?'no credit':S.busy&&!g.last_pass?'pass phase running':''):'behind another sample'; cell(e.id,'q','--muted',n===0&&why?'st':'','waiting in the input FIFO'+(why?' ('+why+')':'')); if(n===0&&why) note(e.id,'--muted','q','waiting at the head of the input FIFO: '+why); } }
  if(g.in_wr){ cell(inp.tag,'in','--b0','','written into the input FIFO'); note(inp.tag,'--b0','in',`TVALID and TREADY: pushed into the input FIFO (${g.in_cnt+1}/${P.INF})`); M.txns[inp.tag].status='in flight'; }
  if(S.busy&&S.cur_tag>=0){ cell(S.cur_tag,'P'+S.pcnt,'--b2','',`pass ${S.pcnt} of ${g.n_pass}: taps ${g.tap_base}..${g.tap_base+P.MAC-1}`); note(S.cur_tag,'--b2','P'+S.pcnt,`pass ${S.pcnt}/${g.n_pass-1}: MAC units take taps ${g.tap_base}..${g.tap_base+P.MAC-1} (operands and coefficients selected this cycle)`); }
  for(let i=0;i<P.DLY;i++) if(S.ch_vld[i]&&S.ch_last[i]&&S.ch_tag[i]>=0){ const t=i===0?'X':i===1?'M':i===P.DLY-1?'S':'T'; const nm={X:'operand and coefficient registers',M:'product registers',T:'adder tree level',S:'adder tree output, fold accumulate'}[t];
    cell(S.ch_tag[i],t==='S'?'Σ':t,'--b3','',nm); note(S.ch_tag[i],'--b3',t==='S'?'Σ':t,'last pass: '+nm+(t==='S'?` (acc = ${g.acc_next})`:'')); }
  if(S.res_vld){ cell(S.res_tag,'R','--b4','','round and saturate'); note(S.res_tag,'--b4','R',`round_sat: acc ${g.rs.floor} (+${g.rs.inc}) → ${g.rs.y}${g.rs.sat?'  SATURATED':''}`); }
  if(S.out_valid){ cell(S.out_tag,'Q','--b4','','output register, pushed into the output FIFO this cycle'); note(S.out_tag,'--b4','Q',`output register: y = ${sx(S.out_data,16)}${S.out_sat?' (saturated)':''}, pushed into the output FIFO`); }
  for(let i=S.outf.rd,n=0;n<fCnt(S.outf);n++,i=(i+1)&(2*P.OUTF-1)){ const e=S.outf.mem[i&(P.OUTF-1)]; if(!e) continue;
    if(n===0&&g.out_pop){ cell(e.id,'O','--b1','','popped: m_axis handshake'); note(e.id,'--b1','O',`m_axis beat: y = ${sx(e.d,16)}${e.l?' TLAST':''}`); M.txns[e.id].status=`output ${sx(e.d,16)}`; }
    else cell(e.id,'o','--b1',n===0?'st':'','waiting in the output FIFO'); }
  // ---- events
  const T=hx;
  if(g.aw_hs) ev.push(['mem',`AW handshake ${regName(inp.awaddr)} (${T(inp.awaddr)})`]);
  if(g.reg_wen){ const nm=regName(g.waddr); ev.push([g.reg_werr?'trap':'mem',`AXI-Lite write ${nm} ← ${T(g.wd)} → ${g.reg_werr?'SLVERR':'OKAY'}${g.reg_werr&&g.cfg_wr_blocked?' (CFG is read-only while busy)':g.reg_werr?' (unmapped)':g.w_is_ro&&g.w_csr_mapped?' (read-only: ignored)':''}`]); if(g.reg_werr) M.n.slverr++; }
  if(g.ar_hs){ ev.push([g.rerr?'trap':'mem',`AXI-Lite read ${regName(inp.araddr)} → ${g.rerr?'SLVERR':T(g.rdata_comb)}`]); if(g.rerr) M.n.slverr++; }
  if(g.coef_we) ev.push(['data',`coefficient bank: shadow[${g.w_idx}] ← ${sx(g.wd,16)} (bank ${S.bank_sel?0:1} is the shadow)`]);
  if(S.c_commit) ev.push(['ctl','COEFF_COMMIT: bank swap pending until a frame boundary or idle']);
  if(g.swap_now) ev.push(['ctl',`coefficient banks swap (${g.evt_frame_done?'TLAST beat left the IP':'FSM idle'}): bank ${S.bank_sel^1} is now active`]);
  if(g.state_n!==S.state&&!g.soft_clear) ev.push([g.state_n===3?'trap':'ctl',`FSM ${STN[S.state]} → ${STN[g.state_n]}${S.state===0&&g.state_n===3?' (CFG illegal: NTAPS or rounding mode)':''}${S.state===1&&g.state_n===2?' (ENABLE dropped: draining admitted samples)':''}`]);
  if(g.soft_clear) ev.push(['trap',`soft_clear (${S.c_flush?'FLUSH':'SOFT_RESET'}): input FIFO, delay line and pipeline cleared; credits ← ${g.reload} (OUT_FIFO_DEPTH − ${P.OUTF-g.reload} beats still in the output FIFO)`]);
  if(sample_start) ev.push(['ctl',`admit sample ${g.inh.id} (${sx(g.inh.d,16)}${g.inh.l?', TLAST':''}): credit taken, delay line shifts; ${g.n_pass} pass${g.n_pass===1?'':'es'} of ${P.MAC} MAC${P.MAC===1?'':'s'}`]);
  if(S.state===1&&g.in_tvalid&&!sample_start&&!g.have_credit) ev.push(['fwd','admission blocked: no credit (in_flight + output FIFO is full)']);
  if(g.evt_sat){ ev.push(['trap',`output saturated: SAT_EVENT, STATUS.SAT sticky${(S.irq_en&2)?', irq asserted':''}`]); M.n.sat++; }
  if(g.cfg_err_event&&!S.cfgerr_sticky) ev.push(['trap','CFG_ERR: ENABLE with an illegal CFG; sticky status set, interrupt raised']);
  if(g.out_pop){ ev.push(['ret',`m_axis beat: y = ${sx(g.outh.d,16)}${g.outh.l?'  TLAST (frame done)':''}  [sample ${g.outh.id}]`]); M.n.out++; }
  if(sample_start) M.n.adm++;
  if(!g.s_tready&&inp.tvalid) ev.push(['info','input FIFO full: s_axis_tready low']);
  // ---- active wires
  const stRead=g.ar_hs&&g.r12===REG.STATUS;
  if(inp.awvalid||inp.wvalid||inp.arvalid) A('E01'); if(S.bvalid||S.rvalid) A('E02'); if(g.reg_wen||g.reg_ren) A('E03','E04');
  if(g.coef_we) A('E05'); if(S.pending||(g.ar_hs&&g.r12>=0x400)) A('E06'); if(S.c_commit) A('E07');
  if(S.state!==0) A('E08'); if(S.busy) A('E09'); if(S.res_vld) A('E10'); if(g.irq) A('E11'); if(g.soft_clear) A('E12');
  if(sample_start||stRead) A('E13'); if(stRead) A('E14'); if(g.evt_frame_done||fFull(S.outf)) A('E15'); if(g.evt_sat) A('E16');
  if(inp.tvalid) A('E20','E21'); if(g.in_tvalid) A('E22'); if(sample_start) A('E23','E24','E25','E41');
  if(S.busy) A('E26','E27','E28','E29'); if(S.ch_vld[0]) A('E30'); if(S.ch_vld[1]) A('E31'); if(g.acc_vld) A('E32','E33'); if(S.res_vld) A('E34');
  if(S.out_valid) A('E35','E36'); if(g.out_tvalid) A('E37'); if(inp.tready&&g.out_tvalid) A('E38'); if(g.out_pop) A('E39'); if(S.state===1) A('E40');
  if(g.evt_frame_done) A('E42'); if(S.pending) A('E43');
  if(S.pending||g.coef_we) A('E50'); if(g.in_wr) A('E51'); if(S.res_vld) A('E52'); if(g.out_wr) A('E53');
  // ---- display values
  const vals={ctrl:STN[S.state],credit:`${S.credits}/${P.OUTF}`,infifo:`${g.in_cnt}/${P.INF}`,outfifo:`${g.out_cnt}/${P.OUTF}`,csr:(S.en?'ENABLE':'')+(g.irq?' IRQ':''),coef:`bank${S.bank_sel}${S.pending?' pending':''}`,
    dl:`x[n]=${S.dl[0]}`,fold:S.busy||g.acc_vld?`acc ${g.acc_next}`:'',rs:S.res_vld?`y=${g.rs.y}`:'',chain:`P=${g.n_pass}`,prep:`pass ${S.busy?S.pcnt:'-'}/${g.n_pass-1}`};
  const marks={}; if(S.state===3) marks.ctrl='trapped'; else if(S.state===1&&g.in_tvalid&&!sample_start) marks.ctrl='stalled'; if(!g.have_credit) marks.credit='stalled'; if(g.evt_sat) marks.rs='trapped'; if(g.irq) marks.irq='trapped'; if(fFull(S.outf)) marks.outfifo='stalled'; if(!g.s_tready) marks.infifo='stalled'; if(g.soft_clear) marks.csr='trapped';
  const occ={H:inp.awvalid||inp.wvalid||inp.arvalid?`${ms.ph==='issue'?(M.sc.ops[ms.i].k==='w'?'write ':'read '):''}${regName(M.sc.ops[ms.i]?M.sc.ops[ms.i].a:0)}`:'idle',
    C:`bank${S.bank_sel} active${S.pending?', commit pending':''}  ${S.en?'ENABLE':'disabled'}`,F:`${STN[S.state]}  credits ${S.credits}/${P.OUTF}`,
    D:S.busy?`pass ${S.pcnt}/${g.n_pass-1}`:(g.acc_vld?'accumulating':'idle'),O:`FIFO ${g.out_cnt}/${P.OUTF}${g.out_pop?'  popping':''}`};
  const chips=[['ENABLE',S.en,'ctl'],['stall: no credit',S.state===1&&g.in_tvalid&&!g.have_credit,'fwd'],['sample_start',sample_start,'ctl'],['soft_clear',g.soft_clear,'trap'],['irq',g.irq,'trap'],['SLVERR',S.bresp===2&&S.bvalid||S.rresp_q===2&&S.rvalid,'trap'],
    ['s_tready',g.s_tready,'data'],['m_tvalid',g.out_tvalid,'data'],['m_tready',inp.tready,'data'],['out_pop',g.out_pop,'mem'],['pending',S.pending,'ctl'],['sat',g.evt_sat,'trap'],['frame_done',g.evt_frame_done,'ctl']];
  // ---- done?
  const opsDone=ms.ph==='end', pipeEmpty=S.dp===0&&fEmpty(S.outf)&&!S.busy&&!S.res_vld&&!S.out_valid;
  const srcDone=M.src.open&&!M.src.cur&&M.src.q.length===0&&fEmpty(S.inf);
  const done=opsDone&&pipeEmpty&&(M.sc.endWhen==='ops'||srcDone||M.spec.src.length===0);
  const io={inp,out:[g.awready,g.wready,S.bvalid,S.bresp,g.arready,S.rvalid,S.rdata_q,S.rresp_q,g.s_tready,g.out_tvalid,g.out_tvalid?g.outh.d&0xffff:0,g.out_tvalid?g.outh.l:0,g.irq?1:0,S.credits,S.state,S.dp,g.in_cnt,g.out_cnt,S.bank_sel,S.pending,S.irq_st,S.scnt,S.fcnt]};
  M.rec.push(io);
  return {act:[...act],lit:['ctrl'],marks,vals,occ,chips,events:ev,done,doneText:'scenario complete',sig:{g,inp,N},io};
 },
 commit(M,R){
  const {g,inp,N}=R.sig, ms=M.mst, cyc=M.cyc;
  // AXI-Lite master process
  if(ms.ph==='issue'){ const op=M.sc.ops[ms.i];
    if(op.k==='w'){ if(g.aw_hs){ ms.awv=0; ms.awdone=1; } if(g.w_hs){ ms.wv=0; ms.wdone=1; }
      if(!ms.awdone&&!ms.awv&&ms.acnt>0){ ms.acnt--; if(ms.acnt===0) ms.awv=1; }
      if(!ms.wdone&&!ms.wv&&ms.wcnt>0){ ms.wcnt--; if(ms.wcnt===0) ms.wv=1; }
      if(M.S.bvalid&&ms.bready){ op.res=M.S.bresp; op.done=true; ms.i++; ms.ph='gap'; ms.timer=1; ms.bready=0; } }
    else { if(g.ar_hs){ ms.arv=0; } if(M.S.rvalid&&ms.rready){ op.res=M.S.rresp_q; op.val=M.S.rdata_q; op.done=true; ms.i++; ms.ph='gap'; ms.timer=1; ms.rready=0; } } }
  // stream source: opens when ENABLE is first seen high
  const src=M.src;
  if(src.open){ if(src.sig&&g.s_tready){ src.sig=0; srcArm(src); } else if(src.cnt>0){ src.cnt--; if(src.cnt===0) src.sig=1; } }
  M.prev={en:M.S.en,ntaps:M.S.ntaps,state:M.S.state,credits:M.S.credits};
  M.S=N;
  if(!src.open&&M.S.en){ src.open=true; srcArm(src); }
  mstLoad(M,cyc+1);
 },
 stats(M){ return [['admitted',M.n.adm],['output beats',M.n.out],['credits',M.S.credits+' / '+M.P.OUTF]]; },
 listing(M){
  const out=[];
  M.sc.ops.forEach((op,i)=>{ const st=op.done?(op.res===2?'SLVERR':'OKAY')+(op.k==='r'&&op.res!==2?' → '+hx(op.val):''):(i===M.mst.i&&M.mst.ph==='issue'?'in progress':'pending');
    out.push({tx:null,badge:op.k==='w'?'W':'R',badgeCol:op.k==='w'?'--b1':'--b0',html:`${op.k==='w'?'write':'read '} ${regName(op.a)}${op.k==='w'?' ← '+hx(op.d):''} <span class="cm"># ${st}</span>`}); });
  M.spec.src.forEach((s,i)=>out.push({tx:i,badge:'S',badgeCol:'--b2',html:`sample ${i}: ${s.v}${s.last?' TLAST':''} <span class="cm"># ${M.txns[i].status}</span>`}));
  return out;
 },
 stateHTML(M,R){
  const S=M.S, P=M.P, pv=M.prev||{}, reg=(n,v,ch)=>`<div class="reg${ch?' chg':''}"><span class="r">${n}</span><span class="v">${v}</span></div>`;
  let h='<div class="regs">'+reg('FSM',STN[S.state],pv.state!==undefined&&pv.state!==S.state)+reg('credits',`${S.credits}/${P.OUTF}`,pv.credits!==undefined&&pv.credits!==S.credits)+reg('CTRL.ENABLE',S.en,pv.en!==undefined&&pv.en!==S.en)+reg('NTAPS',S.ntaps,pv.ntaps!==undefined&&pv.ntaps!==S.ntaps)
   +reg('ROUND',['trunc','half-up','convergent','reserved'][S.round],false)+reg('SAT',S.sat?'symmetric':'asymmetric',false)+reg('SYM',S.sym,false)+reg('IRQ_STATUS','0b'+S.irq_st.toString(2).padStart(3,'0'),false)
   +reg('IRQ_ENABLE','0b'+S.irq_en.toString(2).padStart(3,'0'),false)+reg('SAMPLE_CNT',S.scnt,false)+reg('FRAME_CNT',S.fcnt,false)+reg('active bank',S.bank_sel,false)+'</div>';
  const act=S.bank_sel?S.bank1:S.bank0, sh=S.bank_sel?S.bank0:S.bank1, fmt=a=>a.map((v,i)=>v===null?null:`h[${i}]=${sx(v,16)}`).filter(Boolean).join('  ')||'(none written)';
  h+=`<div class="small muted">active coefficients</div><div class="small mono">${fmt(act)}</div><div class="small muted">shadow coefficients</div><div class="small mono">${fmt(sh)}</div>`;
  h+=`<div class="small muted">delay line x[n], x[n-1], …</div><div class="small mono">${S.dl.slice(0,Math.max(S.ntaps,2)).join(', ')}</div>`;
  return h;
 },
};
