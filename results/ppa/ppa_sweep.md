# axis_fir_top: area, timing and throughput versus MAC_UNITS (measured: Yosys 0.33 + OpenSTA, Sky130 HD tt_025C_1v80, pre-layout)

Build: NTAPS_MAX=64, DATA_W=COEF_W=16, SYMMETRIC_EN=1. Timing-driven ABC mapping. Boundary: inputs 0.35 ns after the edge, outputs 0.15 ns before the next edge (assumption). The asynchronous-reset network is excluded (see constraints/axis_fir_top.sdc). T_min = 10 ns - WNS, verified at +/-2 %.

| MAC_UNITS | II (cycles/sample) | cells | area (um^2) | flip-flops | T_min (ns) | F_max (MHz) | throughput (Msamples/s) | method check |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 32 | 18409 | 198141 | 3860 | 5.336 | 187.4 | 5.86 | verified (+0.107 / -0.106 ns) |
| 2 | 16 | 20681 | 222861 | 3964 | 5.416 | 184.6 | 11.54 | verified (+0.108 / -0.108 ns) |
| 4 | 8 | 25490 | 266065 | 4169 | 5.283 | 189.3 | 23.66 | verified (+0.106 / -0.105 ns) |
| 8 | 4 | 33306 | 344663 | 4578 | 5.310 | 188.3 | 47.08 | verified (+0.106 / -0.107 ns) |
| 16 | 2 | 49338 | 487556 | 5392 | 5.278 | 189.5 | 94.73 | verified (+0.106 / -0.105 ns) |
| 32 | 1 | 77886 | 762919 | 7016 | 5.322 | 187.9 | 187.90 | verified (+0.106 / -0.107 ns) |

Throughput = F_max / II, a derived figure: II = ceil(N_eff / MAC_UNITS) with N_eff = NTAPS_MAX/2 for the symmetric build (documented in docs/03_microarchitecture.md; the cocotb fold-sweep checks bit-exactness at every MAC_UNITS). Worst asynchronous-reset recovery slack at 10 ns, unbuffered reset net (informational, NOT a pass/fail figure): M=1: -32.033 ns, M=2: -35.147 ns, M=4: -41.285 ns, M=8: -53.53 ns, M=16: -77.899 ns, M=32: -126.522 ns
