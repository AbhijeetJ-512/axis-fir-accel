# Third-party files

The MIT license in `LICENSE` covers my own work in this repository. It does not cover the file below, which keeps its own license.

## `ppa/lib/sky130hd_tt.lib`

The Sky130 HD typical-corner liberty file that the synthesis and timing sweep (`make ppa-sweep`) reads. It is the standard-cell timing
data of the SkyWater SKY130 PDK, Copyright 2020 The SkyWater PDK Authors, licensed under the Apache License, Version 2.0
(https://www.apache.org/licenses/LICENSE-2.0). I took this copy from the `test/sky130hd/` directory of the OpenROAD project
(https://github.com/The-OpenROAD-Project/OpenROAD, BSD 3-Clause License, Copyright (c) 2018-2025, The OpenROAD Authors) and didn't change it.

## Tools that are used but not included

Icarus Verilog, Verilator, cocotb, Yosys and OpenSTA are run by the scripts here, but none of their code is in this repository.
