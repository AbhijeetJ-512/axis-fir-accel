// -----------------------------------------------------------------------------
// csr_regfile.sv
//
// Control and status register file, and the SINGLE point of address decode for
// the whole IP -- including the coefficient region, which it forwards to
// coeff_bank through a decoded port. Keeping decode in one module means there
// is exactly one place to look when software reports a wrong address.
//
// Register semantics implemented here:
//   RW    CTRL.ENABLE, CFG, IRQ_ENABLE
//   W1P   CTRL.SOFT_RESET / COEFF_COMMIT / FLUSH / CNT_CLR  (self-clearing pulse)
//   RO    ID, CAPABILITY, STATUS, SAMPLE_CNT, FRAME_CNT
//   RW1C  IRQ_STATUS
//
// Two decisions worth flagging to a reviewer:
//
//   1. RW1C is SET-WINS. If a hardware event and a software clear land on the
//      same cycle, the event survives. The opposite policy silently drops
//      interrupts under load, which is the classic RW1C bug.
//
//   2. Writes to read-only registers are IGNORED and answered OKAY, not
//      SLVERR. Only unmapped addresses return SLVERR. This follows the usual
//      ARM-style convention and keeps read-modify-write driver code from
//      generating spurious bus errors. The distinction is documented in
//      docs/04_interfaces.md so it is a contract, not an accident.
// -----------------------------------------------------------------------------
`ifndef CSR_REGFILE_SV
`define CSR_REGFILE_SV

