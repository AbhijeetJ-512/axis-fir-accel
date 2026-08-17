// -----------------------------------------------------------------------------
// axil_slave.sv
//
// AXI4-Lite slave protocol engine.
//
// Responsibility: terminate the five AXI4-Lite channels and present a simple
// internal register bus. This module knows NOTHING about register semantics --
// it does not know what an address means, only how to hand it over. All decode
// and all side effects live in csr_regfile.
//
// Concurrency: one outstanding write, one outstanding read, independent.
//
// Timing contract (docs/01_requirements.md TR-04):
//   - AWREADY and WREADY depend only on REGISTERED state (bvalid_q, aw_full_q,
//     w_full_q). Neither has a combinational path from BREADY.
//   - ARREADY depends only on rvalid_q. No combinational path from RREADY.
//   This is what makes the slave safe to attach to any interconnect without
//   creating a cross-channel combinational loop.
//
// Throughput cost of that contract: because AWREADY is held low while a B
// response is outstanding, a write takes 3 cycles rather than 1. This is a
// deliberate trade -- AXI4-Lite here carries one-time configuration traffic, so
// register-interface bandwidth is irrelevant, while a combinational path from
// BREADY back to AWREADY would be a real integration hazard.
// -----------------------------------------------------------------------------
`ifndef AXIL_SLAVE_SV
`define AXIL_SLAVE_SV

