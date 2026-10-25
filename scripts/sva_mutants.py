#!/usr/bin/env python3
"""sva_mutants.py - injected-bug campaign for the FIR accelerator's verification: do the cocotb tests and the bound checkers catch deliberate bugs?

    sva_mutants.py [--jobs N] [--only M01,M07] [--selftest]      -> results/sva/mutants.txt

Each mutant is one source edit of the RTL, applied to a copy. The copy is built with Verilator 5.052 together with the checkers and run through the
cocotb suites that contain the edited module (the top suite always). One run answers two questions at once, because the checkers do not change
behaviour: did a cocotb test fail (the TESTS= summary shows FAIL > 0, or the run did not finish), and did a checker assertion fire?
  KILLED by tests            a cocotb test failed (assertions may also have fired)
  KILLED by an assertion only  no cocotb test failed, an assertion fired: the checkers saw what the tests could not
  SURVIVED                   neither: the bug is not detected (listed with the reason when it is understood)
A mutant also records which assertion it was meant to trip; the campaign reports a MISMATCH if none of the expected ones fired, and fails if
any mutant is INVALID (the edit text is not found exactly once, or the copy does not build), if a mutant that is expected to be caught survives, or
if an assertion defined in verif/fir_checkers.sv never fires in any run (the assertion was never shown to be able to fail).
--selftest only checks that every edit applies exactly once and that the list is consistent.
"""
import argparse
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sva_lib as L  # noqa: E402

ROOT = L.ROOT
AX, FF, CR, CT, CB, CS, TP, FC, DL, RS = ("rtl/axi_lite/axil_slave.sv", "rtl/axi_stream/axis_fifo.sv", "rtl/control/credit_ctr.sv", "rtl/control/fir_ctrl.sv",
                                          "rtl/mem/coeff_bank.sv", "rtl/control/csr_regfile.sv", "rtl/top/axis_fir_top.sv", "rtl/dsp/fir_core.sv",
                                          "rtl/dsp/delay_line.sv", "rtl/dsp/round_sat.sv")
