// fir_binds.sv - attach the checkers in fir_checkers.sv to the RTL. Signal names in the RTL are a contract with the checkers (see the
// port lists in fir_checkers.sv). `.*` connects by name; internal signals and widened copies are connected explicitly.
bind axil_slave    fir_axil_chk   #(.DATA_W(DATA_W)) u_chk (.*);
bind axis_fifo     fir_fifo_chk   #(.DATA_W(DATA_W), .DEPTH(DEPTH)) u_chk (.*);
bind credit_ctr    fir_credit_chk #(.MAX_CREDITS(MAX_CREDITS)) u_chk (.*);
bind fir_ctrl      fir_ctrl_chk   #(.MAC_UNITS(MAC_UNITS)) u_chk (.*, .tap_base(32'(tap_base)), .state(state), .busy(busy), .sample_start(sample_start),
                                                                     .last_pass(last_pass), .pcnt(32'(pcnt)), .p_last(32'(p_last)), .dp_count(32'(dp_count)));
bind coeff_bank    fir_coef_chk   #(.W(NTAPS_MAX*COEF_W)) u_chk (.*, .bank_sel(bank_sel));
bind csr_regfile   fir_csr_chk    #(.ADDR_W(ADDR_W), .NTAPS_MAX(NTAPS_MAX)) u_chk (.*, .irq_status_q(irq_status_q), .irq_enable_q(irq_enable_q),
                                       .sat_sticky_q(sat_sticky_q), .cfg_err_sticky_q(cfg_err_sticky_q), .cmd_cnt_clr_q(cmd_cnt_clr_q),
                                       .sample_cnt_q(sample_cnt_q), .frame_cnt_q(frame_cnt_q));
bind delay_line    fir_delay_chk  #(.DATA_W(DATA_W), .NTAPS_MAX(NTAPS_MAX)) u_chk (.*);
bind fir_core      fir_core_chk   #(.DATA_W(DATA_W)) u_chk (.*);
bind axis_fir_top  fir_top_chk    #(.DATA_W(DATA_W), .OUT_FIFO_DEPTH(OUT_FIFO_DEPTH)) u_chk (
    .clk(aclk), .rst_n(rst_n), .s_axis_tvalid(s_axis_tvalid), .s_axis_tready(s_axis_tready),
    .m_axis_tlast(m_axis_tlast), .m_axis_tvalid(m_axis_tvalid), .m_axis_tready(m_axis_tready), .m_axis_tdata(m_axis_tdata), .irq(irq),
    .soft_clear(soft_clear), .consume(consume), .in_tlast_admit(in_tlast), .out_push(out_push), .out_pop(out_pop),
    .out_fifo_full(out_fifo_full), .sts_busy(sts_busy), .out_count(32'(out_count)), .credits(32'(u_credit.credits)), .dp_count(32'(u_ctrl.dp_count)),
    .cfg_symmetric(cfg_symmetric), .evt_frame_done(evt_frame_done), .evt_saturate(evt_saturate), .cfg_round_mode(cfg_round_mode),
    .cfg_sat_mode(cfg_sat_mode), .bank_sel(u_coef.bank_sel));
