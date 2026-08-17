// -----------------------------------------------------------------------------
// axis_fifo.sv
//
// Synchronous elastic buffer carrying an AXI4-Stream payload {TLAST, TDATA}.
//
// This module is not an optimisation -- it is the mechanism by which two of the
// IP's interface requirements are met BY CONSTRUCTION rather than by added
// logic (docs/02_architecture.md section 3.5):
//
//   s_tready = !full     -- a function of occupancy only, so there is no
//                           combinational path from the downstream m_tready
//                           back to the upstream s_tready (requirement TR-04).
//
//   m_tvalid = !empty    -- and m_tdata/m_tlast are the memory read at rd_ptr,
//   m_tdata  = mem[rd]      which only advances on an accepted pop. The payload
//                           is therefore stable for as long as the consumer
//                           stalls, with no explicit hold register
//                           (requirement FR-19).
//
// Pointer scheme: PTR_W = clog2(DEPTH)+1. The extra MSB distinguishes "full"
// from "empty" when the index bits are equal, and makes occupancy a plain
// subtraction. This is deliberately the same scheme the Phase 7 asynchronous
// FIFO will use with Gray-coded pointers, so the two stay structurally
// comparable.
//
// Memory read is asynchronous (a register file, not a RAM macro). For the
// depths this IP uses (2..64 entries) synthesis produces flops and a mux, which
// is what an ASIC flow without compiled memories would produce anyway. A
// RAM-backed variant would need an output register and lookahead logic; that is
// a Phase 10 question, recorded in docs/13_ppa_optimization.md.
// -----------------------------------------------------------------------------
`ifndef AXIS_FIFO_SV
`define AXIS_FIFO_SV

module axis_fifo #(
  parameter int DATA_W = 16,
  parameter int DEPTH  = 8      // power of two, >= 2
) (
  input  logic                    clk,
  input  logic                    rst_n,
  input  logic                    soft_clear,   // synchronous flush; discards contents

  // ---- Slave (write) side ---------------------------------------------------
  input  logic [DATA_W-1:0]       s_tdata,
  input  logic                    s_tlast,
  input  logic                    s_tvalid,
  output logic                    s_tready,

  // ---- Master (read) side ---------------------------------------------------
  output logic [DATA_W-1:0]       m_tdata,
  output logic                    m_tlast,
  output logic                    m_tvalid,
  input  logic                    m_tready,

  // ---- Status ---------------------------------------------------------------
  output logic [$clog2(DEPTH):0]  count,        // 0 .. DEPTH
  output logic                    full,
  output logic                    empty
);

  localparam int PTR_W = $clog2(DEPTH) + 1;
  localparam int IDX_W = $clog2(DEPTH);
  localparam int PAY_W = DATA_W + 1;            // {tlast, tdata}

  // ---------------------------------------------------------------------------
  // Elaboration-time legality
  // ---------------------------------------------------------------------------
  initial begin
    if (DEPTH < 2)
      $fatal(1, "axis_fifo: DEPTH must be >= 2 (got %0d). A depth-1 buffer cannot decouple the two handshakes.", DEPTH);
    if ((DEPTH & (DEPTH-1)) != 0)
      $fatal(1, "axis_fifo: DEPTH must be a power of two (got %0d). The wrap-bit pointer scheme depends on it.", DEPTH);
  end

  // ---------------------------------------------------------------------------
  // Storage and pointers
  // ---------------------------------------------------------------------------
  logic [PAY_W-1:0] mem [0:DEPTH-1];
  logic [PTR_W-1:0] wr_ptr, rd_ptr;

  logic wr_en, rd_en;
  assign wr_en = s_tvalid && s_tready;
  assign rd_en = m_tvalid && m_tready;

  assign empty = (wr_ptr == rd_ptr);
  assign full  = (wr_ptr[PTR_W-1] != rd_ptr[PTR_W-1]) &&
                 (wr_ptr[IDX_W-1:0] == rd_ptr[IDX_W-1:0]);
  assign count = wr_ptr - rd_ptr;

  assign s_tready = !full;
  assign m_tvalid = !empty;

  // Asynchronous read. Legal to read an unwritten entry only while empty, and
  // m_tvalid is low then -- so the X that appears on m_tdata in simulation is
  // deliberate: it catches a consumer that samples TDATA without TVALID.
  assign {m_tlast, m_tdata} = mem[rd_ptr[IDX_W-1:0]];

  // Payload memory is not reset: it is written before it is ever read, and
  // resetting DEPTH x (DATA_W+1) flops buys nothing. Same reasoning as the
  // coefficient banks (engineering log D-009).
  always_ff @(posedge clk) begin
    if (wr_en) begin
      mem[wr_ptr[IDX_W-1:0]] <= {s_tlast, s_tdata};
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      wr_ptr <= '0;
      rd_ptr <= '0;
    end else if (soft_clear) begin
      wr_ptr <= '0;
      rd_ptr <= '0;
    end else begin
      if (wr_en) wr_ptr <= wr_ptr + 1'b1;
      if (rd_en) rd_ptr <= rd_ptr + 1'b1;
    end
  end

endmodule : axis_fifo

`endif