# id, file, old text (exactly once), new text, description, expected assertion labels (any one must fire; [] = not expected to be seen by an assertion)
MUTANTS = [
    ("M01", AX, "      if (bvalid_q && s_axil_bready) begin\n        bvalid_q <= 1'b0;", "      if (bvalid_q) begin\n        bvalid_q <= 1'b0;", "AXI-Lite: the write response is dropped without waiting for BREADY", ["a_b_hold"]),
    ("M02", AX, "      end else if (rvalid_q && s_axil_rready) begin\n        rvalid_q <= 1'b0;", "      end else if (rvalid_q) begin\n        rvalid_q <= 1'b0;", "AXI-Lite: the read response is dropped without waiting for RREADY", ["a_r_hold"]),
    ("M03", AX, "        bresp_q   <= reg_werr ? fir_pkg::RESP_SLVERR : fir_pkg::RESP_OKAY;", "        bresp_q   <= fir_pkg::RESP_OKAY;", "AXI-Lite: a refused write is answered OKAY", ["a_bresp_err"]),
    ("M04", AX, "        rresp_q  <= reg_rerr ? fir_pkg::RESP_SLVERR : fir_pkg::RESP_OKAY;", "        rresp_q  <= fir_pkg::RESP_OKAY;", "AXI-Lite: a read of an unmapped address is answered OKAY", ["a_rresp_err"]),
    ("M05", AX, "  assign s_axil_arready = !rvalid_q;", "  assign s_axil_arready = 1'b1;", "AXI-Lite: ARREADY ignores an unanswered read", ["a_r_blocks_rd"]),
    ("M06", AX, "  assign wr_slot_free = !bvalid_q;", "  assign wr_slot_free = 1'b1;", "AXI-Lite: a new write is accepted while a response is outstanding", ["a_b_blocks_wr", "a_wen_slot_free"]),
    ("M07", AX, "  assign reg_ren   = ar_hs;", "  assign reg_ren   = s_axil_arvalid;", "AXI-Lite: the register read strobe follows ARVALID, not the handshake", ["a_ren_is_hs"]),
    ("M08", FF, "  assign s_tready = !full;", "  assign s_tready = !full || m_tready;", "FIFO: TREADY depends on the consumer's TREADY (and accepts a beat when full)", ["a_tready_def"]),
    ("M09", FF, "  assign m_tvalid = !empty;", "  assign m_tvalid = !empty || s_tvalid;", "FIFO: TVALID is raised for a beat that is not stored yet", ["a_tvalid_def"]),
    ("M10", FF, "  assign full  = (wr_ptr[PTR_W-1] != rd_ptr[PTR_W-1]) &&", "  assign full  = (wr_ptr[PTR_W-1] == rd_ptr[PTR_W-1]) &&", "FIFO: full and empty are confused (the wrap bit is compared the wrong way)", ["a_full_def", "a_empty_def", "a_count_range"]),
    ("M11", FF, "      wr_ptr <= '0;\n      rd_ptr <= '0;\n    end else begin", "      wr_ptr <= '0;\n      rd_ptr <= rd_ptr;\n    end else begin", "FIFO: a flush leaves the read pointer where it was", ["a_flush_empty", "a_count_range"]),
    ("M12", FF, "      if (rd_en) rd_ptr <= rd_ptr + 1'b1;", "      if (m_tvalid) rd_ptr <= rd_ptr + 1'b1;", "FIFO: a beat is popped whether or not the consumer took it", ["a_hold", "sb_order", "a_count_step"]),
    ("M13", FF, "      mem[wr_ptr[IDX_W-1:0]] <= {s_tlast, s_tdata};", "      mem[wr_ptr[IDX_W-1:0]] <= {1'b0, s_tdata};", "FIFO: TLAST is not stored", ["sb_order"]),
    ("M14", CR, "        2'b01:   credits_q <= credits_q + 1'b1;", "        2'b01:   credits_q <= credits_q;", "credit counter: a credit is never returned", ["a_step"]),
    ("M15", CR, "        2'b10:   credits_q <= credits_q - 1'b1;", "        2'b10:   credits_q <= credits_q;", "credit counter: a credit is never spent", ["a_step"]),
    ("M16", CR, "  assign have_credit = (credits_q != '0);", "  assign have_credit = (credits_q > 1);", "credit counter: the last credit can never be used", ["a_have_def"]),
    ("M17", CR, "      credits_q <= clear_value;", "      credits_q <= CNT_W'(MAX_CREDITS);", "credit counter: a flush reloads the maximum instead of the reconciled value", ["a_reload"]),
    ("M18", CR, "      credits_q <= CNT_W'(MAX_CREDITS);\n    end else if (soft_clear) begin", "      credits_q <= CNT_W'(MAX_CREDITS - 1);\n    end else if (soft_clear) begin", "credit counter: reset starts one credit short", ["a_reset_full"]),
    ("M19", CT, "  assign can_accept   = !busy || last_pass;", "  assign can_accept   = !busy;", "control: a new sample waits a cycle after the last pass (initiation interval one cycle too long)", ["a_consume_def"]),
    ("M20", CT, "  assign shift_en  = sample_start;", "  assign shift_en  = busy;", "control: the delay line shifts once per PASS instead of once per sample (design risk R3)", ["a_shift_eq"]),
    ("M21", CT, "in_tvalid && have_credit && can_accept;", "in_tvalid && can_accept;", "control: a sample is admitted without a credit", ["a_consume_def", "a_spend_needs"]),
    ("M22", CT, "  assign sample_start = (state == fir_pkg::ST_RUN) && in_tvalid", "  assign sample_start = in_tvalid", "control: samples are admitted in any FSM state", ["a_only_running", "a_consume_def"]),
    ("M23", CT, "      if (sample_start) tlast_q <= in_tlast;", "      if (last_pass) tlast_q <= in_tlast;", "control: TLAST is captured at the last pass instead of at admission", ["a_tlast_cap", "a_tlast_hold"]),
    ("M24", CT, "        if (!busy && dp_count == '0) state_n = fir_pkg::ST_IDLE;", "        if (!busy) state_n = fir_pkg::ST_IDLE;", "control: DRAIN ends while results are still in the datapath", ["a_drain_wait"]),
    ("M25", CT, "        if (ctrl_enable) state_n = cfg_illegal ? fir_pkg::ST_CFGERR : fir_pkg::ST_RUN;", "        if (ctrl_enable) state_n = fir_pkg::ST_RUN;", "control: an illegal configuration is allowed to run", ["a_idle_to_err"]),
    ("M26", CT, "        2'b01:   dp_count <= dp_count - 1'b1;", "        2'b01:   dp_count <= dp_count;", "control: the in-flight count is never decremented", ["a_credit_inv", "a_dp_nonneg", "a_idle_empty"]),
    ("M27", CT, "  assign tap_base   = pcnt << LOG2_MAC;", "  assign tap_base   = pcnt;", "control: the tap base advances by one tap per pass instead of MAC_UNITS", ["a_tap_base"]),
    ("M28", CT, "      tlast_q  <= 1'b0;\n      dp_count <= '0;\n    end else begin", "      tlast_q  <= 1'b0;\n    end else begin", "control: a flush leaves the in-flight count stale", ["a_soft_idle", "a_credit_inv"]),
    ("M29", CT, "  assign sts_busy     = (state != fir_pkg::ST_IDLE);", "  assign sts_busy     = (state == fir_pkg::ST_RUN);", "control: STATUS.BUSY is low while draining or in the error state", ["a_status_busy"]),
    ("M30", CB, "  assign swap_now = pending_q && (frame_boundary || idle);", "  assign swap_now = pending_q && idle;", "coefficients: a commit never takes effect at a frame boundary while streaming", ["a_swap_occurs"]),
    ("M31", CB, "  assign swap_now = pending_q && (frame_boundary || idle);", "  assign swap_now = pending_q;", "coefficients: a commit swaps the bank immediately, even inside a frame", ["a_swap_cause", "a_swap_window"]),
    ("M32", CB, "      if (bank_sel) bank0[waddr] <= wdata;   // bank1 active -> bank0 is shadow\n      else          bank1[waddr] <= wdata;", "      if (bank_sel) bank1[waddr] <= wdata;\n      else          bank0[waddr] <= wdata;", "coefficients: software writes land in the ACTIVE bank", ["a_active_still"]),
    ("M33", CB, "        pending_q <= commit_req;      // a commit arriving on the swap cycle survives", "        pending_q <= 1'b0;", "coefficients: a commit arriving on the swap cycle is lost", ["a_pend_resub"]),
    ("M34", CS, "  assign cfg_wr_blocked = w_csr_mapped && (waddr12 == fir_pkg::REG_CFG) && sts_busy;", "  assign cfg_wr_blocked = 1'b0;", "registers: a CFG write while BUSY is accepted", ["a_cfg_busy_err"]),
    ("M35", CS, "  assign reg_werr       = !w_mapped || cfg_wr_blocked;", "  assign reg_werr       = cfg_wr_blocked;", "registers: a write to an unmapped address is answered OKAY", ["a_unmapped_w"]),
    ("M36", CS, "  assign cfg_illegal = ntaps_bad || round_bad;", "  assign cfg_illegal = ntaps_bad;", "registers: the reserved rounding mode is not an illegal configuration", ["a_illegal_def"]),
    ("M37", CS, "  assign irq              = |(irq_status_q & irq_enable_q);", "  assign irq              = |irq_status_q;", "registers: the interrupt ignores IRQ_ENABLE", ["a_irq_level"]),
    ("M38", CS, "      if (cfg_err_event) cfg_err_sticky_q <= 1'b1;", "      cfg_err_sticky_q <= cfg_err_event;", "registers: CFG_ERR is not sticky", ["a_cfgerr_stick"]),
    ("M39", CS, "      if (evt_saturate)  sat_sticky_q     <= 1'b1;", "      sat_sticky_q <= evt_saturate;", "registers: SAT_STICKY is not sticky", ["a_sat_stick"]),
    ("M40", CS, "        if (evt_sample)     sample_cnt_q <= sample_cnt_q + 32'd1;", "        if (evt_frame_done) sample_cnt_q <= sample_cnt_q + 32'd1;", "registers: the sample counter counts frames", ["a_sample_cnt"]),
    ("M41", TP, "  assign credit_reload  = CW'(OUT_FIFO_DEPTH) - out_count_next[CW-1:0];", "  assign credit_reload  = CW'(OUT_FIFO_DEPTH);", "top: a flush does not reconcile the credits with the beats still in the output FIFO", ["a_credit_inv", "a_reload"]),
    ("M42", TP, "  assign evt_frame_done = out_pop && m_axis_tlast;", "  assign evt_frame_done = out_push && core_out_last;", "top: the frame boundary is taken when TLAST enters the output FIFO, not when it leaves", ["a_swap_window"]),
    ("M43", TP, "  assign soft_clear = cmd_soft_reset | cmd_flush;", "  assign soft_clear = cmd_soft_reset;", "top: CTRL.FLUSH does nothing", []),
    ("M44", DL, "      for (int i = 1; i < NTAPS_MAX; i++) mem[i] <= mem[i-1];", "      for (int i = 1; i < NTAPS_MAX; i++) mem[i] <= mem[i];", "delay line: the samples never move along the line", ["a_shift_move"]),
    ("M45", FC, "    .clk (clk), .rst_n (rst_n), .clear (clear),", "    .clk (clk), .rst_n (rst_n), .clear (1'b0),", "datapath: a flush does not clear the delay line (FR-04)", ["a_clear_zero"]),
    ("M46", RS, "  assign min_neg = sat_mode ? -max_pos", "  assign min_neg = !sat_mode ? -max_pos", "output stage: the two saturation modes are swapped", ["a_sym_range", "a_sat_on_bound"]),
    ("M47", RS, "  assign sat_event = sat_hi | sat_lo;", "  assign sat_event = sat_hi;", "output stage: negative saturation is not reported", []),
    ("M48", "ASFOUND", "", "", "datapath: the flush does not drop samples still in the pipeline (the defect found by this campaign's checkers, as first built)", ["a_credit_inv", "a_dp_nonneg", "a_no_drop", "a_return_room", "a_range"]),
]

