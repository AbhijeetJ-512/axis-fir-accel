# Explorer validation

`docs/architecture_explorer.html` runs a JavaScript model of `axis_fir_top`. These files check that model against the RTL.

```bash
docs/explorer/validate/build_rtl.sh . docs/explorer/validate/rtlbuild     # Icarus Verilog 12: one replay build per MAC_UNITS (1, 2, 4)
python3 docs/explorer/validate/validate.py docs/explorer/sim.js docs/explorer/validate/rtlbuild
python3 docs/explorer/validate/mutate.py   docs/explorer/sim.js docs/explorer/validate/rtlbuild   # about a minute
```

`run_model.js` runs `sim.js` headless (it uses `gjs`) and writes the per-cycle stimulus and the outputs the model expects. `tb_replay.sv`
drives `axis_fir_top` (NTAPS_MAX = 16, input FIFO 4, output FIFO 8) with that stimulus and prints 23 signals every cycle; `validate.py`
diffs the two. `mutate.py` puts 45 one-line bugs into the model, one at a time, and checks that the replay notices each.

Measured with this commit: 48 runs (10 scenarios x MAC_UNITS 1, 2, 4, and all three rounding modes for the scenarios that depend on it),
3,554 cycles, no difference. Mutation check: 44 of 45 caught; the survivor ("read-only registers writable") is an equivalent change, because
the register `case` has no read-only entries. Not covered: `reset_sync`, X propagation, other parameter values, the bound checkers.

Generated here, not committed: `rtlbuild/` and `run/` (see `.gitignore`).
