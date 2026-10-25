// fir_checkers.sv - bound assertions, cover points and two scoreboards for axis_fir_accel (attached by verif/fir_binds.sv).
//
// Added by the portfolio work (2026-10-21); the RTL is not modified by this file. Every checker is bound into one RTL module type, so it runs
// in every cocotb suite that instantiates that module (csr, stream, fir, top) without touching the testbenches.
//
// Conventions: assertions are labelled a_*, covers c_*. Each assertion names the requirement it comes from (docs/01_requirements.md), or says
// "design rule" where the rule is stated in the RTL header comments (credit counter, FIFO, control FSM). Ready/valid style "whenever" rules
// are written together with their "only when" converse. The scoreboards (sb_*) are procedural bound checks that report through the same
// assertion mechanism, so a failure prints `Assertion failed in <scope>.<label>`.
// Needs the source-built 5.052 simulator (sequence delays, [->n]); the repository's earlier flow (Icarus + cocotb) cannot run concurrent assertions.

// ------------------------------------------------------------------------------------------------------------------------------------
// AXI4-Lite slave (bound into axil_slave): protocol rules, one outstanding write and one outstanding read
// ------------------------------------------------------------------------------------------------------------------------------------
module fir_axil_chk #(
  parameter int DATA_W = 32
) (
  input logic              clk,
  input logic              rst_n,
  input logic              s_axil_awvalid, s_axil_awready,
  input logic              s_axil_wvalid,  s_axil_wready,
  input logic [1:0]        s_axil_bresp,
  input logic              s_axil_bvalid,  s_axil_bready,
  input logic              s_axil_arvalid, s_axil_arready,
  input logic [DATA_W-1:0] s_axil_rdata,
  input logic [1:0]        s_axil_rresp,
  input logic              s_axil_rvalid,  s_axil_rready,
  input logic              reg_wen, reg_ren, reg_werr, reg_rerr
);
  // a checker is armed once it has seen the reset asserted: before that the design is in its random power-up state
  // (rst_q = reset one cycle ago: the first cycle after reset is released is skipped too, because laws written with $past compare it
  //  with the last reset cycle, where the state is legitimately discontinuous)
  logic armed = 1'b0;
  logic rst_q = 1'b1;
  always @(posedge clk) begin
    rst_q <= rst_n;
    if (!rst_n) armed <= 1'b1;
  end

  logic ar_hs, aw_hs, w_hs;
  assign ar_hs = s_axil_arvalid && s_axil_arready;
  assign aw_hs = s_axil_awvalid && s_axil_awready;
  assign w_hs  = s_axil_wvalid  && s_axil_wready;

  // AXI4-Lite: a response channel holds its payload until the master accepts it
  a_b_hold:        assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axil_bvalid && !s_axil_bready |=> s_axil_bvalid && $stable(s_axil_bresp));
  a_r_hold:        assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axil_rvalid && !s_axil_rready |=> s_axil_rvalid && $stable(s_axil_rdata) && $stable(s_axil_rresp));
  // a response exists only because a request was taken, and every taken request gets exactly one response
  a_b_after_wen:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) $rose(s_axil_bvalid) |-> $past(reg_wen));
  a_wen_gives_b:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_wen |=> s_axil_bvalid);
  a_r_after_ar:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) $rose(s_axil_rvalid) |-> $past(ar_hs));
  a_ar_gives_r:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) ar_hs |=> s_axil_rvalid);
  // one write and one read outstanding; the register bus sees single-cycle pulses
  a_wen_slot_free: assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_wen |-> !s_axil_bvalid);
  a_wen_pulse:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_wen |=> !reg_wen);
  a_ren_is_hs:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_ren == ar_hs);
  a_ren_pulse:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_ren |=> !reg_ren);
  a_b_blocks_wr:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axil_bvalid |-> !s_axil_awready && !s_axil_wready);
  a_r_blocks_rd:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axil_rvalid |-> !s_axil_arready);
  // response codes: only OKAY and SLVERR are ever produced, and they follow the register bus error flag (FR-12, FR-24)
  a_bresp_legal:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axil_bvalid |-> (s_axil_bresp == 2'b00 || s_axil_bresp == 2'b10));
  a_rresp_legal:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axil_rvalid |-> (s_axil_rresp == 2'b00 || s_axil_rresp == 2'b10));
  a_bresp_err:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n)  reg_wen &&  reg_werr |=> s_axil_bresp == 2'b10);
  a_bresp_ok:      assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n)  reg_wen && !reg_werr |=> s_axil_bresp == 2'b00);
  a_rresp_err:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n)  ar_hs &&  reg_rerr |=> s_axil_rresp == 2'b10);
  a_rresp_ok:      assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n)  ar_hs && !reg_rerr |=> s_axil_rresp == 2'b00);
  // reset: no response is pending (asynchronous reset, so checked while reset is low)
  a_reset_quiet:   assert property (@(posedge clk) !rst_n && !rst_q |-> (!s_axil_bvalid && !s_axil_rvalid));

  c_aw_first:      cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) aw_hs && !s_axil_wvalid ##[1:8] w_hs);
  c_w_first:       cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) w_hs && !s_axil_awvalid ##[1:8] aw_hs);
  c_aw_w_together: cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) aw_hs && w_hs);
  c_b_backpress:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axil_bvalid && !s_axil_bready ##1 s_axil_bvalid && s_axil_bready);
  c_r_backpress:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axil_rvalid && !s_axil_rready ##1 s_axil_rvalid && s_axil_rready);
  c_b_slverr:      cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axil_bvalid && s_axil_bresp == 2'b10);
  c_r_slverr:      cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axil_rvalid && s_axil_rresp == 2'b10);
  c_b_okay:        cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axil_bvalid && s_axil_bresp == 2'b00);
  c_r_okay:        cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axil_rvalid && s_axil_rresp == 2'b00);
  c_write_b2b:     cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axil_bvalid && s_axil_bready ##1 aw_hs);
  c_read_with_b:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axil_bvalid && ar_hs);
