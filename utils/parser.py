"""
utils/parser.py
───────────────
Parse pymodbus response objects into a uniform result dictionary and
build human-readable TX frame strings for the debug log.
"""
from utils.converter import (
    registers_to_uint16, registers_to_int16,
    registers_to_float32, registers_to_hex,
    coils_to_int_list,
    build_rtu_tx_bytes, build_tcp_tx_bytes, bytes_to_hex_str,
)

# ── Modbus standard exception codes ──────────────────────────────────────────
EXCEPTION_CODES = {
    0x01: "Illegal Function",
    0x02: "Illegal Data Address",
    0x03: "Illegal Data Value",
    0x04: "Server Device Failure",
    0x05: "Acknowledge",
    0x06: "Server Device Busy",
    0x08: "Memory Parity Error",
    0x0A: "Gateway Path Unavailable",
    0x0B: "Gateway Target Device Failed to Respond",
}


def parse_response(response, data_type: str = "UINT16") -> dict:
    """
    Convert a pymodbus response object into a plain dict:
        raw_hex  – space-separated hex of register/coil data
        parsed   – human-readable converted values as a string
        values   – Python list of converted values
        error    – error description if any
        is_error – True when the response represents a failure
    """
    result = dict(raw_hex="", parsed="", values=[], error="", is_error=False)

    # ── Null / timeout ───────────────────────────────────────────────────────
    if response is None:
        result.update(error="No response (timeout)", is_error=True)
        return result

    # ── Modbus exception frame ───────────────────────────────────────────────
    if hasattr(response, "isError") and response.isError():
        exc = getattr(response, "exception_code", None)
        if exc:
            msg = EXCEPTION_CODES.get(exc, f"Unknown (0x{exc:02X})")
            result["error"] = f"MB Exception 0x{exc:02X}: {msg}"
        else:
            result["error"] = str(response)
        result["is_error"] = True
        return result

    # ── Register responses (FC03, FC04) ─────────────────────────────────────
    if hasattr(response, "registers"):
        regs = list(response.registers)
        raw = " ".join(f"{r:04X}" for r in regs)
        result["raw_hex"] = raw
        if data_type == "INT16":
            vals = registers_to_int16(regs)
        elif data_type == "FLOAT32":
            vals = [round(v, 5) for v in registers_to_float32(regs)]
        elif data_type == "HEX":
            vals = registers_to_hex(regs)
        else:
            vals = registers_to_uint16(regs)
        result["parsed"] = str(vals)
        result["values"] = vals
        return result

    # ── Coil / discrete responses (FC01, FC02) ───────────────────────────────
    if hasattr(response, "bits"):
        bits = list(response.bits)
        coils = coils_to_int_list(bits)
        result["raw_hex"] = " ".join(str(b) for b in coils)
        result["parsed"] = str(coils)
        result["values"] = coils
        return result

    # ── Write acknowledgement (FC05, FC06, FC15, FC16) ───────────────────────
    result["raw_hex"] = "ACK"
    result["parsed"]  = "Write OK"
    return result


def format_tx_frame(mode: str, fc: int, slave_id: int,
                    address: int, count: int = None,
                    values: list = None) -> str:
    """
    Build a human-readable hex string representing the outgoing frame,
    using the realistic wire format for the chosen *mode* ("RTU"|"TCP").
    RTU appends **CRC placeholder** bytes (00 00).
    """
    if mode == "RTU":
        raw = build_rtu_tx_bytes(slave_id, fc, address, count, values)
    else:
        raw = build_tcp_tx_bytes(slave_id, fc, address, count, values)
    return bytes_to_hex_str(raw)
