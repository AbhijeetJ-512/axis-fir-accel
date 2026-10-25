# axis_fir_top.sdc - pre-layout constraints for the PPA sweep (ppa/ppa_sweep.py). The period comes from the environment (CLK_PERIOD_NS).
# Boundary model (assumptions, not measurements): every data input arrives 0.35 ns after the clock edge, every output must be stable
# 0.15 ns before the next edge (about a dfxtp_1 clock-to-Q and setup), inputs are driven by a buf_2, outputs load 0.02 pF.
# The asynchronous reset input is a false path: it enters reset_sync, whose output then drives the asynchronous reset pin of
# every flip-flop through an unbuffered net, which a real flow replaces with a reset tree. Recovery/removal on that net is therefore
# reported separately and is NOT part of the clock-path figure.
set period $::env(CLK_PERIOD_NS)
create_clock -name aclk -period $period [get_ports aclk]
set data_in {}
foreach p [all_inputs] {
  set n [get_property $p name]
  if {$n ne "aclk" && $n ne "aresetn"} { lappend data_in $p }
}
set_input_delay 0.35 -clock aclk $data_in
set_output_delay 0.15 -clock aclk [all_outputs]
set_driving_cell -lib_cell sky130_fd_sc_hd__buf_2 -pin X $data_in
set_load 0.02 [all_outputs]
set_false_path -from [get_ports aresetn]
