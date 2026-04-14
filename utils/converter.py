"""
utils/converter.py
──────────────────
Low-level data-type conversion helpers for Modbus register values.
All functions are pure (no side-effects, no imports from this project).
"""
import struct


# ── CRC-16 (Modbus RTU) ────────────────────────────────────────────────────

def _crc16_modbus(data: bytes) -> int:
    """Compute the Modbus RTU CRC-16 checksum."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 0x0001 else crc >> 1
    return crc


# ── Register → host type ──────────────────────────────────────────────────────

def registers_to_uint16(registers: list) -> list:
    """Return raw register values as unsigned 16-bit integers (no-op)."""
    return [int(r) & 0xFFFF for r in registers]


def registers_to_int16(registers: list) -> list:
    """Interpret raw register values as signed 16-bit integers."""
    result = []
    for r in registers:
        r = int(r) & 0xFFFF
        result.append(r - 65536 if r > 32767 else r)
    return result


def registers_to_float32(registers: list) -> list:
    """
    Convert pairs of consecutive registers to IEEE 754 single-precision floats.
    Big-endian word order (high word first).
    Odd trailing register is treated as UINT16.
    """
    result = []
    for i in range(0, len(registers) - 1, 2):
        raw = (int(registers[i]) << 16) | int(registers[i + 1])
        packed = struct.pack(">I", raw)
        result.append(struct.unpack(">f", packed)[0])
    if len(registers) % 2:
        result.append(float(registers[-1]))
    return result


def registers_to_hex(registers: list) -> list:
    """Format register values as '0x????'-style hex strings."""
    return [f"0x{int(r) & 0xFFFF:04X}" for r in registers]


def coils_to_int_list(coils) -> list:
    """Convert an iterable of bool/int coil values to a 0/1 list."""
    return [1 if c else 0 for c in coils]


# ── Host type → register(s) ──────────────────────────────────────────────────

def value_to_registers(value, data_type: str = "UINT16") -> list:
    """
    Convert a single scalar *value* into one or two Modbus register words,
    according to *data_type* ("UINT16" | "INT16" | "FLOAT32" | "HEX").
    """
    if data_type == "FLOAT32":
        packed = struct.pack(">f", float(value))
        hi, lo = struct.unpack(">HH", packed)
        return [hi, lo]
    # INT16 / UINT16 / HEX all map to a single 16-bit word
    return [int(value, 16) & 0xFFFF if isinstance(value, str) and value.lower().startswith("0x")
            else int(value) & 0xFFFF]


# ── Wire-frame helpers ────────────────────────────────────────────────────────

def build_rtu_tx_bytes(slave_id: int, fc: int, address: int,
                       count: int = None, values: list = None) -> bytes:
    """
    Build the RTU PDU+ADU bytes (slave | fc | data | CRC).
    The real CRC-16 is computed and appended so the debug log matches
    the exact bytes pymodbus puts on the wire.
    """
    buf = bytearray([slave_id & 0xFF, fc & 0xFF,
                     (address >> 8) & 0xFF, address & 0xFF])
    if values is None:
        # Read command or FC05/06 single write
        if count is not None:
            buf += bytearray([(count >> 8) & 0xFF, count & 0xFF])
    else:
        if fc == 5:
            # FC05 Write Single Coil: Modbus ON=0xFF00, OFF=0x0000
            v = 0xFF00 if (values[0] if isinstance(values[0], bool) else bool(int(values[0]))) else 0x0000
            buf += bytearray([(v >> 8) & 0xFF, v & 0xFF])
        else:
            for v in values:
                v = int(v) & 0xFFFF
                buf += bytearray([(v >> 8) & 0xFF, v & 0xFF])
    crc = _crc16_modbus(bytes(buf))
    buf += bytearray([crc & 0xFF, (crc >> 8) & 0xFF])
    return bytes(buf)


def build_tcp_tx_bytes(unit_id: int, fc: int, address: int,
                       count: int = None, values: list = None,
                       transaction_id: int = 0) -> bytes:
    """Build the full Modbus TCP MBAP+PDU frame."""
    pdu = bytearray([fc & 0xFF,
                     (address >> 8) & 0xFF, address & 0xFF])
    if values is None:
        if count is not None:
            pdu += bytearray([(count >> 8) & 0xFF, count & 0xFF])
    else:
        if fc == 5:
            # FC05 Write Single Coil: Modbus ON=0xFF00, OFF=0x0000
            v = 0xFF00 if (values[0] if isinstance(values[0], bool) else bool(int(values[0]))) else 0x0000
            pdu += bytearray([(v >> 8) & 0xFF, v & 0xFF])
        else:
            for v in values:
                v = int(v) & 0xFFFF
                pdu += bytearray([(v >> 8) & 0xFF, v & 0xFF])
    length = len(pdu) + 1          # +1 for unit_id
    mbap = bytearray([
        (transaction_id >> 8) & 0xFF, transaction_id & 0xFF,
        0x00, 0x00,                   # protocol id
        (length >> 8) & 0xFF, length & 0xFF,
        unit_id & 0xFF,
    ])
    return bytes(mbap + pdu)


def bytes_to_hex_str(data: bytes) -> str:
    """Format a bytes/bytearray object as a spaced uppercase hex string."""
    return " ".join(f"{b:02X}" for b in data)
