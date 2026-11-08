#!/bin/bash
# build_rtl.sh <repo-root> <outdir>: compile axis_fir_top replay testbench for MAC_UNITS 1,2,4
R=$1; O=$2; mkdir -p $O
SRC="$R/rtl/common/fir_pkg.sv $R/rtl/common/reset_sync.sv $R/rtl/axi_lite/axil_slave.sv $R/rtl/axi_stream/axis_fifo.sv $R/rtl/control/credit_ctr.sv $R/rtl/control/csr_regfile.sv $R/rtl/control/fir_ctrl.sv $R/rtl/mem/coeff_bank.sv $R/rtl/dsp/delay_line.sv $R/rtl/dsp/mac_array.sv $R/rtl/dsp/acc_reduce.sv $R/rtl/dsp/round_sat.sv $R/rtl/dsp/fir_core.sv $R/rtl/top/axis_fir_top.sv"
for m in 1 2 4; do iverilog -g2012 -I$R/rtl/common -Ptb.MAC=$m -o $O/sim_mac$m $SRC $(dirname $0)/tb_replay.sv || exit 1; done
echo built