endmodule

// ------------------------------------------------------------------------------------------------------------------------------------
// Stream FIFO (bound into axis_fifo, so it covers the input and the output FIFO): occupancy bookkeeping, FR-19 payload stability, order
// ------------------------------------------------------------------------------------------------------------------------------------
module fir_fifo_chk #(
  parameter int DATA_W = 16,
  parameter int DEPTH  = 8
) (
  input logic                    clk,
  input logic                    rst_n,
  input logic                    soft_clear,
  input logic [DATA_W-1:0]       s_tdata,
  input logic                    s_tlast, s_tvalid, s_tready,
  input logic [DATA_W-1:0]       m_tdata,
  input logic                    m_tlast, m_tvalid, m_tready,
  input logic [$clog2(DEPTH):0]  count,
  input logic                    full, empty
);
  // a checker is armed once it has seen the reset asserted: before that the design is in its random power-up state
  // (rst_q = reset one cycle ago: the first cycle after reset is released is skipped too, because laws written with $past compare it
  //  with the last reset cycle, where the state is legitimately discontinuous)
  logic armed = 1'b0;
  logic rst_q = 1'b1;
  always @(posedge clk) begin
    rst_q <= rst_n;
    if (!rst_n) armed <= 1'b1;
  end

  logic wr_en, rd_en;
  assign wr_en = s_tvalid && s_tready;
  assign rd_en = m_tvalid && m_tready;

  a_count_range:  assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) int'(count) <= DEPTH);
  a_full_def:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) full  == (int'(count) == DEPTH));
  a_empty_def:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) empty == (count == '0));
  // design rule (header of axis_fifo.sv, TR-04): ready and valid depend on occupancy only
  a_tready_def:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_tready == !full);
  a_tvalid_def:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) m_tvalid == !empty);
  // occupancy follows the transfers exactly; a flush empties it
  a_count_step:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) !$past(soft_clear) |-> int'(count) == int'($past(count)) + int'($past(wr_en)) - int'($past(rd_en)));
  a_flush_empty:  assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) soft_clear |=> empty);
  // FR-19: once offered, a beat is held unchanged until the consumer takes it (a flush is the only thing allowed to withdraw it)
  a_hold:         assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) m_tvalid && !m_tready |=> m_tvalid && $stable(m_tdata) && $stable(m_tlast));

  // FR-15 / FR-16 at the FIFO: what comes out is what went in, in order (a flush drops what is inside)
  logic [DATA_W:0] sb_q [$];
  always @(posedge clk) begin
    if (!armed || !rst_n || soft_clear) begin
      sb_q.delete();
    end else begin
      if (rd_en) begin
        sb_fifo_not_empty: assert (sb_q.size() != 0) else $error("a read beat with nothing stored (count=%0d full=%b empty=%b wr_en=%b rst_q=%b soft_clear=%b)", count, full, empty, wr_en, rst_q, soft_clear);
        if (sb_q.size() != 0) begin
          sb_order: assert ({m_tlast, m_tdata} == sb_q[0]) else $error("beat out of order or corrupted: got %h, expected %h", {m_tlast, m_tdata}, sb_q[0]);
          void'(sb_q.pop_front());
        end
      end
      if (wr_en) sb_q.push_back({s_tlast, s_tdata});
    end
  end

  c_full:          cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) full);
  c_almost_full:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) int'(count) == DEPTH - 1);
  c_fill_drain:    cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) full ##[1:$] empty);
  c_push_pop:      cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) wr_en && rd_en);
  c_stall:         cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) m_tvalid && !m_tready);
  c_wr_blocked:    cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_tvalid && !s_tready);
  c_tlast_in:      cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) wr_en && s_tlast);
  c_flush_nonempty:cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) soft_clear && !empty);
