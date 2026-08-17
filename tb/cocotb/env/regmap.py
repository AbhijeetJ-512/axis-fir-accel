"""Python mirror of rtl/common/fir_pkg.sv.

This file is the single source of register-map truth on the testbench side.
It must be kept in step with fir_pkg.sv by hand; test_csr.test_id_and_capability
is the guard that catches drift, because it recomputes CAPABILITY from these
constants and compares against what the RTL actually reports.
"""

# --- Identification ----------------------------------------------------------
IP_ID = 0x46495230  # "FIR0"

# --- CSR byte offsets --------------------------------------------------------
REG_ID = 0x000
REG_CAPABILITY = 0x004
REG_CTRL = 0x008
REG_CFG = 0x00C
REG_STATUS = 0x010
REG_IRQ_STATUS = 0x014
REG_IRQ_ENABLE = 0x018
REG_SAMPLE_CNT = 0x01C
REG_FRAME_CNT = 0x020

CSR_ALL = [
    REG_ID, REG_CAPABILITY, REG_CTRL, REG_CFG, REG_STATUS,
    REG_IRQ_STATUS, REG_IRQ_ENABLE, REG_SAMPLE_CNT, REG_FRAME_CNT,
]
CSR_RO = [REG_ID, REG_CAPABILITY, REG_STATUS, REG_SAMPLE_CNT, REG_FRAME_CNT]
CSR_RW = [REG_CTRL, REG_CFG, REG_IRQ_STATUS, REG_IRQ_ENABLE]

# --- CTRL fields -------------------------------------------------------------
CTRL_ENABLE_BIT = 0
CTRL_SOFTRST_BIT = 1
CTRL_COMMIT_BIT = 2
CTRL_FLUSH_BIT = 3
CTRL_CNTCLR_BIT = 4

# --- CFG fields --------------------------------------------------------------
CFG_NTAPS_LSB, CFG_NTAPS_W = 0, 9
CFG_ROUND_LSB, CFG_ROUND_W = 16, 2
CFG_SAT_BIT = 20
CFG_SYM_BIT = 24

CFG_NTAPS_MASK = ((1 << CFG_NTAPS_W) - 1) << CFG_NTAPS_LSB
CFG_ROUND_MASK = ((1 << CFG_ROUND_W) - 1) << CFG_ROUND_LSB

# --- STATUS bits -------------------------------------------------------------
STS_IDLE_BIT = 0
STS_BUSY_BIT = 1
STS_COEFPEND_BIT = 2
STS_CFGERR_BIT = 3
STS_SATSTKY_BIT = 4
STS_INEMPTY_BIT = 5
STS_OUTFULL_BIT = 6
STS_DRAIN_BIT = 7

# --- IRQ bits ----------------------------------------------------------------
IRQ_FRAME_DONE_BIT = 0
IRQ_SAT_EVENT_BIT = 1
IRQ_CFG_ERR_BIT = 2
IRQ_W = 3
IRQ_MASK = (1 << IRQ_W) - 1

# --- Rounding modes ----------------------------------------------------------
RND_TRUNC = 0
RND_HALF_UP = 1
RND_CONVERGENT = 2
RND_RESERVED = 3
RND_LEGAL = (RND_TRUNC, RND_HALF_UP, RND_CONVERGENT)

# --- Saturation modes --------------------------------------------------------
SAT_ASYMMETRIC = 0
SAT_SYMMETRIC = 1

# --- Coefficient region ------------------------------------------------------
COEF_BASE = 0x400
COEF_LIMIT = 0x7FF
COEF_IDX_LSB = 2
COEF_IDX_W = 8


def coef_addr(index: int) -> int:
    """Byte address of coefficient `index`."""
    return COEF_BASE + (index << COEF_IDX_LSB)


# --- AXI responses -----------------------------------------------------------
RESP_OKAY = 0b00
RESP_SLVERR = 0b10


# --- Derivations (must mirror fir_pkg::acc_width) ----------------------------
def clog2(n: int) -> int:
    return (n - 1).bit_length()


def acc_width(data_w: int, coef_w: int, ntaps_max: int) -> int:
    return data_w + coef_w + clog2(ntaps_max)


def expected_capability(ntaps_max: int, mac_units: int, data_w: int,
                        coef_w: int, symmetric_en: int) -> int:
    """Recompute CAPABILITY exactly as csr_regfile.sv composes it."""
    acc_w = acc_width(data_w, coef_w, ntaps_max)
    return (
        (clog2(ntaps_max) & 0xF)
        | ((clog2(mac_units) & 0xF) << 4)
        | ((data_w & 0xFF) << 8)
        | ((coef_w & 0xFF) << 16)
        | ((acc_w & 0x7F) << 24)
        | ((symmetric_en & 1) << 31)
    )


def make_cfg(ntaps: int, round_mode: int = RND_TRUNC,
             sat_mode: int = SAT_ASYMMETRIC, symmetric: int = 0) -> int:
    """Compose a CFG word from fields."""
    return (
        ((ntaps & ((1 << CFG_NTAPS_W) - 1)) << CFG_NTAPS_LSB)
        | ((round_mode & ((1 << CFG_ROUND_W) - 1)) << CFG_ROUND_LSB)
        | ((sat_mode & 1) << CFG_SAT_BIT)
        | ((symmetric & 1) << CFG_SYM_BIT)
    )
