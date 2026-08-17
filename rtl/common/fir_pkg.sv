// -----------------------------------------------------------------------------
// fir_pkg.sv
//
// Shared constants, encodings and derivation functions for axis_fir_accel.
//
// Nothing in this package is a "magic number": every register offset, field
// position and derived width is named here exactly once and referenced by name
// everywhere else, in both RTL and (mirrored) in the Python testbench.
// -----------------------------------------------------------------------------
`ifndef FIR_PKG_SV
`define FIR_PKG_SV

package fir_pkg;

  // ---------------------------------------------------------------------------
  // Identification
  // ---------------------------------------------------------------------------
  localparam logic [31:0] IP_ID = 32'h4649_5230;   // ASCII "FIR0"

  // ---------------------------------------------------------------------------
  // Register map -- byte offsets within the AXI4-Lite address space
  //
  // CSR region  : 0x000 .. 0x0FF   (decoded as addr[11:8] == 4'h0)
  // COEF region : 0x400 .. 0x7FF   (decoded as addr[11:10] == 2'b01)
  //
  // The gap between them is deliberate: it leaves 0x100..0x3FF free for future
  // CSRs without moving the coefficient region, which software would notice.
  // ---------------------------------------------------------------------------
  localparam logic [11:0] REG_ID         = 12'h000;  // RO
  localparam logic [11:0] REG_CAPABILITY = 12'h004;  // RO
  localparam logic [11:0] REG_CTRL       = 12'h008;  // RW
  localparam logic [11:0] REG_CFG        = 12'h00C;  // RW (SLVERR while BUSY)
  localparam logic [11:0] REG_STATUS     = 12'h010;  // RO
  localparam logic [11:0] REG_IRQ_STATUS = 12'h014;  // RW1C
  localparam logic [11:0] REG_IRQ_ENABLE = 12'h018;  // RW
  localparam logic [11:0] REG_SAMPLE_CNT = 12'h01C;  // RO
  localparam logic [11:0] REG_FRAME_CNT  = 12'h020;  // RO

  localparam logic [11:0] CSR_ADDR_MAX   = 12'h020;  // highest implemented CSR

  // ---------------------------------------------------------------------------
  // CTRL field positions
  // ---------------------------------------------------------------------------
  localparam int CTRL_ENABLE_BIT  = 0;   // RW,  level
  localparam int CTRL_SOFTRST_BIT = 1;   // W1P, self-clearing pulse
  localparam int CTRL_COMMIT_BIT  = 2;   // W1P, coefficient bank commit request
  localparam int CTRL_FLUSH_BIT   = 3;   // W1P, flush delay line
  localparam int CTRL_CNTCLR_BIT  = 4;   // W1P, clear performance counters

  // ---------------------------------------------------------------------------
  // CFG field positions
  //
  // NTAPS is 9 bits so that the maximum supported NTAPS_MAX (256) is
  // representable. Fields are placed on byte boundaries so that software can
  // update one field with a single strobed byte write.
  // ---------------------------------------------------------------------------
  localparam int CFG_NTAPS_LSB = 0;   localparam int CFG_NTAPS_W = 9;
  localparam int CFG_ROUND_LSB = 16;  localparam int CFG_ROUND_W = 2;
  localparam int CFG_SAT_BIT   = 20;
  localparam int CFG_SYM_BIT   = 24;

  // ---------------------------------------------------------------------------
  // STATUS field positions
  // ---------------------------------------------------------------------------
  localparam int STS_IDLE_BIT     = 0;
  localparam int STS_BUSY_BIT     = 1;
  localparam int STS_COEFPEND_BIT = 2;
  localparam int STS_CFGERR_BIT   = 3;
  localparam int STS_SATSTKY_BIT  = 4;
  localparam int STS_INEMPTY_BIT  = 5;
  localparam int STS_OUTFULL_BIT  = 6;
  localparam int STS_DRAIN_BIT    = 7;

  // ---------------------------------------------------------------------------
  // IRQ bit positions (shared by IRQ_STATUS and IRQ_ENABLE)
  // ---------------------------------------------------------------------------
  localparam int IRQ_FRAME_DONE_BIT = 0;
  localparam int IRQ_SAT_EVENT_BIT  = 1;
  localparam int IRQ_CFG_ERR_BIT    = 2;
  localparam int IRQ_W              = 3;

  // ---------------------------------------------------------------------------
  // Coefficient region decode
  // ---------------------------------------------------------------------------
  localparam logic [1:0] COEF_REGION_SEL = 2'b01;   // addr[11:10]
  localparam int         COEF_IDX_LSB    = 2;       // word-addressed
  localparam int         COEF_IDX_W      = 8;       // addr[9:2] -> up to 256 taps

  // ---------------------------------------------------------------------------
  // Encodings
  // ---------------------------------------------------------------------------
  typedef enum logic [1:0] {
    RND_TRUNC      = 2'b00,   // discard dropped bits (biased toward -inf)
    RND_HALF_UP    = 2'b01,   // add half an LSB then truncate (biased toward +inf)
    RND_CONVERGENT = 2'b10,   // round half to even (unbiased)
    RND_RESERVED   = 2'b11    // illegal -> CFG_ERR
  } round_mode_e;

  typedef enum logic {
    SAT_ASYMMETRIC = 1'b0,    // clamp to [-2^(W-1)   , 2^(W-1)-1]
    SAT_SYMMETRIC  = 1'b1     // clamp to [-(2^(W-1)-1), 2^(W-1)-1]
  } sat_mode_e;

  typedef enum logic [1:0] {
    ST_IDLE   = 2'd0,
    ST_RUN    = 2'd1,
    ST_DRAIN  = 2'd2,
    ST_CFGERR = 2'd3
  } fir_state_e;

  // AXI response codes
  localparam logic [1:0] RESP_OKAY   = 2'b00;
  localparam logic [1:0] RESP_SLVERR = 2'b10;

  // ---------------------------------------------------------------------------
  // Derivations
  //
  // NOTE: the width derivations deliberately live in rtl/common/fir_defs.svh as
  // preprocessor macros, NOT as functions here. Yosys 0.33 rejects functions
  // inside a package, so a package function would compile under Icarus and then
  // break synthesis -- a failure mode that stays hidden until Phase 8. See the
  // header of fir_defs.svh for the full reasoning.
  //
  // Helper functions that are used by exactly one module (such as the CSR
  // byte-strobe merge) are declared inside that module for the same reason.
  // ---------------------------------------------------------------------------

endpackage : fir_pkg

`endif
