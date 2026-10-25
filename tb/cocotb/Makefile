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
# RTL_DIR: override to build a mutated copy of rtl/ (scripts/sva_mutants.py). No trailing comment: make would keep the spaces in the value.
RTL_DIR ?= $(ROOT)/rtl
RTL     := $(RTL_DIR)

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

# ---- Suite: fir (Phase 3 arithmetic) ----------------------------------------
ifeq ($(SUITE),fir)
  DATA_W    ?= 16
  COEF_W    ?= 16
  NTAPS_MAX ?= 64
  SYMMETRIC_EN ?= 1
  RAND_SETS ?= 20
  RAND_LEN  ?= 1200
  export DATA_W COEF_W NTAPS_MAX SYMMETRIC_EN RAND_SETS RAND_LEN

  TOPLEVEL := fir_core_wrap
  MODULE   ?= tests.test_fir_math
  VERILOG_SOURCES := $(COMMON_SOURCES) \
      $(RTL)/dsp/delay_line.sv $(RTL)/dsp/mac_array.sv \
      $(RTL)/dsp/acc_reduce.sv $(RTL)/dsp/round_sat.sv \
      $(RTL)/dsp/fir_core.sv \
      $(ROOT)/tb/sv/fir_core_wrap.sv
  PARAM_ARGS := \
      -P$(TOPLEVEL).DATA_W=$(DATA_W) \
      -P$(TOPLEVEL).COEF_W=$(COEF_W) \
      -P$(TOPLEVEL).NTAPS_MAX=$(NTAPS_MAX) \
      -P$(TOPLEVEL).SYMMETRIC_EN=$(SYMMETRIC_EN)
  CFG_TAG := fir_d$(DATA_W)_c$(COEF_W)_n$(NTAPS_MAX)_s$(SYMMETRIC_EN)
endif

# ---- Suite: top (Phase 4 full integration) ----------------------------------
ifeq ($(SUITE),top)
  DATA_W         ?= 16
  COEF_W         ?= 16
  NTAPS_MAX      ?= 32
  MAC_UNITS      ?= 8
  IN_FIFO_DEPTH  ?= 8
  OUT_FIFO_DEPTH ?= 16
  SYMMETRIC_EN   ?= 1
  export DATA_W COEF_W NTAPS_MAX MAC_UNITS IN_FIFO_DEPTH OUT_FIFO_DEPTH SYMMETRIC_EN

  TOPLEVEL := axis_fir_top
  MODULE   ?= tests.test_top
  VERILOG_SOURCES := $(COMMON_SOURCES) \
      $(RTL)/axi_lite/axil_slave.sv $(RTL)/axi_stream/axis_fifo.sv \
      $(RTL)/control/credit_ctr.sv $(RTL)/control/csr_regfile.sv \
      $(RTL)/control/fir_ctrl.sv $(RTL)/mem/coeff_bank.sv \
      $(RTL)/dsp/delay_line.sv $(RTL)/dsp/mac_array.sv \
      $(RTL)/dsp/acc_reduce.sv $(RTL)/dsp/round_sat.sv $(RTL)/dsp/fir_core.sv \
      $(RTL)/top/axis_fir_top.sv
  PARAM_ARGS := \
      -P$(TOPLEVEL).DATA_W=$(DATA_W) \
      -P$(TOPLEVEL).COEF_W=$(COEF_W) \
      -P$(TOPLEVEL).NTAPS_MAX=$(NTAPS_MAX) \
      -P$(TOPLEVEL).MAC_UNITS=$(MAC_UNITS) \
      -P$(TOPLEVEL).IN_FIFO_DEPTH=$(IN_FIFO_DEPTH) \
      -P$(TOPLEVEL).OUT_FIFO_DEPTH=$(OUT_FIFO_DEPTH) \
      -P$(TOPLEVEL).SYMMETRIC_EN=$(SYMMETRIC_EN)
  CFG_TAG := top_n$(NTAPS_MAX)_m$(MAC_UNITS)_o$(OUT_FIFO_DEPTH)_s$(SYMMETRIC_EN)
endif

ifndef TOPLEVEL
  $(error Unknown SUITE '$(SUITE)'. Use SUITE=csr, stream, fir or top)
endif

# ---- Tool arguments ---------------------------------------------------------
ifeq ($(SIM),verilator)
  # Verilator >= 5.036 is required by cocotb 2.x; the repository's checkers (verif/) need the source-built 5.052 (sequence delays,
  # covergroup-capable). Set VERILATOR_BIN_DIR=<verilator-5.052>/bin and VERILATOR_ROOT=<verilator-5.052>. Parameters become -G overrides.
  COMPILE_ARGS += -I$(RTL)/common --assert --coverage --timing --x-assign unique --x-initial unique \
                  -Wno-fatal -Wno-DECLFILENAME -Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM -Wno-WIDTH -Wno-PINCONNECTEMPTY -Wno-UNOPTFLAT \
                  $(subst -P$(TOPLEVEL).,-G,$(PARAM_ARGS))
  ifneq ($(NO_CHECKERS),1)
    CHECKERS ?= $(ROOT)/verif/fir_checkers.sv
    VERILOG_SOURCES += $(CHECKERS) $(ROOT)/verif/fir_binds.sv
  endif
  # +verilator+rand+reset+2 randomises uninitialised state; +verilator+error+limit lets a failing assertion report every label
  # instead of stopping at the first. Coverage goes to COV_FILE (default coverage.dat in the build directory).
  PLUSARGS += +verilator+rand+reset+2 +verilator+error+limit+100000 +verilator+coverage+file+$(SIM_BUILD)/coverage.dat
else
COMPILE_ARGS += -g2012 -I$(RTL)/common $(PARAM_ARGS)
endif

ifeq ($(WAVES),1)
    PLUSARGS     += -fst
    COMPILE_ARGS += -s dump_wave
    VERILOG_SOURCES += $(ROOT)/tb/sv/dump_wave.sv
endif

SIM_BUILD           := $(ROOT)/sim/build_$(CFG_TAG)
COCOTB_RESULTS_FILE := $(ROOT)/sim/results_$(CFG_TAG).xml

export PYTHONPATH := $(TB_DIR):$(PYTHONPATH)

# ---- cocotb reachability ----------------------------------------------------
# cocotb's own Makefile.sim resolves `cocotb-config` with a parse-time
# $(shell ...), and GNU Make's `export PATH` does NOT affect parse-time shell
# calls -- only recipes. So PATH must already be correct when this file is
# parsed. The top-level Makefile arranges that; running make here directly with
# a PATH that lacks cocotb gives an unhelpful "Couldn't find makefile for
# simulator" error several layers down, so it is caught here instead.
ifeq ($(shell command -v cocotb-config >/dev/null 2>&1 && echo ok),)
  $(error cocotb-config is not on PATH. Run the top-level 'make csr|stream|fir', \
          which discovers it, or add the bin directory of the Python that has \
          cocotb installed to PATH.)
endif

include $(shell cocotb-config --makefiles)/Makefile.sim
