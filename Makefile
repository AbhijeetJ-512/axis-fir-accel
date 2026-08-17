# -----------------------------------------------------------------------------
# axis_fir_accel -- top-level build entry points
#
# Every target here is runnable on a stock Linux box with only open-source
# tools. Nothing in this project reports a number that one of these targets did
# not produce.
#
#   make regress    cocotb regression, default parameter set
#   make sweep      regression across the supported parameter sets
#   make elab       Yosys structural gate (latches / tri-states / loops)
#   make elab-sweep the same gate across parameter sets
#   make waves      regression with FST waveform dump
#   make tools      report which tools are present
#   make clean      remove simulation and synthesis artefacts
# -----------------------------------------------------------------------------

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
COCOTB_DIR := $(ROOT)/tb/cocotb

NTAPS_MAX    ?= 64
MAC_UNITS    ?= 8
DATA_W       ?= 16
COEF_W       ?= 16
SYMMETRIC_EN ?= 1

PARAMS := NTAPS_MAX=$(NTAPS_MAX) MAC_UNITS=$(MAC_UNITS) DATA_W=$(DATA_W) \
          COEF_W=$(COEF_W) SYMMETRIC_EN=$(SYMMETRIC_EN)

.PHONY: all regress sweep waves elab elab-sweep verify-widths tools clean help

all: regress elab

# --- Simulation --------------------------------------------------------------
regress:
	$(MAKE) -C $(COCOTB_DIR) $(PARAMS)

waves:
	$(MAKE) -C $(COCOTB_DIR) $(PARAMS) WAVES=1
	@echo "waveform: $(ROOT)/sim/build_*/dump.fst  (open with: gtkwave <file>)"

# Parameter sets chosen to exercise the boundaries: the minimum useful tap
# count, the default, the maximum that fills the coefficient region, and a
# build with the symmetric datapath removed.
sweep:
	@set -e; for cfg in "64 8 1" "32 4 1" "16 8 1" "128 16 1" "256 1 0"; do \
	  set -- $$cfg; \
	  echo "=== NTAPS_MAX=$$1 MAC_UNITS=$$2 SYMMETRIC_EN=$$3 ==="; \
	  $(MAKE) -s -C $(COCOTB_DIR) NTAPS_MAX=$$1 MAC_UNITS=$$2 SYMMETRIC_EN=$$3 \
	    2>&1 | grep -E "TESTS=|AssertionError" || true; \
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