endmodule

// ------------------------------------------------------------------------------------------------------------------------------------
// Admission credit counter (bound into credit_ctr): the whole flow-control mechanism of the IP
// ------------------------------------------------------------------------------------------------------------------------------------
module fir_credit_chk #(
  parameter int MAX_CREDITS = 8
) (
  input logic                              clk,
  input logic                              rst_n,
  input logic                              soft_clear,
  input logic [$clog2(MAX_CREDITS+1)-1:0]  clear_value,
  input logic                              consume, restore, have_credit,
  input logic [$clog2(MAX_CREDITS+1)-1:0]  credits
);
  // a checker is armed once it has seen the reset asserted: before that the design is in its random power-up state
  // (rst_q = reset one cycle ago: the first cycle after reset is released is skipped too, because laws written with $past compare it
  //  with the last reset cycle, where the state is legitimately discontinuous)
  logic armed = 1'b0;
  logic rst_q = 1'b1;
  always @(posedge clk) begin
    rst_q <= rst_n;
    if (!rst_n) armed <= 1'b1;
  end

  a_range:        assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) int'(credits) <= MAX_CREDITS);
  a_have_def:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) have_credit == (credits != '0));
  a_step:         assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) !$past(soft_clear) |-> int'(credits) == int'($past(credits)) - int'($past(consume)) + int'($past(restore)));
  a_reload:       assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) soft_clear |=> credits == $past(clear_value));
  // design rule: a credit is spent only if one is available (this is what makes the output FIFO overflow-proof)
  a_spend_needs:  assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) consume |-> have_credit);
  // a credit is returned only for a beat that was admitted, so the count can never exceed the maximum
  a_return_room:  assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) restore |-> int'(credits) < MAX_CREDITS);
  a_reset_full:   assert property (@(posedge clk) !rst_n && !rst_q |-> int'(credits) == MAX_CREDITS);

  c_zero:         cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) !have_credit);
  c_full:         cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) int'(credits) == MAX_CREDITS);
  c_spend_return: cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) consume && restore);
  c_return_only:  cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) restore && !consume);
  c_reconcile:    cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) soft_clear && int'(clear_value) != MAX_CREDITS);
endmodule

