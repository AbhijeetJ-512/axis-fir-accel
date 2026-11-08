// Replay testbench: drives axis_fir_top with the per-cycle stimulus file and prints the outputs the testbench would see just before each rising edge.
`timescale 1ns/1ps
module tb;
  parameter MAC = 4;
  reg clk = 0, aresetn = 1; always #5 clk = ~clk;
  reg [11:0] awaddr = 0, araddr = 0; reg awvalid = 0, wvalid = 0, bready = 0, arvalid = 0, rready = 0, tvalid = 0, tlast = 0, tready = 0;
  reg [31:0] wdata = 0; reg [3:0] wstrb = 0; reg [15:0] tdata = 0;
  wire awready, wready, bvalid, arready, rvalid, s_tready, m_tvalid, m_tlast, irq; wire [1:0] bresp, rresp; wire [31:0] rdata; wire [15:0] m_tdata;
  axis_fir_top #(.DATA_W(16), .COEF_W(16), .NTAPS_MAX(16), .MAC_UNITS(MAC), .IN_FIFO_DEPTH(4), .OUT_FIFO_DEPTH(8), .AXIL_ADDR_W(12), .SYMMETRIC_EN(1)) dut (
    .aclk(clk), .aresetn(aresetn),
    .s_axil_awaddr(awaddr), .s_axil_awprot(3'b0), .s_axil_awvalid(awvalid), .s_axil_awready(awready),
    .s_axil_wdata(wdata), .s_axil_wstrb(wstrb), .s_axil_wvalid(wvalid), .s_axil_wready(wready),
    .s_axil_bresp(bresp), .s_axil_bvalid(bvalid), .s_axil_bready(bready),
    .s_axil_araddr(araddr), .s_axil_arprot(3'b0), .s_axil_arvalid(arvalid), .s_axil_arready(arready),
    .s_axil_rdata(rdata), .s_axil_rresp(rresp), .s_axil_rvalid(rvalid), .s_axil_rready(rready),
    .s_axis_tdata(tdata), .s_axis_tlast(tlast), .s_axis_tvalid(tvalid), .s_axis_tready(s_tready),
    .m_axis_tdata(m_tdata), .m_axis_tlast(m_tlast), .m_axis_tvalid(m_tvalid), .m_axis_tready(tready), .irq(irq));
  integer fd, r;
  reg [31:0] a_awvalid, a_awaddr, a_wvalid, a_wdata, a_wstrb, a_bready, a_arvalid, a_araddr, a_rready, a_tvalid, a_tdata, a_tlast, a_tready;
  initial begin
    fd = $fopen("stim.txt", "r");
    #2 aresetn = 0;
    repeat (3) @(posedge clk);
    #1 aresetn = 1;
    repeat (3) @(posedge clk);
    while (!$feof(fd)) begin
      r = $fscanf(fd, "%h %h %h %h %h %h %h %h %h %h %h %h %h\n", a_awvalid, a_awaddr, a_wvalid, a_wdata, a_wstrb, a_bready, a_arvalid, a_araddr, a_rready, a_tvalid, a_tdata, a_tlast, a_tready);
      if (r == 13) begin
        #1;
        awvalid = a_awvalid; awaddr = a_awaddr; wvalid = a_wvalid; wdata = a_wdata; wstrb = a_wstrb; bready = a_bready; arvalid = a_arvalid; araddr = a_araddr;
        rready = a_rready; tvalid = a_tvalid; tdata = a_tdata; tlast = a_tlast; tready = a_tready;
        #8;
        $display("%0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h",
          awready, wready, bvalid, bresp, arready, rvalid, rdata, rresp, s_tready, m_tvalid, m_tvalid ? m_tdata : 16'h0, m_tvalid ? m_tlast : 1'b0, irq,
          dut.u_credit.credits_q, dut.u_ctrl.state, dut.u_ctrl.dp_count, dut.u_in_fifo.count, dut.u_out_fifo.count,
          dut.u_coef.bank_sel, dut.u_coef.pending_q, dut.u_csr.irq_status_q, dut.u_csr.sample_cnt_q, dut.u_csr.frame_cnt_q);
        @(posedge clk);
      end
    end
    $finish;
  end
endmodule
