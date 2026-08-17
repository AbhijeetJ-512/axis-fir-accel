#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# check_comb_independence.sh
#
# Establishes requirement TR-04 structurally, on the elaborated netlist:
#
#   s_axis_tready must have NO combinational dependence on m_axis_tready.
#
# Why this cannot be a simulation check: simulation samples behaviour, it does
# not establish the absence of a path. A combinational loop through an
# interconnect only manifests when a particular master is attached, which the
# testbench never is. The property is structural, so it is checked structurally.
#
# Method: Yosys `%cie*` walks the cone of influence through COMBINATIONAL cells
# only, stopping at sequential elements. Intersecting that cone with the input
# port must give the empty set.
#
# The script SELF-TESTS first, against a pair of tiny modules where the answer
# is known -- one with a direct wire, one through a flop. A check that cannot
# demonstrate a failure is not evidence of anything, so if the self-test does
# not produce exactly one FOUND and one clean, this script aborts rather than
# reporting a false pass.
#
# Usage: ./scripts/check_comb_independence.sh      (from the repository root)
# -----------------------------------------------------------------------------
set -u
cd "$(dirname "$0")/.." || exit 1

TOP="axis_loopback_wrap"
SOURCES="rtl/common/fir_pkg.sv rtl/common/reset_sync.sv \
         rtl/axi_stream/axis_fifo.sv rtl/control/credit_ctr.sv \
         tb/sv/axis_loopback_wrap.sv"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# --- probe: is <src> in the combinational input cone of <dst>? ---------------
# echoes "FOUND" or "clean"
probe () {
  local files="$1" top="$2" dst="$3" src="$4"
  local out
  out=$(yosys -qp "
      read_verilog -sv -I rtl/common $files
      hierarchy -top $top
      proc
      opt
      flatten
      select -set cone o:$dst %cie*
      select -assert-none @cone i:$src %i
  " 2>&1)
  if echo "$out" | grep -q "Assert"; then echo "FOUND"; else echo "clean"; fi
}

# =============================================================================
# 1. Self-test -- prove the check is capable of failing
# =============================================================================
cat > "$WORK/selftest.sv" <<'EOF'
module st_bad (input logic clk, rst_n, src, output logic dst);
  assign dst = src;                                   // direct combinational path
endmodule
module st_good (input logic clk, rst_n, src, output logic dst);
  logic q;
  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) q <= 1'b0; else q <= src;
  assign dst = q;                                     // path is broken by a flop
endmodule
EOF

bad=$(probe "$WORK/selftest.sv" st_bad dst src)
good=$(probe "$WORK/selftest.sv" st_good dst src)

echo "self-test: direct-wire module   = $bad   (expect FOUND)"
echo "self-test: through-a-flop module = $good  (expect clean)"

if [ "$bad" != "FOUND" ] || [ "$good" != "clean" ]; then
  echo
  echo "ABORT: the check does not discriminate. A 'clean' result would be meaningless."
  exit 2
fi
echo

# =============================================================================
# 2. The real checks
# =============================================================================
fail=0

check () {   # description, dst, src
  local desc="$1" dst="$2" src="$3" r
  r=$(probe "$SOURCES" "$TOP" "$dst" "$src")
  if [ "$r" = "clean" ]; then
    printf '  PASS  %-58s no combinational path\n' "$desc"
  else
    printf '  FAIL  %-58s COMBINATIONAL PATH EXISTS\n' "$desc"
    fail=1
  fi
}

echo "$TOP:"
# TR-04: the requirement. A path here would let an interconnect close a loop.
check "m_axis_tready -> s_axis_tready  (TR-04)"  s_axis_tready  m_axis_tready
# Hygiene: no valid feedthrough either, so chained instances do not build up
# an unbounded combinational path across a stream pipeline.
check "s_axis_tvalid -> m_axis_tvalid  (no feedthrough)" m_axis_tvalid s_axis_tvalid
check "s_axis_tdata  -> m_axis_tdata   (no feedthrough)" m_axis_tdata  s_axis_tdata

echo
if [ $fail -eq 0 ]; then
  echo "comb-independence: all checks pass"
else
  echo "comb-independence: FAILURES -- the stream interfaces are not safely composable"
fi
exit $fail
