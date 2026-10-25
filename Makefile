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
#   make fir         Phase 3 FIR arithmetic suite only
#   make fir-full    the >=1M-sample Definition-of-Done run (minutes)
#   make top         Phase 4 full-IP integration suite
#   make fold-sweep  bit-identity across every MAC_UNITS (risk R3)
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

# ---- Python / cocotb discovery ----------------------------------------------
# The system python3 is not necessarily the interpreter cocotb was installed
# into -- on a machine with conda it usually is not. Discover one that can
# import cocotb and put its script directory on PATH, so that every recipe
# below (and the sub-make it launches) can find `cocotb-config`.
#
# This must happen HERE rather than in tb/cocotb/Makefile: cocotb's own
# Makefile.sim resolves cocotb-config with a parse-time $(shell ...), and
# GNU Make's `export PATH` affects recipes but NOT parse-time shell calls. The
# sub-make inherits the exported PATH through the recipe, which is what makes
# it work. Override with: make PYTHON=/path/to/python
PYTHON ?= $(shell for p in python3 python "$$CONDA_PREFIX/bin/python3" \
                    "$$HOME/miniconda3/bin/python3" "$$HOME/anaconda3/bin/python3"; do \
                    { command -v "$$p" >/dev/null 2>&1 || [ -x "$$p" ]; } || continue; \
                    "$$p" -c 'import cocotb' >/dev/null 2>&1 || continue; \
                    command -v "$$p" 2>/dev/null || echo "$$p"; break; \
                  done)
COCOTB_BIN := $(if $(PYTHON),$(shell $(PYTHON) -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>/dev/null))
export PATH := $(if $(COCOTB_BIN),$(COCOTB_BIN):)$(PATH)

NTAPS_MAX    ?= 64
MAC_UNITS    ?= 8
DATA_W       ?= 16
COEF_W       ?= 16
SYMMETRIC_EN ?= 1
SUITE        ?= csr

CSR_PARAMS := NTAPS_MAX=$(NTAPS_MAX) MAC_UNITS=$(MAC_UNITS) DATA_W=$(DATA_W) \
              COEF_W=$(COEF_W) SYMMETRIC_EN=$(SYMMETRIC_EN)

ppa-sweep:
	@python3 ppa/ppa_sweep.py

.PHONY: ppa-sweep all check regress csr stream fir fir-full top fold-sweep sweep csr-sweep stream-sweep waves \
        elab elab-sweep comb-check verify-widths verify-model tools clean help

all: check

# Everything that gates a phase.
check: regress elab-sweep comb-check verify-widths verify-model

# --- Simulation --------------------------------------------------------------
regress: csr stream fir top

csr:
	@echo "=== Phase 1 suite: AXI4-Lite control plane ==="
	@scripts/run_suite.sh "TESTS=|AssertionError" $(COCOTB_DIR) SUITE=csr $(CSR_PARAMS)

stream:
	@echo "=== Phase 2 suite: AXI4-Stream path and flow control ==="
	@scripts/run_suite.sh "TESTS=|AssertionError|matched exactly" $(COCOTB_DIR) SUITE=stream

fir:
	@echo "=== Phase 3 suite: FIR arithmetic (bit-exact) ==="
	@scripts/run_suite.sh "TESTS=|AssertionError|BIT-EXACT|peak \||LATENCY =" $(COCOTB_DIR) SUITE=fir NTAPS_MAX=$(NTAPS_MAX)

# The Phase 3 Definition-of-Done run: >= 1M random samples. Kept separate from
# `make fir` because at 64 taps Icarus evaluates 64 multipliers per cycle, so
# the full run takes minutes -- too slow to sit inside the routine phase gate,
# which needs to stay fast enough that people actually run it.
fir-full:
	@echo "=== Phase 3 DoD run: >=1M samples, 20 coefficient sets, 6 mode pairs ==="
	@scripts/run_suite.sh "TESTS=|AssertionError|BIT-EXACT|peak \|" $(COCOTB_DIR) SUITE=fir NTAPS_MAX=$(NTAPS_MAX) \
	  RAND_SETS=20 RAND_LEN=50000

top:
	@echo "=== Phase 4 suite: full IP integration, MAC_UNITS=$(MAC_UNITS) ==="
	@scripts/run_suite.sh "TESTS=|AssertionError|BIT-EXACT|II:|bit-identical" $(COCOTB_DIR) SUITE=top NTAPS_MAX=32 MAC_UNITS=$(MAC_UNITS)

# Risk R3: in a folded configuration the delay line must shift once per OUTPUT
# SAMPLE, not once per pass. Every MAC_UNITS must give results bit-identical to
# the MAC_UNITS-independent reference model -- which implies they match each
# other. This is the single highest-value test in the project.
fold-sweep:
	@set -e; for m in 1 2 4 8 16 32; do \
	  printf '%-34s ' "MAC_UNITS=$$m"; \
	  $(MAKE) -s -C $(COCOTB_DIR) SUITE=top NTAPS_MAX=32 MAC_UNITS=$$m 2>&1 \
	    | grep -oE "TESTS=[0-9]+ PASS=[0-9]+ FAIL=[0-9]+ SKIP=[0-9]+" | head -1; \
	done

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

verify-model:
	@python3 $(ROOT)/scripts/verify_ref_model.py

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