// ------------------------------------------------------------------------------------------------------------------------------------
// Control FSM and pass sequencer (bound into fir_ctrl)
// ------------------------------------------------------------------------------------------------------------------------------------
module fir_ctrl_chk #(
  parameter int MAC_UNITS = 8
) (
  input logic        clk,
  input logic        rst_n,
  input logic        soft_clear,
  input logic        ctrl_enable, cfg_illegal, cmd_flush,
  input logic        in_tvalid, in_tlast, in_tready,
  input logic        have_credit, consume, core_out_valid,
  input logic        shift_en, pass_vld, pass_first, pass_last, pass_tlast,
  input logic [31:0] tap_base,
  input logic        sts_busy, sts_draining,
  input logic [1:0]  state,
  input logic        busy, sample_start, last_pass,
  input logic [31:0] pcnt, p_last, dp_count
);
  // a checker is armed once it has seen the reset asserted: before that the design is in its random power-up state
  // (rst_q = reset one cycle ago: the first cycle after reset is released is skipped too, because laws written with $past compare it
  //  with the last reset cycle, where the state is legitimately discontinuous)
  logic armed = 1'b0;
  logic rst_q = 1'b1;
  always @(posedge clk) begin
    rst_q <= rst_n;
    if (!rst_n) armed <= 1'b1;
  end

  localparam int LOG2_MAC = $clog2(MAC_UNITS);
  localparam logic [1:0] S_IDLE = fir_pkg::ST_IDLE, S_RUN = fir_pkg::ST_RUN, S_DRAIN = fir_pkg::ST_DRAIN, S_ERR = fir_pkg::ST_CFGERR;

  // FSM transitions, one per arc (a soft clear forces IDLE from anywhere, so it is excluded)
  a_idle_stay:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) state == S_IDLE && !ctrl_enable |=> state == S_IDLE);
  a_idle_to_run:  assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) state == S_IDLE &&  ctrl_enable && !cfg_illegal |=> state == S_RUN);
  a_idle_to_err:  assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) state == S_IDLE &&  ctrl_enable &&  cfg_illegal |=> state == S_ERR);   // FR-21
  a_err_hold:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) state == S_ERR  &&  ctrl_enable |=> state == S_ERR);
  a_err_exit:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) state == S_ERR  && !ctrl_enable |=> state == S_IDLE);
  a_run_stay:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) state == S_RUN  &&  ctrl_enable && !cmd_flush |=> state == S_RUN);
  a_run_to_drain: assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) state == S_RUN  && (!ctrl_enable || cmd_flush) |=> state == S_DRAIN);
  a_drain_done:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) state == S_DRAIN && !busy && dp_count == 0 |=> state == S_IDLE);
  a_drain_wait:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) state == S_DRAIN && (busy || dp_count != 0) |=> state == S_DRAIN);
  a_soft_idle:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) soft_clear |=> state == S_IDLE && !busy && dp_count == 0);
  a_status_busy:  assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) sts_busy == (state != S_IDLE));
  a_status_drain: assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) sts_draining == (state == S_DRAIN));

  // admission (FR-15, FR-21): a sample enters only while running, with data and a credit, and only when the pass sequencer is free
  a_consume_def:  assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) consume == (state == S_RUN && in_tvalid && have_credit && (!busy || last_pass)));
  a_only_running: assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) consume |-> state == S_RUN);
  a_ready_eq:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) in_tready == consume);
  // design risk R3: the delay line shifts once per SAMPLE (at admission), never once per pass
  a_shift_eq:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) shift_en == consume);

  // pass sequencing: exactly ceil(n_eff/MAC_UNITS) consecutive passes per sample, tap_base advancing by MAC_UNITS
  a_pass_start:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) consume |=> pass_vld && pass_first);
  a_pass_first:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) pass_first == (pass_vld && pcnt == 0));
  a_pass_last:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) pass_last  == (pass_vld && pcnt == p_last));
  a_pass_step:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) pass_vld && !pass_last |=> pass_vld && pcnt == $past(pcnt) + 1);
  a_pass_bound:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) pass_vld |-> pcnt <= p_last);
  a_tap_base:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) tap_base == (pcnt << LOG2_MAC));
  a_pass_end:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) pass_last && !consume |=> !pass_vld);
  // FR-17: TLAST of a sample is held steady for its whole pass phase
  a_tlast_hold:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) pass_vld && !consume |=> $stable(pass_tlast));
  a_tlast_cap:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) consume |=> pass_tlast == $past(in_tlast));
  // in-flight bookkeeping: an output always has an admitted sample behind it
  a_dp_nonneg:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) core_out_valid |-> dp_count != 0);

  c_run:          cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) state == S_IDLE && ctrl_enable ##1 state == S_RUN);
  c_cfg_error:    cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) state == S_ERR);
  c_drain_busy:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) state == S_DRAIN && dp_count != 0);
  c_flush_run:    cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) state == S_RUN && cmd_flush);
  c_credit_stall: cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) state == S_RUN && in_tvalid && !have_credit);
  c_input_starve: cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) state == S_RUN && !in_tvalid && !busy);
  c_multipass:    cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) consume && p_last != 0);
  c_single_pass:  cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) consume && p_last == 0);
  c_back_to_back: cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) consume ##1 consume);
  c_tlast_admit:  cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) consume && in_tlast);
  c_soft_busy:    cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) soft_clear && busy);
endmodule

