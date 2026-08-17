// Waveform dump module, elaborated only when the regression is invoked with
// WAVES=1. Kept separate so the default (fast) run has no dump overhead.
`timescale 1ns/1ps
module dump_wave;
  initial begin
    $dumpfile("dump.fst");
    $dumpvars(0, ctrl_plane_wrap);
  end
endmodule
