// Waveform dump module, elaborated only when the regression is invoked with
// WAVES=1. Kept separate so the default (fast) run has no dump overhead.
// $dumpvars(0) with no scope argument dumps everything, which keeps this file
// independent of which suite is being run.
`timescale 1ns/1ps
module dump_wave;
  initial begin
    $dumpfile("dump.fst");
    $dumpvars(0);
  end
endmodule
