# -----------------------------------------------------------------------------
# cocotb regression driver
#
#   make            run the default (Phase 1 control-plane) suite
#   make MODULE=tests.test_csr TESTCASE=test_reset_values   run one test
#   make NTAPS_MAX=32 MAC_UNITS=4                           re-elaborate
#   make WAVES=1                                            dump sim/dump.fst
#
# Parameters are passed to BOTH the RTL (via Icarus -P) and the Python tests
# (via the environment), so the two can never silently disagree -- a mismatch
# shows up immediately as a CAPABILITY register failure.
# -----------------------------------------------------------------------------

SIM           ?= icarus
TOPLEVEL_LANG ?= verilog

TB_DIR   := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ROOT     := $(abspath $(TB_DIR)/../..)
RTL      := $(ROOT)/rtl

# ---- Elaboration parameters -------------------------------------------------
NTAPS_MAX    ?= 64
MAC_UNITS    ?= 8
DATA_W       ?= 16
COEF_W       ?= 16
SYMMETRIC_EN ?= 1

export NTAPS_MAX MAC_UNITS DATA_W COEF_W SYMMETRIC_EN

# ---- Sources ----------------------------------------------------------------
VERILOG_SOURCES := \
  $(RTL)/common/fir_pkg.sv     \
  $(RTL)/common/reset_sync.sv  \
  $(RTL)/axi_lite/axil_slave.sv \
  $(RTL)/control/csr_regfile.sv \
  $(ROOT)/tb/sv/ctrl_plane_wrap.sv

TOPLEVEL := ctrl_plane_wrap
MODULE   ?= tests.test_csr

# ---- Tool arguments ---------------------------------------------------------
COMPILE_ARGS += -g2012
COMPILE_ARGS += -I$(RTL)/common
COMPILE_ARGS += -P$(TOPLEVEL).NTAPS_MAX=$(NTAPS_MAX)
COMPILE_ARGS += -P$(TOPLEVEL).MAC_UNITS=$(MAC_UNITS)
COMPILE_ARGS += -P$(TOPLEVEL).DATA_W=$(DATA_W)
COMPILE_ARGS += -P$(TOPLEVEL).COEF_W=$(COEF_W)
COMPILE_ARGS += -P$(TOPLEVEL).SYMMETRIC_EN=$(SYMMETRIC_EN)

ifeq ($(WAVES),1)
    PLUSARGS += -fst
    COMPILE_ARGS += -s dump_wave
    VERILOG_SOURCES += $(ROOT)/tb/sv/dump_wave.sv
endif

# The build directory is keyed on the parameter set. Without this, changing a
# parameter on the make command line does NOT invalidate the compiled
# simulation -- make sees an up-to-date target because no SOURCE changed, and
# silently re-runs the previous elaboration. That produced a full set of
# false failures the first time the parameter sweep was attempted.
CFG_TAG    := n$(NTAPS_MAX)_m$(MAC_UNITS)_d$(DATA_W)_c$(COEF_W)_s$(SYMMETRIC_EN)
SIM_BUILD  := $(ROOT)/sim/build_$(CFG_TAG)
COCOTB_RESULTS_FILE := $(ROOT)/sim/results_$(CFG_TAG).xml

export PYTHONPATH := $(TB_DIR):$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