module axil_slave
#(
  parameter int ADDR_W = 12,
  parameter int DATA_W = 32
) (
  input  logic                    clk,
  input  logic                    rst_n,

  // ---- AXI4-Lite slave -----------------------------------------------------
  input  logic [ADDR_W-1:0]       s_axil_awaddr,
  input  logic [2:0]              s_axil_awprot,
  input  logic                    s_axil_awvalid,
  output logic                    s_axil_awready,

  input  logic [DATA_W-1:0]       s_axil_wdata,
  input  logic [DATA_W/8-1:0]     s_axil_wstrb,
  input  logic                    s_axil_wvalid,
  output logic                    s_axil_wready,

  output logic [1:0]              s_axil_bresp,
  output logic                    s_axil_bvalid,
  input  logic                    s_axil_bready,

  input  logic [ADDR_W-1:0]       s_axil_araddr,
  input  logic [2:0]              s_axil_arprot,
  input  logic                    s_axil_arvalid,
  output logic                    s_axil_arready,

  output logic [DATA_W-1:0]       s_axil_rdata,
  output logic [1:0]              s_axil_rresp,
  output logic                    s_axil_rvalid,
  input  logic                    s_axil_rready,

  // ---- Internal register bus ----------------------------------------------
  // Write and read sides are separate so that the two AXI channels stay
  // independent, as the protocol intends.
  output logic [ADDR_W-1:0]       reg_waddr,
  output logic [DATA_W-1:0]       reg_wdata,
  output logic [DATA_W/8-1:0]     reg_wstrb,
  output logic                    reg_wen,     // single-cycle pulse
  input  logic                    reg_werr,    // comb. valid when reg_wen is high

  output logic [ADDR_W-1:0]       reg_raddr,
  output logic                    reg_ren,     // single-cycle pulse
  input  logic [DATA_W-1:0]       reg_rdata,   // comb. function of reg_raddr
  input  logic                    reg_rerr     // comb. function of reg_raddr
);

  localparam int STRB_W = DATA_W/8;

  // ---------------------------------------------------------------------------
  // Write channel
  //
  // AW and W may arrive in any order, or together. Each is captured into a
  // holding register if it arrives without its partner. The register write
  // fires on the cycle both halves are available -- taken directly from the bus
  // if arriving now, or from the holding register if captured earlier.
  // ---------------------------------------------------------------------------
  logic                aw_full_q, w_full_q;
  logic [ADDR_W-1:0]   awaddr_q;
  logic [DATA_W-1:0]   wdata_q;
  logic [STRB_W-1:0]   wstrb_q;
  logic                bvalid_q;
  logic [1:0]          bresp_q;

  // No new write may be accepted while a response is outstanding.
  logic wr_slot_free;
  assign wr_slot_free = !bvalid_q;

  assign s_axil_awready = wr_slot_free && !aw_full_q;
  assign s_axil_wready  = wr_slot_free && !w_full_q;

  logic aw_hs, w_hs;
  assign aw_hs = s_axil_awvalid && s_axil_awready;
  assign w_hs  = s_axil_wvalid  && s_axil_wready;

  logic aw_have, w_have;
  assign aw_have = aw_full_q || aw_hs;
  assign w_have  = w_full_q  || w_hs;

  assign reg_wen   = aw_have && w_have && wr_slot_free;
  assign reg_waddr = aw_full_q ? awaddr_q : s_axil_awaddr;
  assign reg_wdata = w_full_q  ? wdata_q  : s_axil_wdata;
  assign reg_wstrb = w_full_q  ? wstrb_q  : s_axil_wstrb;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      aw_full_q <= 1'b0;
      w_full_q  <= 1'b0;
      awaddr_q  <= '0;
      wdata_q   <= '0;
      wstrb_q   <= '0;
      bvalid_q  <= 1'b0;
      bresp_q   <= fir_pkg::RESP_OKAY;
    end else begin
      if (reg_wen) begin
        // Both halves consumed this cycle; issue the response.
        aw_full_q <= 1'b0;
        w_full_q  <= 1'b0;
        bvalid_q  <= 1'b1;
        bresp_q   <= reg_werr ? fir_pkg::RESP_SLVERR : fir_pkg::RESP_OKAY;
      end else begin
        // Partial arrival: hold whichever half turned up.
        if (aw_hs) begin
          aw_full_q <= 1'b1;
          awaddr_q  <= s_axil_awaddr;
        end
        if (w_hs) begin
          w_full_q <= 1'b1;
          wdata_q  <= s_axil_wdata;
          wstrb_q  <= s_axil_wstrb;
        end
      end

      // Response retire. Cannot collide with the set above: reg_wen requires
      // bvalid_q == 0, and this arm requires bvalid_q == 1.
      if (bvalid_q && s_axil_bready) begin
        bvalid_q <= 1'b0;
      end
    end
  end

  assign s_axil_bvalid = bvalid_q;
  assign s_axil_bresp  = bresp_q;

  // ---------------------------------------------------------------------------
  // Read channel
  //
  // The register file presents rdata combinationally from raddr, so the read
  // data is captured on the same edge that accepts the address.
  // ---------------------------------------------------------------------------
  logic              rvalid_q;
  logic [DATA_W-1:0] rdata_q;
  logic [1:0]        rresp_q;

  assign s_axil_arready = !rvalid_q;

  logic ar_hs;
  assign ar_hs = s_axil_arvalid && s_axil_arready;

  assign reg_raddr = s_axil_araddr;
  assign reg_ren   = ar_hs;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rvalid_q <= 1'b0;
      rdata_q  <= '0;
      rresp_q  <= fir_pkg::RESP_OKAY;
    end else begin
      if (ar_hs) begin
        rvalid_q <= 1'b1;
        rdata_q  <= reg_rdata;
        rresp_q  <= reg_rerr ? fir_pkg::RESP_SLVERR : fir_pkg::RESP_OKAY;
      end else if (rvalid_q && s_axil_rready) begin
        rvalid_q <= 1'b0;
      end
    end
  end

  assign s_axil_rvalid = rvalid_q;
  assign s_axil_rdata  = rdata_q;
  assign s_axil_rresp  = rresp_q;

  // AWPROT/ARPROT are accepted and ignored: this IP implements no privilege or
  // security filtering. Named here so the unused input is deliberate, not an
  // oversight a reviewer has to guess about.
  logic unused_prot;
  assign unused_prot = &{1'b0, s_axil_awprot, s_axil_arprot};

endmodule : axil_slave

`endif
