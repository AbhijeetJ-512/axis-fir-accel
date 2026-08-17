# -----------------------------------------------------------------------------
# cocotb regression driver
#
#   make SUITE=csr                    Phase 1 control-plane suite (default)
#   make SUITE=stream                 Phase 2 stream/flow-control suite
#   make SUITE=stream TESTCASE=test_long_random_stream
#   make SUITE=csr NTAPS_MAX=32 MAC_UNITS=4
#   make SUITE=stream WAVES=1         dump sim/build_*/dump.fst
#
# Parameters are passed to BOTH the RTL (via Icarus -P) and the Python tests
# (via the environment), so the two can never silently disagree.
#
# SIM_BUILD is keyed on the suite AND the parameter set. Without that, changing
# a parameter on the command line does not invalidate the compiled simulation --
# make sees an up-to-date target because no SOURCE changed, and silently re-runs
# the previous elaboration. That produced a full set of false results the first
# time a parameter sweep was attempted (engineering log D-016).
# -----------------------------------------------------------------------------

SIM           ?= icarus
TOPLEVEL_LANG ?= verilog
SUITE         ?= csr

TB_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ROOT   := $(abspath $(TB_DIR)/../..)
RTL    := $(ROOT)/rtl

COMMON_SOURCES := $(RTL)/common/fir_pkg.sv $(RTL)/common/reset_sync.sv

# ---- Suite: csr -------------------------------------------------------------
ifeq ($(SUITE),csr)
  NTAPS_MAX    ?= 64
  MAC_UNITS    ?= 8
  DATA_W       ?= 16
  COEF_W       ?= 16
  SYMMETRIC_EN ?= 1
  export NTAPS_MAX MAC_UNITS DATA_W COEF_W SYMMETRIC_EN

  TOPLEVEL := ctrl_plane_wrap
  MODULE   ?= tests.test_csr
  VERILOG_SOURCES := $(COMMON_SOURCES) \
      $(RTL)/axi_lite/axil_slave.sv \
      $(RTL)/control/csr_regfile.sv \
      $(ROOT)/tb/sv/ctrl_plane_wrap.sv
  PARAM_ARGS := \
      -P$(TOPLEVEL).NTAPS_MAX=$(NTAPS_MAX) \
      -P$(TOPLEVEL).MAC_UNITS=$(MAC_UNITS) \
      -P$(TOPLEVEL).DATA_W=$(DATA_W) \
      -P$(TOPLEVEL).COEF_W=$(COEF_W) \
      -P$(TOPLEVEL).SYMMETRIC_EN=$(SYMMETRIC_EN)
  CFG_TAG := csr_n$(NTAPS_MAX)_m$(MAC_UNITS)_d$(DATA_W)_c$(COEF_W)_s$(SYMMETRIC_EN)
endif

# ---- Suite: stream ----------------------------------------------------------
ifeq ($(SUITE),stream)
  DATA_W         ?= 16
  IN_FIFO_DEPTH  ?= 8
  OUT_FIFO_DEPTH ?= 8
  PIPE_DEPTH     ?= 4
  LONG_BEATS     ?= 100000
  export DATA_W IN_FIFO_DEPTH OUT_FIFO_DEPTH PIPE_DEPTH LONG_BEATS

  TOPLEVEL := axis_loopback_wrap
  MODULE   ?= tests.test_stream
  VERILOG_SOURCES := $(COMMON_SOURCES) \
      $(RTL)/axi_stream/axis_fifo.sv \
      $(RTL)/control/credit_ctr.sv \
      $(ROOT)/tb/sv/axis_loopback_wrap.sv
  PARAM_ARGS := \
      -P$(TOPLEVEL).DATA_W=$(DATA_W) \
      -P$(TOPLEVEL).IN_FIFO_DEPTH=$(IN_FIFO_DEPTH) \
      -P$(TOPLEVEL).OUT_FIFO_DEPTH=$(OUT_FIFO_DEPTH) \
      -P$(TOPLEVEL).PIPE_DEPTH=$(PIPE_DEPTH)
  CFG_TAG := stream_d$(DATA_W)_i$(IN_FIFO_DEPTH)_o$(OUT_FIFO_DEPTH)_p$(PIPE_DEPTH)
endif

ifndef TOPLEVEL
  $(error Unknown SUITE '$(SUITE)'. Use SUITE=csr or SUITE=stream)
endif

# ---- Tool arguments ---------------------------------------------------------
COMPILE_ARGS += -g2012 -I$(RTL)/common $(PARAM_ARGS)

ifeq ($(WAVES),1)
    PLUSARGS     += -fst
    COMPILE_ARGS += -s dump_wave
    VERILOG_SOURCES += $(ROOT)/tb/sv/dump_wave.sv
endif

SIM_BUILD           := $(ROOT)/sim/build_$(CFG_TAG)
COCOTB_RESULTS_FILE := $(ROOT)/sim/results_$(CFG_TAG).xml

export PYTHONPATH := $(TB_DIR):$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
