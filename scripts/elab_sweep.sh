#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# elab_sweep.sh -- run the Yosys structural gate across several parameter sets.
#
# A latch or a combinational loop can be introduced by ONE parameter value (a
# generate branch that is only taken when NTAPS_MAX fills the coefficient
# region, say), so checking only the default build is not enough.
#
# Usage: ./scripts/elab_sweep.sh          (from the repository root)
# Exit:  0 if every configuration is clean, 1 otherwise.
# -----------------------------------------------------------------------------
set -u
cd "$(dirname "$0")/.." || exit 1

CONFIGS=(
  "64  8  16 16 1"
  "32  4  16 16 1"
  "256 1  16 16 0"
  "16  8  16 16 1"
  "128 16 16 16 1"
)

mkdir -p synthesis/reports
fail=0

for cfg in "${CONFIGS[@]}"; do
  read -r NT MU DW CW SE <<< "$cfg"
  tag="n${NT}_m${MU}_s${SE}"
  log="synthesis/reports/elab_${tag}.log"

  yosys -q -p "
    read_verilog -sv -I rtl/common \
        rtl/common/fir_pkg.sv rtl/common/reset_sync.sv \
        rtl/axi_lite/axil_slave.sv rtl/axi_stream/axis_fifo.sv \
        rtl/control/credit_ctr.sv rtl/control/csr_regfile.sv \
        rtl/dsp/delay_line.sv rtl/dsp/mac_array.sv rtl/dsp/acc_reduce.sv \
        rtl/dsp/round_sat.sv rtl/dsp/fir_core.sv \
        rtl/control/fir_ctrl.sv rtl/mem/coeff_bank.sv rtl/top/axis_fir_top.sv
    chparam -set NTAPS_MAX ${NT} -set MAC_UNITS ${MU} \
            -set DATA_W ${DW} -set COEF_W ${CW} -set SYMMETRIC_EN ${SE} csr_regfile
    chparam -set DATA_W ${DW} -set COEF_W ${CW} -set NTAPS_MAX ${NT} \
            -set SYMMETRIC_EN ${SE} -set MAC_UNITS ${MU} fir_core
    chparam -set NTAPS_MAX ${NT} -set MAC_UNITS ${MU} fir_ctrl
    chparam -set NTAPS_MAX ${NT} -set COEF_W ${CW} coeff_bank
    chparam -set DATA_W ${DW} -set COEF_W ${CW} -set NTAPS_MAX ${NT} \
            -set MAC_UNITS ${MU} -set SYMMETRIC_EN ${SE} axis_fir_top
    hierarchy -check
    proc
    opt_expr
    opt_clean
    select -assert-none t:\$dlatch
    select -assert-none t:\$adlatch
    select -assert-none t:\$_DLATCH_*
    select -assert-none t:\$tribuf
    select -assert-none t:\$_TBUF_*
    check -assert
    stat
  " > "$log" 2>&1

  if [ $? -eq 0 ]; then
    printf '  PASS  NTAPS_MAX=%-4s MAC_UNITS=%-3s SYM=%s   -> %s\n' "$NT" "$MU" "$SE" "$log"
  else
    printf '  FAIL  NTAPS_MAX=%-4s MAC_UNITS=%-3s SYM=%s   -> %s\n' "$NT" "$MU" "$SE" "$log"
    grep -E "ERROR|Assert" "$log" | head -3
    fail=1
  fi
done

echo
[ $fail -eq 0 ] && echo "elab_sweep: all configurations clean (no latches, no tri-states, no loops)" \
                || echo "elab_sweep: FAILURES -- see logs above"
exit $fail
