# ─────────────────────────────────────────────────────────────────────────────
#  ModTool – Application-wide defaults and constants
# ─────────────────────────────────────────────────────────────────────────────

APP_TITLE   = "ModTool  ·  Professional Modbus Debugger"
APP_WIDTH   = 1400
APP_HEIGHT  = 900

# ── RTU defaults ─────────────────────────────────────────────────────────────
DEFAULT_COM_PORT = "COM1"
DEFAULT_BAUDRATE = "9600"
DEFAULT_PARITY   = "N - None"
DEFAULT_STOPBITS = "1"
DEFAULT_BYTESIZE = "8"
DEFAULT_SLAVE_ID = "1"

# ── TCP defaults ─────────────────────────────────────────────────────────────
DEFAULT_IP      = "192.168.1.1"
DEFAULT_TCP_PORT = "502"
DEFAULT_UNIT_ID  = "1"

# ── Drop-down options ─────────────────────────────────────────────────────────
BAUDRATES   = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"]
PARITIES    = ["N - None", "E - Even", "O - Odd"]
STOP_BITS   = ["1", "1.5", "2"]
BYTE_SIZES  = ["7", "8"]

POLL_INTERVALS = ["100", "250", "500", "1000", "2000", "5000"]

FUNCTION_CODES = [
    "01  –  Read Coils",
    "02  –  Read Discrete Inputs",
    "03  –  Read Holding Registers",
    "04  –  Read Input Registers",
    "05  –  Write Single Coil",
    "06  –  Write Single Register",
    "15  –  Write Multiple Coils",
    "16  –  Write Multiple Registers",
]

DATA_TYPES = ["UINT16", "INT16", "FLOAT32", "HEX"]

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR = "logs"

# ── UI layout ────────────────────────────────────────────────────────────────
LEFT_COL_W     = 440   # px – left panel column width
CONN_PANEL_H   = 315   # px – connection panel height
RESP_PANEL_H   = 195   # px – response panel height
HEADER_COLOR   = (100, 200, 255, 255)
OK_COLOR       = (60, 220, 100, 255)
ERR_COLOR      = (230, 80,  80, 255)
WARN_COLOR     = (255, 200, 50, 255)
DIM_COLOR      = (140, 150, 160, 255)
TX_COLOR       = (100, 180, 255, 255)
RX_COLOR       = (100, 255, 155, 255)
INFO_COLOR     = (190, 200, 210, 255)
