// -----------------------------------------------------------------------------
// fir_defs.svh
//
// Width derivations, as preprocessor macros rather than package functions.
//
// WHY MACROS AND NOT FUNCTIONS:
//   Yosys 0.33's Verilog front end does not accept `function ... endfunction`
//   inside a `package` (it parses enums, typedefs and localparams there
//   without complaint). Icarus accepts them, so the problem is invisible until
//   synthesis is attempted. Rather than carry RTL that only one of our two
//   mandatory tools can read, the derivations live here as macros, which both
//   accept.
//
//   The alternative -- repeating `DATA_W + COEF_W + $clog2(NTAPS_MAX)` in every
//   module that needs it -- would put the project's single most important
//   numerical invariant in four places at once. One definition, referenced by
//   name, is worth the mild ugliness of a macro.
//
// Both derivations are verified by scripts/verify_acc_width.py.
// See docs/01_requirements.md NR-03/NR-04/NR-05.
// -----------------------------------------------------------------------------
`ifndef FIR_DEFS_SVH
`define FIR_DEFS_SVH

// Exact product width for a signed DW x CW multiply.
`define FIR_PROD_W(DW, CW) ((DW) + (CW))

// Accumulator width that makes overflow impossible for up to NMAX products.
// Proven sufficient and minimal for NMAX in 4..256 by verify_acc_width.py.
`define FIR_ACC_W(DW, CW, NMAX) ((DW) + (CW) + $clog2(NMAX))

// Fractional bits discarded when narrowing the accumulator back to the sample
// format: product has (DW-1)+(CW-1) fractional bits, output keeps (DW-1).
`define FIR_SHIFT(CW) ((CW) - 1)

`endif