# which suites contain which module (a mutant is run only where its module is built; the top suite contains everything)
SUITE_OF = {AX: ("csr", "top"), CS: ("csr", "top"), FF: ("stream", "top"), CR: ("stream", "top"), FC: ("fir", "top"), DL: ("fir", "top"), RS: ("fir", "top"),
            "ASFOUND": ("fir", "top")}


# Q01.. : one mutant for each assertion that none of the first 48 made fire (and none of them changed the tests that existed then).
# Same rules as above: one injected bug per mutant, written against the RTL as it is, found exactly once.
QNEW = [
    # ---- AXI-Lite slave
    ("Q01", AX, "        if (aw_hs) begin\n          aw_full_q <= 1'b1;\n          awaddr_q  <= s_axil_awaddr;",
     "        if (aw_hs) begin\n          aw_full_q <= 1'b1;\n          bvalid_q  <= 1'b1;\n          awaddr_q  <= s_axil_awaddr;",
     "AXI-Lite: a write response is issued as soon as the address arrives, before the data", ["a_b_after_wen"]),
    ("Q02", AX, "        bvalid_q  <= 1'b1;\n        bresp_q   <= reg_werr ? fir_pkg::RESP_SLVERR : fir_pkg::RESP_OKAY;",
     "        bvalid_q  <= !reg_werr;\n        bresp_q   <= reg_werr ? fir_pkg::RESP_SLVERR : fir_pkg::RESP_OKAY;",
     "AXI-Lite: a refused write gets no response", ["a_wen_gives_b"]),
    ("Q03", AX, "  assign s_axil_rvalid = rvalid_q;", "  assign s_axil_rvalid = rvalid_q || s_axil_arvalid;",
     "AXI-Lite: RVALID follows ARVALID combinationally", ["a_r_after_ar"]),
    ("Q04", AX, "        rvalid_q <= 1'b1;\n        rdata_q  <= reg_rdata;", "        rvalid_q <= !reg_rerr;\n        rdata_q  <= reg_rdata;",
     "AXI-Lite: a read of an unmapped address gets no response", ["a_ar_gives_r"]),
    ("Q05", AX, "        aw_full_q <= 1'b0;\n        w_full_q  <= 1'b0;\n        bvalid_q  <= 1'b1;", "        aw_full_q <= 1'b1;\n        w_full_q  <= 1'b1;\n        bvalid_q  <= 1'b0;",
     "AXI-Lite: the write enable never goes away (the halves stay held, no response)", ["a_wen_pulse"]),
    ("Q06", AX, "        bresp_q   <= reg_werr ? fir_pkg::RESP_SLVERR : fir_pkg::RESP_OKAY;", "        bresp_q   <= reg_werr ? 2'b11 : fir_pkg::RESP_OKAY;",
     "AXI-Lite: a refused write is answered DECERR", ["a_bresp_legal"]),
    ("Q07", AX, "        bresp_q   <= reg_werr ? fir_pkg::RESP_SLVERR : fir_pkg::RESP_OKAY;", "        bresp_q   <= fir_pkg::RESP_SLVERR;",
     "AXI-Lite: every write is answered SLVERR", ["a_bresp_ok"]),
    ("Q08", AX, "        rresp_q  <= reg_rerr ? fir_pkg::RESP_SLVERR : fir_pkg::RESP_OKAY;", "        rresp_q  <= reg_rerr ? 2'b11 : fir_pkg::RESP_OKAY;",
     "AXI-Lite: a read of an unmapped address is answered DECERR", ["a_rresp_legal"]),
    ("Q09", AX, "        rresp_q  <= reg_rerr ? fir_pkg::RESP_SLVERR : fir_pkg::RESP_OKAY;", "        rresp_q  <= fir_pkg::RESP_SLVERR;",
     "AXI-Lite: every read is answered SLVERR", ["a_rresp_ok"]),
    ("Q10", AX, "      bvalid_q  <= 1'b0;\n      bresp_q   <= fir_pkg::RESP_OKAY;", "      bvalid_q  <= 1'b1;\n      bresp_q   <= fir_pkg::RESP_OKAY;",
     "AXI-Lite: reset leaves a write response pending", ["a_reset_quiet"]),
    # ---- stream FIFO
    ("Q11", FF, "  assign empty = (wr_ptr == rd_ptr);", "  assign empty = (wr_ptr[PTR_W-2:0] == rd_ptr[PTR_W-2:0]);",
     "FIFO: a full FIFO reads as empty (the wrap bit is ignored)", ["a_empty_def"]),
    # ---- control FSM and pass sequencer
    ("Q12", CT, "        if (ctrl_enable) state_n = cfg_illegal ? fir_pkg::ST_CFGERR : fir_pkg::ST_RUN;",
     "        if (ctrl_enable || cmd_flush) state_n = cfg_illegal ? fir_pkg::ST_CFGERR : fir_pkg::ST_RUN;",
     "control: a flush command wakes the idle FSM", ["a_idle_stay"]),
    ("Q13", CT, "        if (ctrl_enable) state_n = cfg_illegal ? fir_pkg::ST_CFGERR : fir_pkg::ST_RUN;",
     "        if (ctrl_enable) state_n = cfg_illegal ? fir_pkg::ST_CFGERR : fir_pkg::ST_DRAIN;",
     "control: enabling goes to the drain state instead of RUN", ["a_idle_to_run"]),
    ("Q14", CT, "        if (!ctrl_enable) state_n = fir_pkg::ST_IDLE;", "        if (!ctrl_enable || in_tvalid) state_n = fir_pkg::ST_IDLE;",
     "control: the error state is left when input data arrives", ["a_err_hold"]),
    ("Q15", CT, "        if (!ctrl_enable) state_n = fir_pkg::ST_IDLE;", "        if (!ctrl_enable && !cfg_illegal) state_n = fir_pkg::ST_IDLE;",
     "control: the error state is left only when the configuration is legal as well", ["a_err_exit"]),
    ("Q16", CT, "        if (!ctrl_enable || cmd_flush) state_n = fir_pkg::ST_DRAIN;", "        if (!ctrl_enable || cmd_flush || !in_tvalid) state_n = fir_pkg::ST_DRAIN;",
     "control: RUN drops to DRAIN whenever the input stalls", ["a_run_stay"]),
    ("Q17", CT, "        if (!ctrl_enable || cmd_flush) state_n = fir_pkg::ST_DRAIN;", "        if (!ctrl_enable) state_n = fir_pkg::ST_DRAIN;",
     "control: a flush command does not leave RUN", ["a_run_to_drain"]),
    ("Q18", CT, "        if (!busy && dp_count == '0) state_n = fir_pkg::ST_IDLE;", "        if (!busy && dp_count == '0 && !in_tvalid) state_n = fir_pkg::ST_IDLE;",
     "control: DRAIN does not finish while the input still has data", ["a_drain_done"]),
    ("Q19", CT, "  assign sts_draining = (state == fir_pkg::ST_DRAIN);", "  assign sts_draining = (state == fir_pkg::ST_DRAIN) && (dp_count != '0);",
     "control: STATUS.DRAINING is low in DRAIN once the datapath is empty", ["a_status_drain"]),
    ("Q20", CT, "  assign in_tready = sample_start;", "  assign in_tready = (state == fir_pkg::ST_RUN) && have_credit && can_accept;",
     "control: TREADY does not wait for TVALID (legal AXI-Stream, but not the consume strobe)", ["a_ready_eq"]),
    ("Q21", CT, "      end else begin\n        busy <= sample_start;\n        pcnt <= '0;\n      end", "      end else begin\n        busy <= sample_start && !tlast_q;\n        pcnt <= '0;\n      end",
     "control: the first sample after a TLAST sample is consumed but gets no pass phase", ["a_pass_start"]),
    ("Q22", CT, "  assign pass_first = busy && (pcnt == '0);", "  assign pass_first = busy && (pcnt == '0) && !tlast_q;",
     "control: pass_first is low for a sample that follows TLAST", ["a_pass_first"]),
    ("Q23", CT, "  assign pass_last  = last_pass;", "  assign pass_last  = last_pass && !tlast_q;",
     "control: pass_last is low for the TLAST sample", ["a_pass_last"]),
    ("Q24", CT, "          pcnt <= pcnt + 1'b1;", "          pcnt <= pcnt + 2'd2;",
     "control: the pass counter skips a pass", ["a_pass_step"]),
    ("Q25", CT, "  assign last_pass = busy && (pcnt == p_last);", "  assign last_pass = busy && (pcnt > p_last);",
     "control: the pass phase runs one pass too many", ["a_pass_bound"]),
    ("Q26", CT, "          busy <= sample_start;      // back-to-back if a new sample was admitted", "          busy <= 1'b1;",
     "control: the pass phase restarts after the last pass although no sample was admitted", ["a_pass_end"]),
    # ---- coefficient bank
    ("Q27", CB, "      end else if (commit_req) begin\n        pending_q <= 1'b1;", "      end else if (commit_req) begin\n        pending_q <= 1'b0;",
     "coefficients: a commit that cannot swap at once is forgotten", ["a_pend_set"]),
    ("Q28", CB, "      bank_sel  <= 1'b0;\n      pending_q <= 1'b0;", "      bank_sel  <= 1'b1;\n      pending_q <= 1'b0;",
     "coefficients: reset selects bank 1 as the active bank", ["a_reset_bank"]),
    # ---- register file
    ("Q29", CS, "    reg_rerr  = 1'b1;                       // default: unmapped", "    reg_rerr  = 1'b0;                       // default: unmapped",
     "registers: a read of an unmapped address is flagged OKAY by the register file", ["a_unmapped_r"]),
    ("Q30", CS, "  assign reg_werr       = !w_mapped || cfg_wr_blocked;", "  assign reg_werr       = !w_mapped || cfg_wr_blocked || (w_csr_mapped && w_is_ro);",
     "registers: a write to a read-only register is refused", ["a_mapped_w"]),
    ("Q31", CS, "    if (r_csr_mapped) begin\n      reg_rerr = 1'b0;", "    if (r_csr_mapped) begin\n      reg_rerr = (raddr12 == fir_pkg::REG_FRAME_CNT);",
     "registers: a read of FRAME_CNT is flagged as an error", ["a_mapped_r"]),
    ("Q32", CS, ("  assign csr_wr = reg_wen && w_csr_mapped && !w_is_ro && !reg_werr;", "          default: ; // mapped, writable, but no side effect"),
     ("  assign csr_wr = reg_wen && !w_is_ro;", "          default: cfg_round_q <= fir_pkg::RND_RESERVED;"),
     "registers: a write to an unmapped address changes the rounding mode", ["a_unmapped_keep"]),
    ("Q33", CS, "        fir_pkg::REG_ID:         reg_rdata = fir_pkg::IP_ID;", "        fir_pkg::REG_ID:         reg_rdata = fir_pkg::IP_ID ^ 32'h1;",
     "registers: the ID register reads back a wrong constant", ["a_id_const"]),
    ("Q34", CS, "      if (cfg_err_event) cfg_err_sticky_q <= 1'b1;", "      if (cfg_err_event && irq_enable_q[fir_pkg::IRQ_CFG_ERR_BIT]) cfg_err_sticky_q <= 1'b1;",
     "registers: STATUS.CFG_ERR is set only when its interrupt is enabled", ["a_cfgerr_set"]),
    ("Q35", CS, "      if (evt_saturate)  sat_sticky_q     <= 1'b1;", "      if (evt_saturate && irq_enable_q[fir_pkg::IRQ_SAT_EVENT_BIT]) sat_sticky_q     <= 1'b1;",
     "registers: STATUS.SAT_STICKY is set only when its interrupt is enabled", ["a_sat_set"]),
    ("Q36", CS, "      if (evt_frame_done) irq_status_q[fir_pkg::IRQ_FRAME_DONE_BIT] <= 1'b1;",
     "      if (evt_frame_done && irq_enable_q[fir_pkg::IRQ_FRAME_DONE_BIT]) irq_status_q[fir_pkg::IRQ_FRAME_DONE_BIT] <= 1'b1;",
     "registers: the frame-done status bit is set only when its interrupt is enabled", ["a_frame_event"]),
    ("Q37", CS, "      if (cfg_err_event)  irq_status_q[fir_pkg::IRQ_CFG_ERR_BIT]    <= 1'b1;",
     "      if (cfg_err_event && irq_enable_q[fir_pkg::IRQ_CFG_ERR_BIT])  irq_status_q[fir_pkg::IRQ_CFG_ERR_BIT]    <= 1'b1;",
     "registers: the CFG_ERR status bit is set only when its interrupt is enabled", ["a_cfgerr_event"]),
    ("Q38", CS, "      cmd_flush_q      <= 1'b0;\n      cmd_cnt_clr_q    <= 1'b0;\n\n      if (csr_wr) begin", "      cmd_cnt_clr_q    <= 1'b0;\n\n      if (csr_wr) begin",
     "registers: the flush command is not a pulse (it stays until the register is written again)", ["a_cmd_pulse"]),
    ("Q39", CS, "        if (evt_frame_done) frame_cnt_q  <= frame_cnt_q  + 32'd1;", "        if (evt_sample) frame_cnt_q  <= frame_cnt_q  + 32'd1;",
     "registers: the frame counter counts samples", ["a_frame_cnt"]),
    ("Q40", CS, "        frame_cnt_q  <= 32'h0;\n      end else begin", "        frame_cnt_q  <= frame_cnt_q;\n      end else begin",
     "registers: CNT_CLR does not clear the frame counter", ["a_cnt_clear"]),
    # ---- delay line
    ("Q41", DL, "    end else if (clear) begin\n      for (int i = 0; i < NTAPS_MAX; i++) mem[i] <= '0;", "    end else if (clear) begin\n      for (int i = 0; i < NTAPS_MAX; i++) mem[i] <= mem[i];",
     "delay line: a clear leaves the samples where they are", ["a_clear_zero"]),
    ("Q42", DL, "    end else if (shift_en) begin\n      mem[0] <= din;", "    end else begin\n      mem[0] <= din;",
     "delay line: the line shifts on every clock", ["a_taps_hold"]),
    ("Q43", DL, "      mem[0] <= din;", "      mem[0] <= din ^ {{(DATA_W-1){1'b0}}, 1'b1};",
     "delay line: the newest sample enters with its low bit inverted", ["a_shift_in"]),
    ("Q44", DL, "    if (!rst_n) begin\n      for (int i = 0; i < NTAPS_MAX; i++) mem[i] <= '0;\n    end else if (clear) begin",
     "    if (!rst_n) begin\n      for (int i = 0; i < NTAPS_MAX; i++) mem[i] <= '1;\n    end else if (clear) begin",
     "delay line: reset fills the line with ones", ["a_reset_zero"]),
    # ---- credit scheme (two files: the credit counter forgets to spend, and the output FIFO accepts a beat when it is full)
    ("Q45", TP, ("  credit_ctr #(.MAX_CREDITS (OUT_FIFO_DEPTH)) u_credit (", "  axis_fifo #(.DATA_W (DATA_W), .DEPTH (OUT_FIFO_DEPTH)) u_out_fifo (",
                 "  assign credit_reload  = CW'(OUT_FIFO_DEPTH) - out_count_next[CW-1:0];"),
     ("  credit_ctr #(.MAX_CREDITS (OUT_FIFO_DEPTH + 4)) u_credit (", "  axis_fifo #(.DATA_W (DATA_W), .DEPTH (2*OUT_FIFO_DEPTH)) u_out_fifo (",
      "  assign credit_reload  = CW'(OUT_FIFO_DEPTH + 4) - out_count_next[CW-1:0];"),
     "credits: a pool four larger than OUT_FIFO_DEPTH and an output FIFO twice as deep (works, but buffers more than the contract allows)", ["a_out_bound"]),
    ("Q46", CT, "        if (ctrl_enable) state_n = cfg_illegal ? fir_pkg::ST_CFGERR : fir_pkg::ST_RUN;",
     "        if (ctrl_enable || in_tvalid) state_n = cfg_illegal ? fir_pkg::ST_CFGERR : fir_pkg::ST_RUN;",
     "control: the idle FSM starts on input data without ENABLE", ["a_idle_stay"]),
    ("Q47", CT, "        if (!ctrl_enable || cmd_flush) state_n = fir_pkg::ST_DRAIN;", "        if (cmd_flush) state_n = fir_pkg::ST_DRAIN;",
     "control: RUN is not left when ENABLE is dropped", ["a_run_to_drain"]),
]
MUTANTS = MUTANTS + QNEW