`include "fir_defs.svh"

module csr_regfile
#(
  parameter int ADDR_W       = 12,     // AXI4-Lite address width
  parameter int AXIL_DATA_W  = 32,     // AXI4-Lite data width (fixed 32 here)
  parameter int DATA_W       = 16,     // sample width  (reported in CAPABILITY)
  parameter int COEF_W       = 16,     // coefficient width
  parameter int NTAPS_MAX    = 64,
  parameter int MAC_UNITS    = 8,
  parameter bit SYMMETRIC_EN = 1'b1
) (
  input  logic                    clk,
  input  logic                    rst_n,

  // ---- Internal register bus (from axil_slave) -----------------------------
  input  logic [ADDR_W-1:0]       reg_waddr,
  input  logic [AXIL_DATA_W-1:0]  reg_wdata,
  input  logic [AXIL_DATA_W/8-1:0]reg_wstrb,
  input  logic                    reg_wen,
  output logic                    reg_werr,

  input  logic [ADDR_W-1:0]       reg_raddr,
  input  logic                    reg_ren,
  output logic [AXIL_DATA_W-1:0]  reg_rdata,
  output logic                    reg_rerr,

  // ---- Decoded coefficient port (to coeff_bank shadow side) ----------------
  // Read and write addresses are separate. A shared port would be corrupted by
  // a simultaneous AXI read and write, which the independent AXI4-Lite channels
  // make perfectly legal.
  output logic                    coef_we,
  output logic [fir_pkg::COEF_IDX_W-1:0]   coef_waddr,
  output logic [COEF_W-1:0]       coef_wdata,
  output logic [fir_pkg::COEF_IDX_W-1:0]   coef_raddr,
  input  logic [COEF_W-1:0]       coef_rdata,   // comb. function of coef_raddr

  // ---- Configuration to the datapath ---------------------------------------
  output logic                    ctrl_enable,
  output logic [fir_pkg::CFG_NTAPS_W-1:0]  cfg_ntaps,
  output logic [fir_pkg::CFG_ROUND_W-1:0]  cfg_round_mode,
  output logic                    cfg_sat_mode,
  output logic                    cfg_symmetric,
  output logic                    cfg_illegal,

  // ---- Self-clearing command pulses ----------------------------------------
  output logic                    cmd_soft_reset,
  output logic                    cmd_coeff_commit,
  output logic                    cmd_flush,

  // ---- Status from the datapath --------------------------------------------
  input  logic                    sts_busy,
  input  logic                    sts_draining,
  input  logic                    sts_coeff_pending,
  input  logic                    sts_in_fifo_empty,
  input  logic                    sts_out_fifo_full,

  // ---- Event strobes from the datapath -------------------------------------
  input  logic                    evt_sample,       // one accepted sample
  input  logic                    evt_frame_done,   // TLAST beat left the IP
  input  logic                    evt_saturate,     // output stage saturated

  // ---- Interrupt ------------------------------------------------------------
  output logic                    irq
);

  localparam int STRB_W = AXIL_DATA_W/8;
  localparam int ACC_W  = `FIR_ACC_W(DATA_W, COEF_W, NTAPS_MAX);

  // Width-matched copies of NTAPS_MAX for the two range comparisons.
  //
  // NTAPS_MAX_IDX is deliberately ONE BIT WIDER than the coefficient index.
  // At the maximum supported NTAPS_MAX (256) the value does not fit in
  // COEF_IDX_W = 8 bits: an 8-bit copy would truncate to 0 and reject every
  // coefficient address in the build that needs them most.
  localparam logic [fir_pkg::COEF_IDX_W:0]    NTAPS_MAX_IDX =
      (fir_pkg::COEF_IDX_W+1)'(NTAPS_MAX);
  localparam logic [fir_pkg::CFG_NTAPS_W-1:0] NTAPS_MAX_CFG =
      fir_pkg::CFG_NTAPS_W'(NTAPS_MAX);

  // Byte-strobe merge. Declared here rather than in fir_pkg because Yosys 0.33
  // does not accept functions inside a package (see fir_defs.svh).
  function automatic logic [31:0] apply_strb(input logic [31:0] old_val,
                                             input logic [31:0] new_val,
                                             input logic [3:0]  strb);
    logic [31:0] merged;
    begin
      for (int b = 0; b < 4; b++) begin
        merged[b*8 +: 8] = strb[b] ? new_val[b*8 +: 8] : old_val[b*8 +: 8];
      end
      apply_strb = merged;
    end
  endfunction

  // ---------------------------------------------------------------------------
  // Elaboration-time legality
  // ---------------------------------------------------------------------------
  initial begin
    if (ADDR_W < 12)
      $fatal(1, "csr_regfile: ADDR_W must be >= 12 to cover the coefficient region (got %0d).", ADDR_W);
    if (AXIL_DATA_W != 32)
      $fatal(1, "csr_regfile: only AXIL_DATA_W = 32 is supported (got %0d).", AXIL_DATA_W);
    if (NTAPS_MAX > (1 << fir_pkg::COEF_IDX_W))
      $fatal(1, "csr_regfile: NTAPS_MAX (%0d) exceeds the addressable coefficient region.", NTAPS_MAX);
    if (COEF_W > 16)
      $fatal(1, "csr_regfile: COEF_W > 16 does not fit one coefficient per 32-bit word cleanly (got %0d).", COEF_W);
  end

  // ---------------------------------------------------------------------------
  // Address decode
  //
  // Decode is on the low 12 bits; any address bit above bit 11 must be zero for
  // the access to be considered mapped, so an oversized ADDR_W aliases nothing.
  // ---------------------------------------------------------------------------
  logic [11:0] waddr12, raddr12;
  assign waddr12 = reg_waddr[11:0];
  assign raddr12 = reg_raddr[11:0];

  logic w_upper_zero, r_upper_zero;
  generate
    if (ADDR_W > 12) begin : g_wide_addr
      assign w_upper_zero = (reg_waddr[ADDR_W-1:12] == '0);
      assign r_upper_zero = (reg_raddr[ADDR_W-1:12] == '0);
    end else begin : g_exact_addr
      assign w_upper_zero = 1'b1;
      assign r_upper_zero = 1'b1;
    end
  endgenerate

  logic w_csr_region, r_csr_region;
  logic w_coef_region, r_coef_region;
  assign w_csr_region  = w_upper_zero && (waddr12[11:8] == 4'h0);
  assign r_csr_region  = r_upper_zero && (raddr12[11:8] == 4'h0);
  assign w_coef_region = w_upper_zero && (waddr12[11:10] == fir_pkg::COEF_REGION_SEL);
  assign r_coef_region = r_upper_zero && (raddr12[11:10] == fir_pkg::COEF_REGION_SEL);

  logic [fir_pkg::COEF_IDX_W-1:0] w_coef_idx, r_coef_idx;
  assign w_coef_idx = waddr12[fir_pkg::COEF_IDX_LSB +: fir_pkg::COEF_IDX_W];
  assign r_coef_idx = raddr12[fir_pkg::COEF_IDX_LSB +: fir_pkg::COEF_IDX_W];

  logic w_coef_valid, r_coef_valid;
  // Plain unsigned comparison rather than an int'() cast: Yosys 0.33 rejects
  // int'(). Both index and bound are non-negative and the index is unsigned, so
  // the expression is evaluated unsigned and the cast added nothing anyway.
  assign w_coef_valid = w_coef_region && ({1'b0, w_coef_idx} < NTAPS_MAX_IDX);
  assign r_coef_valid = r_coef_region && ({1'b0, r_coef_idx} < NTAPS_MAX_IDX);

  // Is this CSR word address implemented at all?
  logic w_csr_mapped, r_csr_mapped;
  always_comb begin
    case (waddr12)
      fir_pkg::REG_ID, fir_pkg::REG_CAPABILITY, fir_pkg::REG_CTRL, fir_pkg::REG_CFG, fir_pkg::REG_STATUS,
      fir_pkg::REG_IRQ_STATUS, fir_pkg::REG_IRQ_ENABLE, fir_pkg::REG_SAMPLE_CNT, fir_pkg::REG_FRAME_CNT:
        w_csr_mapped = w_csr_region;
      default:
        w_csr_mapped = 1'b0;
    endcase
    case (raddr12)
      fir_pkg::REG_ID, fir_pkg::REG_CAPABILITY, fir_pkg::REG_CTRL, fir_pkg::REG_CFG, fir_pkg::REG_STATUS,
      fir_pkg::REG_IRQ_STATUS, fir_pkg::REG_IRQ_ENABLE, fir_pkg::REG_SAMPLE_CNT, fir_pkg::REG_FRAME_CNT:
        r_csr_mapped = r_csr_region;
      default:
        r_csr_mapped = 1'b0;
    endcase
  end

  // Read-only CSRs: writes land here harmlessly and are answered OKAY.
  logic w_is_ro;
  always_comb begin
    case (waddr12)
      fir_pkg::REG_ID, fir_pkg::REG_CAPABILITY, fir_pkg::REG_STATUS, fir_pkg::REG_SAMPLE_CNT, fir_pkg::REG_FRAME_CNT:
        w_is_ro = 1'b1;
      default:
        w_is_ro = 1'b0;
    endcase
  end

  // ---------------------------------------------------------------------------
  // Architectural state
  // ---------------------------------------------------------------------------
  logic                    ctrl_enable_q;
  logic [fir_pkg::CFG_NTAPS_W-1:0]  cfg_ntaps_q;
  logic [fir_pkg::CFG_ROUND_W-1:0]  cfg_round_q;
  logic                    cfg_sat_q;
  logic                    cfg_sym_q;
  logic [fir_pkg::IRQ_W-1:0]        irq_status_q;
  logic [fir_pkg::IRQ_W-1:0]        irq_enable_q;
  logic                    sat_sticky_q;
  logic                    cfg_err_sticky_q;
  logic [31:0]             sample_cnt_q;
  logic [31:0]             frame_cnt_q;

  logic cmd_soft_reset_q, cmd_commit_q, cmd_flush_q, cmd_cnt_clr_q;

  // ---------------------------------------------------------------------------
  // Readback composition (also used as the "old value" for strobed writes, so
  // that a partial byte write preserves the untouched bytes exactly)
  // ---------------------------------------------------------------------------
  logic [31:0] ctrl_rd, cfg_rd, status_rd;
  logic [31:0] capability_rd;

  // Command bits always read back 0: they are pulses, never state.
  always_comb begin
    ctrl_rd                       = 32'h0;
    ctrl_rd[fir_pkg::CTRL_ENABLE_BIT]      = ctrl_enable_q;
  end

  always_comb begin
    cfg_rd                                  = 32'h0;
    cfg_rd[fir_pkg::CFG_NTAPS_LSB +: fir_pkg::CFG_NTAPS_W]    = cfg_ntaps_q;
    cfg_rd[fir_pkg::CFG_ROUND_LSB +: fir_pkg::CFG_ROUND_W]    = cfg_round_q;
    cfg_rd[fir_pkg::CFG_SAT_BIT]                     = cfg_sat_q;
    cfg_rd[fir_pkg::CFG_SYM_BIT]                     = cfg_sym_q;
  end

  always_comb begin
    status_rd                       = 32'h0;
    status_rd[fir_pkg::STS_IDLE_BIT]         = !sts_busy && !sts_draining;
    status_rd[fir_pkg::STS_BUSY_BIT]         = sts_busy;
    status_rd[fir_pkg::STS_COEFPEND_BIT]     = sts_coeff_pending;
    status_rd[fir_pkg::STS_CFGERR_BIT]       = cfg_err_sticky_q;
    status_rd[fir_pkg::STS_SATSTKY_BIT]      = sat_sticky_q;
    status_rd[fir_pkg::STS_INEMPTY_BIT]      = sts_in_fifo_empty;
    status_rd[fir_pkg::STS_OUTFULL_BIT]      = sts_out_fifo_full;
    status_rd[fir_pkg::STS_DRAIN_BIT]        = sts_draining;
  end

  // CAPABILITY lets software discover the synthesised build without a separate
  // device-tree entry. Widths are stored as log2 where the value is always a
  // power of two, and directly otherwise.
  //
  // This is a pure function of the parameters, so it is a localparam rather
  // than a process -- it must synthesise to constant tie-offs, not logic.
  localparam logic [31:0] CAPABILITY_VAL = {
      SYMMETRIC_EN,          // [31]
      7'(ACC_W),             // [30:24]
      8'(COEF_W),            // [23:16]
      8'(DATA_W),            // [15:8]
      4'($clog2(MAC_UNITS)), // [7:4]
      4'($clog2(NTAPS_MAX))  // [3:0]
  };
  assign capability_rd = CAPABILITY_VAL;

  // ---------------------------------------------------------------------------
  // Strobed write data
  // ---------------------------------------------------------------------------
  logic [31:0] wr_ctrl, wr_cfg, wr_irq_en, wr_irq_clr;
  assign wr_ctrl    = apply_strb(ctrl_rd,             reg_wdata, reg_wstrb);
  assign wr_cfg     = apply_strb(cfg_rd,              reg_wdata, reg_wstrb);
  assign wr_irq_en  = apply_strb({29'h0, irq_enable_q}, reg_wdata, reg_wstrb);
  // For W1C the "old value" is zero, so bytes that are not strobed contribute
  // no clear bits and therefore cannot clear anything by accident.
  assign wr_irq_clr = apply_strb(32'h0,               reg_wdata, reg_wstrb);

  logic csr_wr;
  assign csr_wr = reg_wen && w_csr_mapped && !w_is_ro && !reg_werr;

  // ---------------------------------------------------------------------------
  // Configuration legality
  //
  // Evaluated on the CURRENT register contents, so an illegal value is flagged
  // from the cycle it is written, not one transaction later.
  // ---------------------------------------------------------------------------
  logic ntaps_bad, round_bad;
  assign ntaps_bad   = (cfg_ntaps_q == '0) || (cfg_ntaps_q > NTAPS_MAX_CFG);
  assign round_bad   = (cfg_round_q == fir_pkg::RND_RESERVED);
  assign cfg_illegal = ntaps_bad || round_bad;

  // cfg_illegal is a pure validity indicator and is true out of reset, because
  // NTAPS resets to 0. The sticky CFG_ERR status and its interrupt must NOT
  // fire on that: an unconfigured block is not a faulted block. The error is
  // raised only when software actually asks an illegally-configured block to
  // run, which is exactly what FR-21 specifies.
  logic cfg_err_event;
  assign cfg_err_event = ctrl_enable_q && cfg_illegal;

  // ---------------------------------------------------------------------------
  // Sequential
  // ---------------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ctrl_enable_q    <= 1'b0;
      cfg_ntaps_q      <= '0;
      cfg_round_q      <= fir_pkg::RND_TRUNC;
      cfg_sat_q        <= fir_pkg::SAT_ASYMMETRIC;
      cfg_sym_q        <= 1'b0;
      irq_status_q     <= '0;
      irq_enable_q     <= '0;
      sat_sticky_q     <= 1'b0;
      cfg_err_sticky_q <= 1'b0;
      sample_cnt_q     <= 32'h0;
      frame_cnt_q      <= 32'h0;
      cmd_soft_reset_q <= 1'b0;
      cmd_commit_q     <= 1'b0;
      cmd_flush_q      <= 1'b0;
      cmd_cnt_clr_q    <= 1'b0;
    end else begin
      // Command pulses are one cycle wide unless re-written.
      cmd_soft_reset_q <= 1'b0;
      cmd_commit_q     <= 1'b0;
      cmd_flush_q      <= 1'b0;
      cmd_cnt_clr_q    <= 1'b0;

      if (csr_wr) begin
        case (waddr12)
          fir_pkg::REG_CTRL: begin
            ctrl_enable_q    <= wr_ctrl[fir_pkg::CTRL_ENABLE_BIT];
            cmd_soft_reset_q <= wr_ctrl[fir_pkg::CTRL_SOFTRST_BIT];
            cmd_commit_q     <= wr_ctrl[fir_pkg::CTRL_COMMIT_BIT];
            cmd_flush_q      <= wr_ctrl[fir_pkg::CTRL_FLUSH_BIT];
            cmd_cnt_clr_q    <= wr_ctrl[fir_pkg::CTRL_CNTCLR_BIT];
          end
          fir_pkg::REG_CFG: begin
            cfg_ntaps_q <= wr_cfg[fir_pkg::CFG_NTAPS_LSB +: fir_pkg::CFG_NTAPS_W];
            cfg_round_q <= wr_cfg[fir_pkg::CFG_ROUND_LSB +: fir_pkg::CFG_ROUND_W];
            cfg_sat_q   <= wr_cfg[fir_pkg::CFG_SAT_BIT];
            // When the symmetric datapath is not synthesised the bit is tied
            // off rather than reported as an error: software reads back what it
            // actually got.
            cfg_sym_q   <= SYMMETRIC_EN ? wr_cfg[fir_pkg::CFG_SYM_BIT] : 1'b0;
          end
          fir_pkg::REG_IRQ_ENABLE: begin
            irq_enable_q <= wr_irq_en[fir_pkg::IRQ_W-1:0];
          end
          fir_pkg::REG_IRQ_STATUS: begin
            irq_status_q <= irq_status_q & ~wr_irq_clr[fir_pkg::IRQ_W-1:0];
          end
          default: ; // mapped, writable, but no side effect
        endcase
      end

      // --- Event capture -----------------------------------------------------
      // Placed AFTER the write case on purpose: a hardware event occurring in
      // the same cycle as a software W1C wins, so no interrupt is ever lost.
      if (evt_frame_done) irq_status_q[fir_pkg::IRQ_FRAME_DONE_BIT] <= 1'b1;
      if (evt_saturate)   irq_status_q[fir_pkg::IRQ_SAT_EVENT_BIT]  <= 1'b1;
      if (cfg_err_event)  irq_status_q[fir_pkg::IRQ_CFG_ERR_BIT]    <= 1'b1;

      if (evt_saturate)  sat_sticky_q     <= 1'b1;
      if (cfg_err_event) cfg_err_sticky_q <= 1'b1;

      // Sticky status bits share the W1C write with their IRQ counterparts, so
      // one clear operation resets both views. Event-wins ordering applies here
      // too, which is why these follow the set arms above.
      if (csr_wr && (waddr12 == fir_pkg::REG_IRQ_STATUS)) begin
        if (wr_irq_clr[fir_pkg::IRQ_SAT_EVENT_BIT] && !evt_saturate)  sat_sticky_q     <= 1'b0;
        if (wr_irq_clr[fir_pkg::IRQ_CFG_ERR_BIT]   && !cfg_err_event) cfg_err_sticky_q <= 1'b0;
      end

      // --- Performance counters (free-running, wrap on overflow) -------------
      if (cmd_cnt_clr_q) begin
        sample_cnt_q <= 32'h0;
        frame_cnt_q  <= 32'h0;
      end else begin
        if (evt_sample)     sample_cnt_q <= sample_cnt_q + 32'd1;
        if (evt_frame_done) frame_cnt_q  <= frame_cnt_q  + 32'd1;
      end
    end
  end

  // ---------------------------------------------------------------------------
  // Write error generation
  //
  // SLVERR only for genuinely unmapped addresses, and for CFG writes while the
  // datapath is BUSY (requirement FR-12) -- the latter is a real protection,
  // since silently accepting a tap-count change mid-stream would corrupt data.
  // ---------------------------------------------------------------------------
  logic w_mapped, cfg_wr_blocked;
  assign w_mapped       = w_csr_mapped || w_coef_valid;
  assign cfg_wr_blocked = w_csr_mapped && (waddr12 == fir_pkg::REG_CFG) && sts_busy;
  assign reg_werr       = !w_mapped || cfg_wr_blocked;

  // ---------------------------------------------------------------------------
  // Coefficient port
  // ---------------------------------------------------------------------------
  assign coef_we    = reg_wen && w_coef_valid;
  assign coef_waddr = w_coef_idx;
  assign coef_wdata = reg_wdata[COEF_W-1:0];
  assign coef_raddr = r_coef_idx;

  // ---------------------------------------------------------------------------
  // Read mux
  // ---------------------------------------------------------------------------
  always_comb begin
    reg_rdata = 32'h0;
    reg_rerr  = 1'b1;                       // default: unmapped

    if (r_csr_mapped) begin
      reg_rerr = 1'b0;
      case (raddr12)
        fir_pkg::REG_ID:         reg_rdata = fir_pkg::IP_ID;
        fir_pkg::REG_CAPABILITY: reg_rdata = capability_rd;
        fir_pkg::REG_CTRL:       reg_rdata = ctrl_rd;
        fir_pkg::REG_CFG:        reg_rdata = cfg_rd;
        fir_pkg::REG_STATUS:     reg_rdata = status_rd;
        fir_pkg::REG_IRQ_STATUS: reg_rdata = {29'h0, irq_status_q};
        fir_pkg::REG_IRQ_ENABLE: reg_rdata = {29'h0, irq_enable_q};
        fir_pkg::REG_SAMPLE_CNT: reg_rdata = sample_cnt_q;
        fir_pkg::REG_FRAME_CNT:  reg_rdata = frame_cnt_q;
        default:        reg_rdata = 32'h0;  // unreachable: guarded by r_csr_mapped
      endcase
    end else if (r_coef_valid) begin
      reg_rerr  = 1'b0;
      reg_rdata = {{(32-COEF_W){1'b0}}, coef_rdata};
    end
  end

  // ---------------------------------------------------------------------------
  // Outputs
  // ---------------------------------------------------------------------------
  assign ctrl_enable      = ctrl_enable_q;
  assign cfg_ntaps        = cfg_ntaps_q;
  assign cfg_round_mode   = cfg_round_q;
  assign cfg_sat_mode     = cfg_sat_q;
  assign cfg_symmetric    = cfg_sym_q;
  assign cmd_soft_reset   = cmd_soft_reset_q;
  assign cmd_coeff_commit = cmd_commit_q;
  assign cmd_flush        = cmd_flush_q;
  assign irq              = |(irq_status_q & irq_enable_q);

endmodule : csr_regfile

`endif
