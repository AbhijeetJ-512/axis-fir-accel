// -----------------------------------------------------------------------------
// axis_loopback_wrap.sv   *** TESTBENCH WRAPPER -- NOT PART OF THE DELIVERED IP ***
//
// Phase 2 device under test. It is the Phase 4 stream skeleton with the
// arithmetic replaced by a plain delay:
//
//   s_axis --> [ in_fifo ] --> issue gate --> [ delay pipe ] --> [ out_fifo ] --> m_axis
//                                  ^                                  |
//                                  +------- credit_ctr <--------------+
//
// The delay pipe stands in for delay_line/mac_array/acc_reduce/round_sat. It has
// the property that matters for flow control and no other: FIXED LATENCY, ONE
// OUTPUT PER INPUT, NEVER STALLS. Everything this phase proves about
// losslessness, ordering, TLAST alignment and the credit invariant therefore
// carries over unchanged when the real datapath replaces it.
//
// Both axis_fifo and credit_ctr are real deliverables; only this wiring and the
// dummy pipe are testbench-only.
// -----------------------------------------------------------------------------
`timescale 1ns/1ps

module axis_loopback_wrap #(
  parameter int DATA_W         = 16,
  parameter int IN_FIFO_DEPTH  = 8,
  parameter int OUT_FIFO_DEPTH = 8,
  parameter int PIPE_DEPTH     = 4     // stand-in for the arithmetic pipeline
) (
  input  logic                clk,
  input  logic                arst_n,
  input  logic                soft_clear,

  // ---- AXI4-Stream slave ----------------------------------------------------
  input  logic [DATA_W-1:0]   s_axis_tdata,
  input  logic                s_axis_tlast,
  input  logic                s_axis_tvalid,
  output logic                s_axis_tready,

  // ---- AXI4-Stream master ---------------------------------------------------
  output logic [DATA_W-1:0]   m_axis_tdata,
  output logic                m_axis_tlast,
  output logic                m_axis_tvalid,
  input  logic                m_axis_tready,

  // ---- Observability for the testbench --------------------------------------
  output logic                dbg_issue,
  output logic                dbg_out_push,
  output logic                dbg_out_full,
  output logic                dbg_have_credit,
  output logic [$clog2(IN_FIFO_DEPTH):0]  dbg_in_count,
  output logic [$clog2(OUT_FIFO_DEPTH):0] dbg_out_count,
  output logic [$clog2(OUT_FIFO_DEPTH+1)-1:0] dbg_credits
);

  localparam int PAY_W = DATA_W + 1;   // {tlast, tdata}

  // ---------------------------------------------------------------------------
  // Elaboration-time legality
  //
  // The credit scheme throttles below one sample per cycle unless the output
  // FIFO can hold everything in flight plus the beat being read out. Getting
  // this wrong does not corrupt data -- it silently halves throughput, which is
  // exactly the kind of bug that hides until a performance measurement.
  // ---------------------------------------------------------------------------
  initial begin
    if (OUT_FIFO_DEPTH <= PIPE_DEPTH + 1)
      $fatal(1, "axis_loopback_wrap: OUT_FIFO_DEPTH (%0d) must exceed PIPE_DEPTH+1 (%0d) to sustain II=1.",
             OUT_FIFO_DEPTH, PIPE_DEPTH + 1);
    if (PIPE_DEPTH < 1)
      $fatal(1, "axis_loopback_wrap: PIPE_DEPTH must be >= 1 (got %0d).", PIPE_DEPTH);
  end

  logic rst_n;
  reset_sync #(.STAGES(2)) u_rst_sync (
    .clk (clk), .arst_n (arst_n), .rst_n (rst_n)
  );

  // ---------------------------------------------------------------------------
  // Input elastic buffer
  // ---------------------------------------------------------------------------
  logic [DATA_W-1:0] in_tdata;
  logic              in_tlast, in_tvalid, in_tready;

  axis_fifo #(.DATA_W(DATA_W), .DEPTH(IN_FIFO_DEPTH)) u_in_fifo (
    .clk (clk), .rst_n (rst_n), .soft_clear (soft_clear),
    .s_tdata (s_axis_tdata), .s_tlast (s_axis_tlast),
    .s_tvalid(s_axis_tvalid), .s_tready(s_axis_tready),
    .m_tdata (in_tdata), .m_tlast (in_tlast),
    .m_tvalid(in_tvalid), .m_tready(in_tready),
    .count (dbg_in_count), .full (), .empty ()
  );

  // ---------------------------------------------------------------------------
  // Admission
  // ---------------------------------------------------------------------------
  logic issue, have_credit, out_pop;
  logic out_push, out_full;

  assign issue     = in_tvalid && have_credit;
  assign in_tready = issue;                       // pop the input FIFO on admission
  assign out_pop   = m_axis_tvalid && m_axis_tready;

  // ---------------------------------------------------------------------------
  // Credit reconciliation on flush
  //
  // soft_clear flushes the input FIFO and the pipeline but deliberately does
  // NOT flush the output FIFO. Clearing the output FIFO would drop m_axis_tvalid
  // while a beat was still being offered, which is an AXI4-Stream violation --
  // TVALID may not be withdrawn before TREADY. The downstream consumer has no
  // visibility of our soft reset and is entitled to the beat it was promised.
  //
  // Because the flush discards in-flight samples, their credits would never be
  // returned. The counter is therefore reloaded with the exact number of free
  // output slots after this cycle's push/pop settle, which restores the
  // invariant precisely rather than approximately.
  // ---------------------------------------------------------------------------
  localparam int CW = $clog2(OUT_FIFO_DEPTH+1);

  logic [$clog2(OUT_FIFO_DEPTH):0] out_count_next;
  logic [CW-1:0]                   credit_reload;

  assign out_count_next = dbg_out_count
                        + {{$clog2(OUT_FIFO_DEPTH){1'b0}}, out_push}
                        - {{$clog2(OUT_FIFO_DEPTH){1'b0}}, out_pop};
  assign credit_reload  = CW'(OUT_FIFO_DEPTH) - out_count_next[CW-1:0];

  credit_ctr #(.MAX_CREDITS(OUT_FIFO_DEPTH)) u_credit (
    .clk (clk), .rst_n (rst_n), .soft_clear (soft_clear),
    .clear_value (credit_reload),
    .consume (issue), .restore (out_pop),
    .have_credit (have_credit), .credits (dbg_credits)
  );

  // ---------------------------------------------------------------------------
  // Fixed-latency delay pipe (stands in for the arithmetic pipeline)
  //
  // No enables, no stall input: by the credit invariant this pipe is never
  // asked to hold a result. The valid chain is the only control state.
  // ---------------------------------------------------------------------------
  logic [PIPE_DEPTH-1:0] pipe_vld;
  logic [PAY_W-1:0]      pipe_pay [0:PIPE_DEPTH-1];

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pipe_vld <= '0;
    end else if (soft_clear) begin
      pipe_vld <= '0;
    end else begin
      pipe_vld[0] <= issue;
      for (int i = 1; i < PIPE_DEPTH; i++) begin
        pipe_vld[i] <= pipe_vld[i-1];
      end
    end
  end

  // Payload advances only behind a valid, so an X read from an empty input FIFO
  // cannot enter the pipe and mask a real bug further down.
  always_ff @(posedge clk) begin
    if (issue) begin
      pipe_pay[0] <= {in_tlast, in_tdata};
    end
    for (int i = 1; i < PIPE_DEPTH; i++) begin
      if (pipe_vld[i-1]) pipe_pay[i] <= pipe_pay[i-1];
    end
  end

  // ---------------------------------------------------------------------------
  // Output elastic buffer
  //
  // s_tready of the output FIFO is deliberately IGNORED. The credit counter
  // guarantees space exists; if that guarantee were ever violated the testbench
  // check on (dbg_out_push && dbg_out_full) would fire immediately.
  // ---------------------------------------------------------------------------
  assign out_push = pipe_vld[PIPE_DEPTH-1];

  // soft_clear is NOT wired here -- see the credit reconciliation note above.
  axis_fifo #(.DATA_W(DATA_W), .DEPTH(OUT_FIFO_DEPTH)) u_out_fifo (
    .clk (clk), .rst_n (rst_n), .soft_clear (1'b0),
    .s_tdata (pipe_pay[PIPE_DEPTH-1][DATA_W-1:0]),
    .s_tlast (pipe_pay[PIPE_DEPTH-1][DATA_W]),
    .s_tvalid(out_push), .s_tready(),
    .m_tdata (m_axis_tdata), .m_tlast (m_axis_tlast),
    .m_tvalid(m_axis_tvalid), .m_tready(m_axis_tready),
    .count (dbg_out_count), .full (out_full), .empty ()
  );

  assign dbg_issue       = issue;
  assign dbg_out_push    = out_push;
  assign dbg_out_full    = out_full;
  assign dbg_have_credit = have_credit;

endmodule : axis_loopback_wrap
