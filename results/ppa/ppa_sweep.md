# axis_fir_top: area, timing and throughput versus MAC_UNITS (measured: Yosys 0.33 + OpenSTA, Sky130 HD tt_025C_1v80, pre-layout)

Build: NTAPS_MAX=64, DATA_W=COEF_W=16, SYMMETRIC_EN=1. Timing-driven ABC mapping. Boundary: inputs 0.35 ns after the edge, outputs 0.15 ns before the next edge (assumption). The asynchronous-reset network is excluded (see constraints/axis_fir_top.sdc). T_min = 10 ns - WNS, verified at +/-2 %.

| MAC_UNITS | II (cycles/sample) | cells | area (um^2) | flip-flops | T_min (ns) | F_max (MHz) | throughput (Msamples/s) | method check |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 32 | 18662 | 200006 | 3860 | 5.392 | 185.5 | 5.80 | verified (+0.107 / -0.108 ns) |
| 2 | 16 | 20743 | 222904 | 3964 | 5.460 | 183.2 | 11.45 | verified (+0.109 / -0.109 ns) |
| 4 | 8 | 25497 | 265551 | 4169 | 5.286 | 189.2 | 23.65 | verified (+0.105 / -0.106 ns) |
| 8 | 4 | 33370 | 344493 | 4578 | 5.198 | 192.4 | 48.10 | verified (+0.104 / -0.104 ns) |
| 16 | 2 | 49537 | 486684 | 5392 | 5.266 | 189.9 | 94.95 | verified (+0.105 / -0.106 ns) |
| 32 | 1 | 77787 | 760277 | 7016 | 5.405 | 185.0 | 185.01 | verified (+0.108 / -0.108 ns) |

Throughput = F_max / II, a derived figure: II = ceil(N_eff / MAC_UNITS) with N_eff = NTAPS_MAX/2 for the symmetric build (documented in docs/03_microarchitecture.md; the cocotb fold-sweep checks bit-exactness at every MAC_UNITS). Worst asynchronous-reset recovery slack at 10 ns, unbuffered reset net (informational, NOT a pass/fail figure): M=1: -32.033 ns, M=2: -35.147 ns, M=4: -41.285 ns, M=8: -53.53 ns, M=16: -77.899 ns, M=32: -126.522 ns
