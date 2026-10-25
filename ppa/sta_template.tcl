# OpenSTA, pre-layout, ideal clock. Environment: NETLIST SDC CLK_PERIOD_NS OUTDIR
read_liberty ppa/lib/sky130hd_tt.lib
read_verilog $::env(NETLIST)
link_design axis_fir_top
read_sdc $::env(SDC)
set f [open $::env(OUTDIR)/timing.txt w]
puts $f "# pre-layout STA, ideal clock, no wire parasitics, sky130_fd_sc_hd tt_025C_1v80, period $::env(CLK_PERIOD_NS) ns"
close $f
report_checks -path_delay max -path_group aclk -group_path_count 1 -digits 3 >> $::env(OUTDIR)/timing.txt
report_checks -path_delay max -path_group {**async_default**} -group_path_count 1 -digits 3 >> $::env(OUTDIR)/async.txt
report_worst_slack -max -digits 3 >> $::env(OUTDIR)/timing_all.txt
exit