// ------------------------------------------------------------------------------------------------------------------------------------
// Coefficient banks (bound into coeff_bank): FR-03, FR-10, FR-11
// ------------------------------------------------------------------------------------------------------------------------------------
module fir_coef_chk #(
  parameter int W = 1024
) (
  input logic         clk,
  input logic         rst_n,
  input logic         we,
  input logic         commit_req, frame_boundary, idle, pending,
  input logic [W-1:0] coef_flat,
  input logic         bank_sel
);
  // a checker is armed once it has seen the reset asserted: before that the design is in its random power-up state
  // (rst_q = reset one cycle ago: the first cycle after reset is released is skipped too, because laws written with $past compare it
  //  with the last reset cycle, where the state is legitimately discontinuous)
  logic armed = 1'b0;
  logic rst_q = 1'b1;
  always @(posedge clk) begin
    rst_q <= rst_n;
    if (!rst_n) armed <= 1'b1;
  end

  logic swap_now;
  assign swap_now = pending && (frame_boundary || idle);

  // FR-10: the active bank changes only at a swap, and a swap happens exactly when a commit is pending at a frame boundary or while idle
  a_swap_cause:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) $changed(bank_sel) |-> $past(swap_now));
  a_swap_occurs:  assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) swap_now |=> bank_sel != $past(bank_sel));
  a_active_still: assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) !$past(swap_now) |-> $stable(coef_flat));    // FR-03: writes never disturb the active bank
  // FR-11: pending is set by a commit, held until the swap, and a commit arriving on the swap cycle is not lost
  a_pend_set:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) commit_req && !swap_now |=> pending);
  a_pend_hold:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) pending && !swap_now |=> pending);
  a_pend_clear:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) swap_now && !commit_req |=> !pending);
  a_pend_resub:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) swap_now &&  commit_req |=> pending);
  a_reset_bank:   assert property (@(posedge clk) !rst_n && !rst_q |-> !bank_sel && !pending);

  c_swap_frame:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) swap_now && frame_boundary && !idle);
  c_swap_idle:    cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) swap_now && idle && !frame_boundary);
  c_write_pend:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) we && pending);
  c_commit_swap:  cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) swap_now && commit_req);
  c_second_swap:  cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) swap_now ##[1:$] swap_now);
endmodule

