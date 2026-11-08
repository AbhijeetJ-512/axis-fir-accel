// gjs run_model.js <sim.js> <scenario> <mac> <rnd> <stimfile> <expfile>   -- run the page's model headless and write the per-cycle stimulus and expected outputs
const GLib=imports.gi.GLib; const args=ARGV;
const rd=p=>new TextDecoder().decode(GLib.file_get_contents(p)[1]);
const {SIM,fir_step}=new Function(rd(args[0])+';return {SIM,fir_step};')();
const M=SIM.reset(args[1],{mac:+args[2],rnd:+args[3]}); M.cyc=0; const stim=[], exp=[]; let R, done=false;
const h=v=>(v>>>0).toString(16);
while(M.cyc<400){ M.cyc++; R=SIM.eval(M); const i=R.io.inp;
  stim.push([i.awvalid,i.awaddr,i.wvalid,i.wdata,i.wstrb,i.bready,i.arvalid,i.araddr,i.rready,i.tvalid,i.tdata&0xffff,i.tlast,i.tready].map(v=>h(+v)).join(' '));
  exp.push(R.io.out.map(v=>h(+v)).join(' '));
  if(R.done){ done=true; break; } SIM.commit(M,R); }
GLib.file_set_contents(args[4],stim.join('\n')+'\n'); GLib.file_set_contents(args[5],exp.join('\n')+'\n');
print(`${args[1]} mac=${args[2]} rnd=${args[3]}: ${M.cyc} cycles, done=${done}, admitted=${M.n.adm}, outputs=${M.n.out}, sat=${M.n.sat}, slverr=${M.n.slverr}`);
