#!/usr/bin/env bash
# run_suite.sh - run one cocotb suite, show the interesting lines, and FAIL if the suite did not pass.
#
#   run_suite.sh <grep -E pattern for the lines to show> <make arguments for tb/cocotb ...>
#
# Why this exists (portfolio change, 2026-10-21): the original Makefile ran each suite as
#     make ... 2>&1 | grep -E "TESTS=|..." || true
# which can only ever exit 0. When the Phase 4 refactor stopped tb/sv/fir_core_wrap.sv from elaborating, `make check`
# kept printing "Phase 3 suite" with no result line and still returned success. A suite now fails the target when
# (a) the simulator build/run returned an error, (b) no "TESTS=" summary line was produced, or (c) FAIL is not 0.
set -u
show="$1"; shift
out="$(make -s -C "$@" 2>&1)"; rc=$?
printf '%s\n' "$out" | grep -E "$show"
summary="$(printf '%s\n' "$out" | grep -oE 'TESTS=[0-9]+ PASS=[0-9]+ FAIL=[0-9]+ SKIP=[0-9]+' | tail -1)"
if [ -z "$summary" ]; then echo "SUITE ERROR: no TESTS= summary line (simulator build or run failed); last lines:"; printf '%s\n' "$out" | tail -8; exit 1; fi
case "$summary" in *"FAIL=0 "*) ;; *) echo "SUITE FAILED: $summary"; exit 1;; esac
[ $rc -eq 0 ] || { echo "SUITE ERROR: simulator make returned $rc"; exit 1; }
exit 0