// ------------------------------------------------------------------------------------------------------------------------------------
// Register file (bound into csr_regfile): FR-12, FR-21..25
// ------------------------------------------------------------------------------------------------------------------------------------
module fir_csr_chk #(
  parameter int ADDR_W    = 12,
  parameter int NTAPS_MAX = 64
) (
  input logic              clk,
  input logic              rst_n,
  input logic              reg_wen, reg_ren,
  input logic [ADDR_W-1:0] reg_waddr, reg_raddr,
  input logic [31:0]       reg_wdata,
  input logic [3:0]        reg_wstrb,
  input logic              reg_werr, reg_rerr,
  input logic [31:0]       reg_rdata,
  input logic              ctrl_enable,
  input logic [8:0]        cfg_ntaps,
  input logic [1:0]        cfg_round_mode,
  input logic              cfg_sat_mode, cfg_symmetric, cfg_illegal,
  input logic              cmd_soft_reset, cmd_coeff_commit, cmd_flush,
  input logic              sts_busy,
  input logic              evt_sample, evt_frame_done, evt_saturate, irq,
  input logic [2:0]        irq_status_q, irq_enable_q,
  input logic              sat_sticky_q, cfg_err_sticky_q, cmd_cnt_clr_q,
  input logic [31:0]       sample_cnt_q, frame_cnt_q
);
  // a checker is armed once it has seen the reset asserted: before that the design is in its random power-up state
  // (rst_q = reset one cycle ago: the first cycle after reset is released is skipped too, because laws written with $past compare it
  //  with the last reset cycle, where the state is legitimately discontinuous)
  logic armed = 1'b0;
  logic rst_q = 1'b1;
  always @(posedge clk) begin
    rst_q <= rst_n;
    if (!rst_n) armed <= 1'b1;
  end

  import fir_pkg::*;

  // independent restatement of the address map (docs/04_interfaces.md): nine CSR words, then NTAPS_MAX coefficient words at 0x400
  function automatic bit mapped(input logic [ADDR_W-1:0] a);
    logic [11:0] a12;
    logic [31:0] a32;
    a32 = 32'(a);
    a12 = a32[11:0];
    if (a32[31:12] != '0) return 0;
    if (a12[11:8] == 4'h0) return a12 inside {12'h000, 12'h004, 12'h008, 12'h00C, 12'h010, 12'h014, 12'h018, 12'h01C, 12'h020};
    if (a12[11:10] == 2'b01) return a12[9:2] < NTAPS_MAX;
    return 0;
  endfunction

  logic w_cfg_blocked, irq_clr_cfg, irq_clr_sat;
  assign w_cfg_blocked = reg_wen && reg_waddr == REG_CFG && sts_busy;
  assign irq_clr_cfg   = reg_wen && reg_waddr == REG_IRQ_STATUS && reg_wstrb[0] && reg_wdata[IRQ_CFG_ERR_BIT];
  assign irq_clr_sat   = reg_wen && reg_waddr == REG_IRQ_STATUS && reg_wstrb[0] && reg_wdata[IRQ_SAT_EVENT_BIT];

  // FR-12: a CFG write while BUSY is refused and changes nothing
  a_cfg_busy_err: assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) w_cfg_blocked |-> reg_werr);
  a_cfg_busy_keep: assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) w_cfg_blocked |=> $stable(cfg_ntaps) && $stable(cfg_round_mode) && $stable(cfg_sat_mode) && $stable(cfg_symmetric));
  // FR-24: unmapped accesses are errors and change nothing; mapped ones are not errors (except the FR-12 case)
  a_unmapped_w:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_wen && !mapped(reg_waddr) |-> reg_werr);
  a_unmapped_r:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_ren && !mapped(reg_raddr) |-> reg_rerr);
  a_mapped_w:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_wen &&  mapped(reg_waddr) && !w_cfg_blocked |-> !reg_werr);
  a_mapped_r:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_ren &&  mapped(reg_raddr) |-> !reg_rerr);
  a_unmapped_keep:assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_wen && !mapped(reg_waddr) |=> $stable(ctrl_enable) && $stable(cfg_ntaps) && $stable(cfg_round_mode) && $stable(cfg_sat_mode) && $stable(irq_enable_q));
  a_id_const:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_raddr == REG_ID |-> reg_rdata == IP_ID);
  // FR-21 / FR-22: an illegal configuration is flagged while enabled, and the flag is sticky until cleared by software
  a_illegal_def:  assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) cfg_illegal == (cfg_ntaps == 0 || int'(cfg_ntaps) > NTAPS_MAX || cfg_round_mode == RND_RESERVED));
  a_cfgerr_set:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) ctrl_enable && cfg_illegal |=> cfg_err_sticky_q);
  a_cfgerr_stick: assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) $fell(cfg_err_sticky_q) |-> $past(irq_clr_cfg));
  // FR-23: saturation sets a sticky status bit and an event, and never stops the stream
  a_sat_set:      assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) evt_saturate |=> sat_sticky_q && irq_status_q[IRQ_SAT_EVENT_BIT]);
  a_sat_stick:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) $fell(sat_sticky_q) |-> $past(irq_clr_sat));
  a_frame_event:  assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) evt_frame_done |=> irq_status_q[IRQ_FRAME_DONE_BIT]);
  a_cfgerr_event: assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) ctrl_enable && cfg_illegal |=> irq_status_q[IRQ_CFG_ERR_BIT]);
  // FR-25: level interrupt
  a_irq_level:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) irq == |(irq_status_q & irq_enable_q));
  // FR-11 and friends: command bits are one-cycle pulses
  a_cmd_pulse:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) (cmd_soft_reset || cmd_coeff_commit || cmd_flush) |=> !cmd_soft_reset && !cmd_coeff_commit && !cmd_flush);
  // performance counters: count events, clear on CNTCLR
  a_sample_cnt:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) !$past(cmd_cnt_clr_q) |-> sample_cnt_q == $past(sample_cnt_q) + 32'($past(evt_sample)));
  a_frame_cnt:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) !$past(cmd_cnt_clr_q) |-> frame_cnt_q == $past(frame_cnt_q) + 32'($past(evt_frame_done)));
  a_cnt_clear:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) $past(cmd_cnt_clr_q) |-> sample_cnt_q == 0 && frame_cnt_q == 0);

  c_cfg_blocked:  cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) w_cfg_blocked);
  c_unmapped_w:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_wen && !mapped(reg_waddr));
  c_unmapped_r:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_ren && !mapped(reg_raddr));
  c_ro_write:     cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_wen && reg_waddr inside {REG_ID, REG_CAPABILITY, REG_STATUS, REG_SAMPLE_CNT, REG_FRAME_CNT});
  c_ntaps_zero:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) ctrl_enable && cfg_ntaps == 0);
  c_ntaps_big:    cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) ctrl_enable && int'(cfg_ntaps) > NTAPS_MAX);
  c_round_resv:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) ctrl_enable && cfg_round_mode == RND_RESERVED);
  c_sat:          cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) evt_saturate);
  c_irq_up:       cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) $rose(irq));
  c_irq_down:     cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) $fell(irq));
  c_cmd_commit:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) cmd_coeff_commit);
  c_cmd_flush:    cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) cmd_flush);
  c_cmd_soft:     cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) cmd_soft_reset);
  c_cnt_clear:    cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) cmd_cnt_clr_q);
  c_partial_strb: cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) reg_wen && reg_wstrb != 4'hF);
  c_cfg_sticky:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) cfg_err_sticky_q ##[1:$] !cfg_err_sticky_q);
endmodule

