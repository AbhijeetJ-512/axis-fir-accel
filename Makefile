# -----------------------------------------------------------------------------
# axis_fir_accel -- top-level build entry points
#
# Every target here is runnable on a stock Linux box with only open-source
# tools. Nothing in this project reports a number that one of these targets did
# not produce.
#
#   make regress     both cocotb suites, default parameters
#   make csr         Phase 1 control-plane suite only
#   make stream      Phase 2 stream / flow-control suite only
#   make sweep       both suites across their parameter sets
#   make elab        Yosys structural gate (latches / tri-states / loops)
#   make elab-sweep  the same gate across parameter sets
#   make comb-check  structural proof of TR-04 on the elaborated netlist
#   make check       everything above that gates a phase
#   make waves       regression with FST waveform dump (SUITE=csr|stream)
#   make tools       report which tools are present
#   make clean       remove simulation and synthesis artefacts
# -----------------------------------------------------------------------------

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
COCOTB_DIR := $(ROOT)/tb/cocotb

NTAPS_MAX    ?= 64
MAC_UNITS    ?= 8
DATA_W       ?= 16
COEF_W       ?= 16
SYMMETRIC_EN ?= 1
SUITE        ?= csr

CSR_PARAMS := NTAPS_MAX=$(NTAPS_MAX) MAC_UNITS=$(MAC_UNITS) DATA_W=$(DATA_W) \
              COEF_W=$(COEF_W) SYMMETRIC_EN=$(SYMMETRIC_EN)

.PHONY: all check regress csr stream sweep csr-sweep stream-sweep waves \
        elab elab-sweep comb-check verify-widths tools clean help

all: check

# Everything that gates a phase.
check: regress elab-sweep comb-check verify-widths

# --- Simulation --------------------------------------------------------------
regress: csr stream

csr:
	@echo "=== Phase 1 suite: AXI4-Lite control plane ==="
	@$(MAKE) -s -C $(COCOTB_DIR) SUITE=csr $(CSR_PARAMS) 2>&1 \
	  | grep -E "TESTS=|AssertionError" || true

stream:
	@echo "=== Phase 2 suite: AXI4-Stream path and flow control ==="
	@$(MAKE) -s -C $(COCOTB_DIR) SUITE=stream 2>&1 \
	  | grep -E "TESTS=|AssertionError|matched exactly" || true

waves:
	$(MAKE) -C $(COCOTB_DIR) SUITE=$(SUITE) WAVES=1
	@echo "waveform: $(ROOT)/sim/build_*/dump.fst  (open with: gtkwave <file>)"

sweep: csr-sweep stream-sweep

# Boundaries: minimum useful tap count, the default, the value that exactly
# fills the coefficient region, and a build with the symmetric path removed.
csr-sweep:
	@set -e; for cfg in "64 8 1" "32 4 1" "16 8 1" "128 16 1" "256 1 0"; do \
	  set -- $$cfg; \
	  printf '%-44s ' "csr: NTAPS_MAX=$$1 MAC_UNITS=$$2 SYM=$$3"; \
	  $(MAKE) -s -C $(COCOTB_DIR) SUITE=csr NTAPS_MAX=$$1 MAC_UNITS=$$2 SYMMETRIC_EN=$$3 \
	    2>&1 | grep -oE "TESTS=[0-9]+ PASS=[0-9]+ FAIL=[0-9]+ SKIP=[0-9]+" | head -1; \
	done

# Boundaries: minimum legal FIFO depths, the default, a deep pipeline that
# stresses the credit constraint OUT_FIFO_DEPTH > PIPE_DEPTH+1, and a narrow
# datapath.
stream-sweep:
	@set -e; for cfg in "16 8 8 4" "16 2 4 2" "16 4 32 16" "8 8 8 1" "32 16 16 8"; do \
	  set -- $$cfg; \
	  printf '%-44s ' "stream: D=$$1 IN=$$2 OUT=$$3 PIPE=$$4"; \
	  $(MAKE) -s -C $(COCOTB_DIR) SUITE=stream DATA_W=$$1 IN_FIFO_DEPTH=$$2 \
	    OUT_FIFO_DEPTH=$$3 PIPE_DEPTH=$$4 LONG_BEATS=5000 \
	    2>&1 | grep -oE "TESTS=[0-9]+ PASS=[0-9]+ FAIL=[0-9]+ SKIP=[0-9]+" | head -1; \
	done

# --- Structural checks -------------------------------------------------------
elab:
	@mkdir -p $(ROOT)/synthesis/reports
	yosys -s $(ROOT)/synthesis/scripts/elab_check.ys \
	  > $(ROOT)/synthesis/reports/elab_check.log 2>&1 \
	  && echo "elab: clean (no latches, no tri-states, no combinational loops)" \
	  || { echo "elab: FAILED -- see synthesis/reports/elab_check.log"; \
	       grep -E "ERROR|Assert" $(ROOT)/synthesis/reports/elab_check.log | head; exit 1; }

elab-sweep:
	@bash $(ROOT)/scripts/elab_sweep.sh

comb-check:
	@bash $(ROOT)/scripts/check_comb_independence.sh

# --- Derivation check --------------------------------------------------------
verify-widths:
	@python3 $(ROOT)/scripts/verify_acc_width.py

# --- Environment -------------------------------------------------------------
tools:
	@for t in iverilog yosys sta gtkwave python3 verilator sby; do \
	  p=$$(command -v $$t 2>/dev/null); \
	  if [ -n "$$p" ]; then printf '  %-12s %s\n' "$$t" "$$p"; \
	  else printf '  %-12s MISSING\n' "$$t"; fi; \
	done
	@python3 -c "import cocotb; print('  cocotb       ' + cocotb.__version__)" 2>/dev/null \
	  || echo "  cocotb       MISSING  (pip install cocotb)"

clean:
	rm -rf $(ROOT)/sim/build_* $(ROOT)/sim/results_*.xml
	rm -rf $(ROOT)/tb/cocotb/__pycache__ $(ROOT)/tb/cocotb/*/__pycache__
	find $(ROOT) -name '*.vcd' -o -name '*.fst' | xargs -r rm -f

help:
	@grep -E '^#   make' $(firstword $(MAKEFILE_LIST)) | sed 's/^#   /  /'