# Survivors that are understood: the edit changes nothing observable. The reason is recorded; results/sva/equivalence.txt (scripts/check_equivalent.py) must
# confirm each one, or the campaign reports the claim as unverified.
EQUIVALENT = {
    "Q12": "equivalent: fir_ctrl takes its soft_clear branch (state <= IDLE) whenever cmd_flush is high, because axis_fir_top ties soft_clear = cmd_soft_reset | cmd_flush; "
           "state_n, the only thing the edit changes, is not used in that cycle. The flush term in fir_ctrl's next-state logic is dead logic.",
    "Q17": "equivalent: same reason as Q12 (the flush term of the RUN arm is overridden by soft_clear in the cycle cmd_flush is high); ENABLE dropping still leaves RUN.",
}


def configs_for(file):
    files = file if isinstance(file, tuple) else (file,)
    want = tuple(sorted({s for f in files for s in SUITE_OF.get(f, ("top",))}))
    cfgs = [c for c in L.CONFIGS if c[0] in L.QUICK and c[1] in want]
    if "top" in want:   # fold-specific bugs need a multi-pass build as well
        cfgs += [c for c in L.CONFIGS if c[0] in ("top_m1", "top_m4")]
    return cfgs


def apply(m, work):
    """copy rtl/ to work/<id>/rtl and apply the edit; returns (rtl_dir, error)"""
    mid, rel, old, new = m[0], m[1], m[2], m[3]
    d = os.path.join(work, mid, "rtl")
    if os.path.exists(d):
        shutil.rmtree(d)
    shutil.copytree(os.path.join(ROOT, "rtl"), d)
    if rel == "ASFOUND":
        shutil.copy(os.path.join(ROOT, "verif", "as_found", "fir_core.sv"), os.path.join(d, "dsp", "fir_core.sv"))
        return d, None
    # a mutant may need several edits (each exactly once): several in one file (old/new are tuples), or one in each of several files
    # (rel, old and new are tuples of the same length, edit i applies to file rel[i])
    if isinstance(rel, tuple):
        edits = list(zip(rel, old, new))
    elif isinstance(old, tuple):
        edits = [(rel, o, n) for o, n in zip(old, new)]
    else:
        edits = [(rel, old, new)]
    text = {}
    for f, o, n in edits:
        path = os.path.join(d, f[len("rtl/"):])
        if path not in text:
            text[path] = open(path).read()
        if text[path].count(o) != 1:
            return d, "edit text found %d times in %s" % (text[path].count(o), f)
        text[path] = text[path].replace(o, n)
    for path, tx in text.items():
        open(path, "w").write(tx)
    return d, None