// ------------------------------------------------------------------------------------------------------------------------------------
// Delay line (bound into delay_line): FR-04 and design risk R3
// ------------------------------------------------------------------------------------------------------------------------------------
module fir_delay_chk #(
  parameter int DATA_W    = 16,
  parameter int NTAPS_MAX = 64
) (
  input logic                        clk,
  input logic                        rst_n,
  input logic                        clear, shift_en,
  input logic [DATA_W-1:0]           din,
  input logic [NTAPS_MAX*DATA_W-1:0] taps_flat
);
  // a checker is armed once it has seen the reset asserted: before that the design is in its random power-up state
  // (rst_q = reset one cycle ago: the first cycle after reset is released is skipped too, because laws written with $past compare it
  //  with the last reset cycle, where the state is legitimately discontinuous)
  logic armed = 1'b0;
  logic rst_q = 1'b1;
  always @(posedge clk) begin
    rst_q <= rst_n;
    if (!rst_n) armed <= 1'b1;
  end

  a_clear_zero:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) clear |=> taps_flat == '0);                                          // FR-04
  a_taps_hold:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) !clear && !shift_en |=> $stable(taps_flat));
  a_shift_in:     assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) !clear && shift_en |=> taps_flat[DATA_W-1:0] == $past(din));
  a_shift_move:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) !clear && shift_en |=> taps_flat[NTAPS_MAX*DATA_W-1:DATA_W] == $past(taps_flat[(NTAPS_MAX-1)*DATA_W-1:0]));
  a_reset_zero:   assert property (@(posedge clk) !rst_n && !rst_q |-> taps_flat == '0);

  c_shift:        cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) shift_en && !clear);
  c_clear_full:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) clear && taps_flat != '0);
  c_shift_wrap:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) shift_en [->NTAPS_MAX]);
endmodule

// ------------------------------------------------------------------------------------------------------------------------------------
// Datapath output stage (bound into fir_core): FR-06 saturation bounds
// ------------------------------------------------------------------------------------------------------------------------------------
module fir_core_chk #(
  parameter int DATA_W = 16
) (
  input logic              clk,
  input logic              rst_n,
  input logic              cfg_sat_mode,
  input logic [1:0]        cfg_round_mode,
  input logic              out_valid, out_sat,
  input logic [DATA_W-1:0] out_data
);
  // a checker is armed once it has seen the reset asserted: before that the design is in its random power-up state
  // (rst_q = reset one cycle ago: the first cycle after reset is released is skipped too, because laws written with $past compare it
  //  with the last reset cycle, where the state is legitimately discontinuous)
  logic armed = 1'b0;
  logic rst_q = 1'b1;
  always @(posedge clk) begin
    rst_q <= rst_n;
    if (!rst_n) armed <= 1'b1;
  end

  logic signed [DATA_W-1:0] y, ymin, ymax, ymin_sym;
  assign y        = out_data;
  assign ymax     = {1'b0, {(DATA_W-1){1'b1}}};
  assign ymin     = {1'b1, {(DATA_W-1){1'b0}}};
  assign ymin_sym = ymin + 1'b1;

  // FR-06: the symmetric mode never produces -2^(W-1); a saturation event always lands on a bound; no event means no clamp was needed
  a_sym_range:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) out_valid && cfg_sat_mode |-> y != ymin);
  a_sat_on_bound: assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) out_valid && out_sat |-> (y == ymax || y == (cfg_sat_mode ? ymin_sym : ymin)));

  c_sat_hi:       cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) out_valid && out_sat && y == ymax);
  c_sat_lo_asym:  cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) out_valid && out_sat && !cfg_sat_mode && y == ymin);
  c_sat_lo_sym:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) out_valid && out_sat &&  cfg_sat_mode && y == ymin_sym);
  c_round_trunc:  cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) out_valid && cfg_round_mode == fir_pkg::RND_TRUNC);
  c_round_up:     cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) out_valid && cfg_round_mode == fir_pkg::RND_HALF_UP);
  c_round_conv:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) out_valid && cfg_round_mode == fir_pkg::RND_CONVERGENT);
  c_zero_out:     cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) out_valid && y == 0);
endmodule