def run_mutant(m, work, jobs_inner=1, reuse=False):
    mid, rel, old, new, desc, expect = m
    d, err = apply(m, work)
    if err:
        return dict(id=mid, desc=desc, status="INVALID", why=err, fired={}, expect=expect)
    res = L.run_matrix(configs_for(rel), os.path.join(work, mid, "runs"), jobs=jobs_inner, rtl_dir=d, reuse=reuse)
    fired = {}
    for r in res:
        for k, v in r["fired"].items():
            fired[k] = fired.get(k, 0) + v
    failed = [r["tag"] + (" (timeout)" if r.get("timeout") else "") for r in res if r["tests_failed"]]
    aborted = [r["tag"] for r in res if r["aborted_by_checker"]]
    unbuilt = [r["tag"] for r in res if r["tests"] is None and r.get("build_error")]
    if unbuilt:
        return dict(id=mid, desc=desc, status="INVALID", why="does not build: " + ", ".join(unbuilt), fired={}, expect=expect)
    if all(r["tests"] is None and not r.get("timeout") for r in res) and all(not r["fired"] for r in res):
        return dict(id=mid, desc=desc, status="INVALID", why="no run built or finished (see %s)" % os.path.relpath(os.path.join(work, mid, "runs"), ROOT), fired={}, expect=expect)
    if failed:
        status = "KILLED by tests"
    elif fired:
        status = "KILLED by an assertion only"
    else:
        status = "SURVIVED"
    hit = [e for e in expect if e in fired]
    return dict(id=mid, desc=desc, status=status, why=("failed: " + ", ".join(failed)) if failed else "", fired=fired, expect=expect,
                mismatch=bool(expect and fired and not hit), runs=len(res), unbuilt=unbuilt, aborted=aborted, known=EQUIVALENT.get(mid))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--only")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--resume", action="store_true", help="reuse the finished run logs of an earlier, interrupted campaign")
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "sva", "mutants.txt"))
    a = ap.parse_args()
    work = os.path.join(ROOT, "build", "sva_mutants")
    if not a.resume and not a.selftest and os.path.isdir(work):
        shutil.rmtree(work)
    ms = [m for m in MUTANTS if not a.only or m[0] in a.only.split(",")]
    if a.selftest:
        bad = 0
        ids = [m[0] for m in MUTANTS]
        if len(set(ids)) != len(ids):
            print("duplicate mutant ids")
            bad = 1
        for m in MUTANTS:
            d, err = apply(m, tempfile.mkdtemp())
            if err:
                print("%s: %s" % (m[0], err))
                bad = 1
        asserts = set(L.assertion_labels(os.path.join(ROOT, "verif", "fir_checkers.sv")))
        for m in MUTANTS:
            for e in m[5]:
                if e not in asserts:
                    print("%s expects an assertion that does not exist: %s" % (m[0], e))
                    bad = 1
        print("sva_mutants selftest: %s (%d mutants, %d assertions)" % ("FAILED" if bad else "ok", len(MUTANTS), len(asserts)))
        return bad
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        res = list(ex.map(lambda m: run_mutant(m, work, reuse=a.resume), ms))
    killed = [r for r in res if r["status"].startswith("KILLED")]
    only_assert = [r for r in res if r["status"] == "KILLED by an assertion only"]
    surv = [r for r in res if r["status"] == "SURVIVED"]
    invalid = [r for r in res if r["status"] == "INVALID"]
    all_fired = set()
    for r in res:
        all_fired |= {k for k in r["fired"] if k.startswith(("a_", "sb_"))}
    asserts = L.assertion_labels(os.path.join(ROOT, "verif", "fir_checkers.sv"))
    unv_path = os.path.join(ROOT, "verif", "sva_unviolable.txt")
    unviolable = {}
    if os.path.exists(unv_path):
        for ln in open(unv_path):
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                lab, _, why = ln.partition(" ")
                unviolable[lab] = why.strip()
    stale = sorted(x for x in unviolable if x in all_fired)
    never = [x for x in asserts if x not in all_fired and x not in unviolable]
    # a survivor with a recorded reason is accepted only if the bounded model check (check_equivalent.py) found no difference for it
    eq_path = os.path.join(ROOT, "results", "sva", "equivalence.txt")
    eq_txt = open(eq_path).read() if os.path.exists(eq_path) else ""
    unverified = [r["id"] for r in surv if r.get("known") and not any(l.startswith(r["id"] + " ") and ("PROVED EQUIVALENT" in l or "NO DIFFERENCE" in l) for l in eq_txt.splitlines())]
    stale_eq = [m for m in EQUIVALENT if m in {r["id"] for r in res} and m not in {r["id"] for r in surv}]
    unexplained = [r["id"] for r in surv if not r.get("known")]
    if len(set(asserts)) != len(asserts):   # a firing is reported by label, so two checkers sharing one would be indistinguishable
        print("duplicate assertion labels in verif/fir_checkers.sv")
        return 2
    unknown = sorted({k for r in res for k in r["fired"] if k.startswith(("a_", "sb_")) and k not in set(asserts)})
    if unknown:   # a log line that cannot be matched to a defined assertion: never count it silently
        print("fired labels that match no defined assertion (log lines damaged?): " + ", ".join(unknown))
        return 2
    mism = [r for r in res if r.get("mismatch")]
    lines = ["mutants: %d, caught %d (by tests %d, by an assertion only %d), survived %d, invalid %d" % (
        len(res), len(killed), len(killed) - len(only_assert), len(only_assert), len(surv), len(invalid)),
        "caught ONLY by an assertion (the cocotb tests missed them): " + (", ".join(r["id"] for r in only_assert) or "none"),
        "survivors (each with its reason, confirmed by results/sva/equivalence.txt): " + (", ".join(r["id"] for r in surv) or "none"),
        "assertions defined: %d; fired in at least one mutant run: %d" % (len(asserts), len(asserts) - len(never) - len(unviolable)),
        "assertions that fired in no run: " + (", ".join(never) or "none"),
        "assertions no RTL edit can violate (reason in verif/sva_unviolable.txt): " + (", ".join(sorted(unviolable)) or "none"),
        "mutants whose expected assertion did not fire (another one did): " + (", ".join(r["id"] for r in mism) or "none"),
        "PROBLEMS: " + ("; ".join(["INVALID " + r["id"] for r in invalid] + ["UNEXPLAINED SURVIVOR " + x for x in unexplained] + ["UNVERIFIED EQUIVALENCE CLAIM " + x for x in unverified]
                                  + ["STALE EQUIVALENCE CLAIM " + x for x in stale_eq] + ["STALE unviolable entry " + x for x in stale]) or "none"), ""]
    for r in sorted(res, key=lambda r: r["id"]):
        lines.append("%s  %-28s %-90s assert-fired: %s%s" % (r["id"], r["status"], r["desc"][:90], ",".join(sorted(k for k in r["fired"] if k.startswith(("a_", "sb_", "rtl:")))) or "-",
                                                             ("   [" + r["why"][:80] + "]") if r["why"] and r["status"] == "INVALID" else ""))
        if r["status"] == "SURVIVED" and r.get("known"):
            lines.append("      " + r["known"])
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w").write("\n".join(lines) + "\n")
    open(os.path.join(os.path.dirname(a.out), "mutants_head.txt"), "w").write("\n".join(lines[:8]) + "\n")   # included by the design document
    print("\n".join(lines[:8]))
    return 1 if (invalid or unexplained or unverified or stale_eq or never or stale) else 0


if __name__ == "__main__":
    sys.exit(main())