// ------------------------------------------------------------------------------------------------------------------------------------
// Top level (bound into axis_fir_top): FR-15, FR-17, FR-18, FR-19 and the credit invariant that makes output overflow impossible
// ------------------------------------------------------------------------------------------------------------------------------------
module fir_top_chk #(
  parameter int DATA_W         = 16,
  parameter int OUT_FIFO_DEPTH = 16
) (
  input logic              clk,
  input logic              rst_n,
  input logic              s_axis_tvalid, s_axis_tready,
  input logic              m_axis_tlast, m_axis_tvalid, m_axis_tready,
  input logic [DATA_W-1:0] m_axis_tdata,
  input logic              irq,
  input logic              soft_clear, consume, in_tlast_admit,
  input logic              out_push, out_pop, out_fifo_full, sts_busy,
  input logic [31:0]       out_count, credits, dp_count,
  input logic              cfg_symmetric, evt_frame_done, evt_saturate,
  input logic [1:0]        cfg_round_mode,
  input logic              cfg_sat_mode,
  input logic              bank_sel
);
  // a checker is armed once it has seen the reset asserted: before that the design is in its random power-up state
  // (rst_q = reset one cycle ago: the first cycle after reset is released is skipped too, because laws written with $past compare it
  //  with the last reset cycle, where the state is legitimately discontinuous)
  logic armed = 1'b0;
  logic rst_q = 1'b1;
  always @(posedge clk) begin
    rst_q <= rst_n;
    if (!rst_n) armed <= 1'b1;
  end

  // FR-19: the output stream obeys the AXI4-Stream stability rule (no beat is withdrawn or altered before it is taken)
  a_m_hold:       assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) m_axis_tvalid && !m_axis_tready |=> m_axis_tvalid && $stable(m_axis_tdata) && $stable(m_axis_tlast));
  // design rule (credit_ctr.sv header): credits + samples in the datapath + beats in the output FIFO is exactly the output FIFO depth,
  // so a push can never find the FIFO full
  a_credit_inv:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) int'(credits) + int'(dp_count) + int'(out_count) == OUT_FIFO_DEPTH);
  a_no_drop:      assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) out_push |-> !out_fifo_full || out_pop);
  a_out_bound:    assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) int'(out_count) <= OUT_FIFO_DEPTH);
  // FR-15: when the IP reports idle nothing is left inside it
  a_idle_empty:   assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n || soft_clear) !sts_busy |-> dp_count == 0);
  // FR-10: the coefficient bank never swaps while a frame is in flight, except at the frame-boundary beat
  a_swap_window:  assert property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) $changed(bank_sel) |-> $past(!sts_busy || (out_pop && m_axis_tlast)));

  // FR-15 and FR-17: one output beat per admitted sample, TLAST on the same beat index. The scoreboard is re-armed after a flush, which
  // legitimately discards what is in the datapath.
  logic        sb_armed;
  logic        sb_q [$];
  always @(posedge clk) begin
    if (!armed || !rst_n) begin
      sb_q.delete();
      sb_armed <= 1'b1;
    end else if (soft_clear) begin
      sb_q.delete();
      sb_armed <= 1'b0;
    end else begin
      if (!sb_armed && dp_count == 0 && out_count == 0 && !consume) sb_armed <= 1'b1;
      if (sb_armed) begin
        if (out_pop) begin
          sb_beat_has_sample: assert (sb_q.size() != 0) else $error("an output beat with no admitted sample behind it");
          if (sb_q.size() != 0) begin
            sb_tlast_index: assert (m_axis_tlast == sb_q[0]) else $error("TLAST moved: expected %0d on this beat", sb_q[0]);
            void'(sb_q.pop_front());
          end
        end
        if (consume) sb_q.push_back(in_tlast_admit);
      end
    end
  end

  c_out_stall8:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) m_axis_tvalid && !m_axis_tready [*8]);
  c_in_blocked:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) s_axis_tvalid && !s_axis_tready);
  c_out_full:     cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) out_fifo_full);
  c_credit_zero:  cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) credits == 0);
  c_tlast_out:    cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) m_axis_tvalid && m_axis_tready && m_axis_tlast);
  c_frame_len1:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) consume && in_tlast_admit ##1 consume && in_tlast_admit);
  c_irq:          cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) irq);
  c_flush_data:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) soft_clear && (dp_count != 0 || out_count != 0));
  c_sym_run:      cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) consume && cfg_symmetric);
  c_asym_run:     cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) consume && !cfg_symmetric);
  c_sat_sym:      cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) evt_saturate && cfg_sat_mode);
  c_sat_asym:     cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) evt_saturate && !cfg_sat_mode);
  c_swap:         cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) $changed(bank_sel));
  c_swap_busy:    cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) $changed(bank_sel) && $past(sts_busy));
  c_drain_flow:   cover property (@(posedge clk) disable iff (!armed || !rst_q || !rst_n) sts_busy ##[1:$] !sts_busy);
endmodule
