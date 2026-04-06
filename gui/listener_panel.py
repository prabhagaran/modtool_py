"""
gui/listener_panel.py
─────────────────────
Modbus TCP passive listener panel.

Binds a TCP server socket and decodes every incoming Modbus frame live.

Three modes
───────────
  Silent   – log only, never reply
  Respond  – reply with a Modbus exception so the master does not time out
  Forward  – transparent proxy: forward to a real slave and show both sides

All DPG mutations happen on the main thread via gui_queue.
"""
import csv
import datetime
import os
import socket
import struct
import threading

import dearpygui.dearpygui as dpg

try:
    import serial
    _SERIAL_OK = True
except ImportError:
    _SERIAL_OK = False

from config.defaults import (
    HEADER_COLOR, OK_COLOR, ERR_COLOR, WARN_COLOR,
    DIM_COLOR, TX_COLOR, RX_COLOR,
    BAUDRATES, PARITIES, STOP_BITS, BYTE_SIZES,
    LOG_DIR,
)
from utils import gui_queue


# ── COM port enumeration ──────────────────────────────────────────────────────────

def _list_com_ports() -> list[str]:
    """Return sorted list of available serial port names."""
    try:
        import serial.tools.list_ports
        return sorted(p.device for p in serial.tools.list_ports.comports())
    except Exception:
        return []


# ── Module state ──────────────────────────────────────────────────────────────
_server_thread: threading.Thread | None = None
_server_sock:   socket.socket   | None = None
_listen_active = False
_row_counter   = 0
_MAX_ROWS      = 500
_log_csv_fh    = None
_log_txt_fh    = None
_log_csv_writer = None

_FC_NAMES = {
    1:  "Read Coils",
    2:  "Read Discrete Inputs",
    3:  "Read Holding Regs",
    4:  "Read Input Regs",
    5:  "Write Single Coil",
    6:  "Write Single Register",
    15: "Write Multiple Coils",
    16: "Write Multiple Registers",
}
_PARITY_MAP = {"N - None": "N", "E - Even": "E", "O - Odd": "O"}


# ── Shared helpers ───────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _decode_pdu(uid: int, pdu: bytes) -> str:
    if not pdu:
        return "empty PDU"
    fc   = pdu[0]
    name = _FC_NAMES.get(fc, f"FC{fc:02d}")
    if len(pdu) >= 5 and fc in (1, 2, 3, 4, 5, 6, 15, 16):
        addr = struct.unpack(">H", pdu[1:3])[0]
        qty  = struct.unpack(">H", pdu[3:5])[0]
        return f"{name}  unit={uid}  addr={addr}  qty={qty}"
    return f"{name}  unit={uid}"


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


# ── RTU CRC-16 helpers ────────────────────────────────────────────────────────

def _crc16(data: bytes) -> int:
    """Compute Modbus RTU CRC-16."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 0x0001 else crc >> 1
    return crc


def _check_crc(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    return _crc16(frame[:-2]) == struct.unpack("<H", frame[-2:])[0]


def _decode_rtu_frame(frame: bytes) -> str:
    crc_ok  = _check_crc(frame)
    uid     = frame[0]
    pdu     = frame[1:-2]
    desc    = _decode_pdu(uid, pdu)
    crc_str = "CRC OK" if crc_ok else (
        f"CRC FAIL calc={_crc16(frame[:-2]):04X} "
        f"recv={struct.unpack('<H', frame[-2:])[0]:04X}"
    )
    return f"{desc}  [{crc_str}]"


# ── Build ─────────────────────────────────────────────────────────────────────

def build() -> None:
    """Create the listener panel as a child_window in the current DPG context."""
    with dpg.child_window(tag="listener_panel", width=-1, height=-1, border=True):

        dpg.add_text("MODBUS LISTENER / SNIFFER", color=HEADER_COLOR, indent=4)
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # ── Transport selector ────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            dpg.add_text("Transport:")
            dpg.add_spacer(width=8)
            dpg.add_radio_button(
                tag="lst_transport", items=["TCP", "RTU"],
                default_value="TCP", horizontal=True,
                callback=_on_transport_change,
            )

        dpg.add_spacer(height=8)
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # ══ TCP SECTION ════════════════════════════════════════════════════
        with dpg.group(tag="lst_tcp_section", indent=0):

            with dpg.group(horizontal=True, indent=4):
                dpg.add_text("Bind Port:")
                dpg.add_spacer(width=4)
                dpg.add_input_int(tag="lst_port", default_value=5020,
                                  min_value=1, max_value=65535,
                                  min_clamped=True, max_clamped=True, width=100)
                dpg.add_spacer(width=20)
                dpg.add_text("Mode:")
                dpg.add_spacer(width=6)
                dpg.add_radio_button(
                    tag="lst_mode",
                    items=["Silent", "Respond", "Forward"],
                    default_value="Silent", horizontal=True,
                    callback=_on_tcp_mode_change,
                )

            dpg.add_spacer(height=6)

            with dpg.group(tag="lst_fwd_row", show=False, horizontal=True, indent=4):
                dpg.add_text("Forward to (host:port):")
                dpg.add_spacer(width=6)
                dpg.add_input_text(tag="lst_fwd_addr",
                                   default_value="192.168.1.1:502", width=220)

        # ══ RTU SECTION (hidden by default) ════════════════════════════════
        with dpg.group(tag="lst_rtu_section", show=False, indent=0):

            with dpg.group(horizontal=True, indent=4):
                dpg.add_text("COM Port:")
                dpg.add_spacer(width=4)
                _ports = _list_com_ports()
                dpg.add_combo(
                    tag="lst_rtu_com",
                    items=_ports,
                    default_value=_ports[0] if _ports else "",
                    width=110,
                )
                dpg.add_spacer(width=6)
                dpg.add_button(
                    label=" ↺ ",
                    callback=lambda: (
                        dpg.configure_item("lst_rtu_com",
                                           items=_list_com_ports())
                    ),
                    width=30,
                )
                dpg.add_spacer(width=16)
                dpg.add_text("Baudrate:")
                dpg.add_spacer(width=4)
                dpg.add_combo(tag="lst_rtu_baud", items=BAUDRATES,
                              default_value="9600", width=100)

            dpg.add_spacer(height=6)

            with dpg.group(horizontal=True, indent=4):
                dpg.add_text("Parity:")
                dpg.add_spacer(width=4)
                dpg.add_combo(tag="lst_rtu_parity", items=PARITIES,
                              default_value="N - None", width=110)
                dpg.add_spacer(width=16)
                dpg.add_text("Stop Bits:")
                dpg.add_spacer(width=4)
                dpg.add_combo(tag="lst_rtu_stopbits", items=STOP_BITS,
                              default_value="1", width=60)
                dpg.add_spacer(width=16)
                dpg.add_text("Byte Size:")
                dpg.add_spacer(width=4)
                dpg.add_combo(tag="lst_rtu_bytesize", items=BYTE_SIZES,
                              default_value="8", width=60)

            dpg.add_spacer(height=6)
            with dpg.group(indent=4):
                dpg.add_text(
                    "Opens port in read mode. Needs a USB-RS485 adapter on the bus.",
                    color=DIM_COLOR,
                )

        dpg.add_spacer(height=10)

        # ── Controls ──────────────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            dpg.add_button(tag="btn_lst_start", label="  ▶  START LISTENING  ",
                           callback=_on_start, width=190, height=32)
            dpg.add_spacer(width=8)
            dpg.add_button(tag="btn_lst_stop", label="  ■  STOP  ",
                           callback=_on_stop, width=100, height=32,
                           enabled=False)
            dpg.add_spacer(width=16)
            dpg.add_text("  ●  IDLE", tag="lst_status", color=DIM_COLOR)

        dpg.add_spacer(height=8)
        dpg.add_separator()
        dpg.add_spacer(height=4)

        # ── Capture log header ────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            dpg.add_text("CAPTURED FRAMES", color=HEADER_COLOR)
            dpg.add_spacer(width=16)
            dpg.add_button(label=" Clear ", callback=_on_clear, width=60)
            dpg.add_spacer(width=10)
            dpg.add_text("Auto-scroll:")
            dpg.add_checkbox(tag="lst_autoscroll", default_value=True)
            dpg.add_spacer(width=16)
            dpg.add_text("Log to File:")
            dpg.add_checkbox(tag="lst_log_to_file", default_value=False)
            dpg.add_spacer(width=8)
            dpg.add_text("", tag="lst_log_path", color=DIM_COLOR)

        dpg.add_separator()

        # Column headers
        with dpg.group(horizontal=True, indent=8):
            dpg.add_text(f"{'Time':<14}",   color=DIM_COLOR)
            dpg.add_text(f"{'Source':<22}", color=DIM_COLOR)
            dpg.add_text(f"{'Dir':<6}",     color=DIM_COLOR)
            dpg.add_text("Description / Raw Hex", color=DIM_COLOR)

        dpg.add_separator()

        with dpg.child_window(tag="lst_log_scroll", width=-1, height=-1, border=False):
            dpg.add_group(tag="lst_log_content")


# ── Callbacks ─────────────────────────────────────────────────────────────────

def _on_transport_change(sender, app_data, user_data) -> None:
    tcp = (app_data == "TCP")
    dpg.configure_item("lst_tcp_section", show=tcp)
    dpg.configure_item("lst_rtu_section", show=not tcp)


def _on_tcp_mode_change(sender, app_data, user_data) -> None:
    dpg.configure_item("lst_fwd_row", show=(app_data == "Forward"))


def _on_start(sender, app_data, user_data) -> None:
    global _listen_active
    if _listen_active:
        return
    if dpg.get_value("lst_log_to_file"):
        _open_log_file()
    transport = dpg.get_value("lst_transport")
    if transport == "RTU":
        _start_rtu()
    else:
        _start_tcp()


def _on_stop(sender, app_data, user_data) -> None:
    global _listen_active
    _listen_active = False
    _close_log_file()
    if _server_sock:
        try:
            _server_sock.close()
        except Exception:
            pass


def _open_log_file() -> None:
    global _log_csv_fh, _log_txt_fh, _log_csv_writer
    os.makedirs(LOG_DIR, exist_ok=True)
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(LOG_DIR, f"listener_{ts}")
    _log_csv_fh     = open(base + ".csv", "w", newline="", encoding="utf-8")
    _log_txt_fh     = open(base + ".txt", "w", encoding="utf-8")
    _log_csv_writer = csv.writer(_log_csv_fh)
    _log_csv_writer.writerow(["Timestamp", "Source", "Direction", "Message"])
    _log_txt_fh.write(f"Listener Log  {ts}\n")
    _log_txt_fh.write("=" * 70 + "\n")
    _log_txt_fh.write(f"{'Time':<14}  {'Source':<22}  {'Dir':<5}  Description\n")
    _log_txt_fh.write("-" * 70 + "\n")
    gui_queue.post(lambda b=os.path.basename(base + ".csv"):
                   (dpg.set_value("lst_log_path", f"  {b}"),
                    dpg.configure_item("lst_log_path", color=OK_COLOR)))


def _close_log_file() -> None:
    global _log_csv_fh, _log_txt_fh, _log_csv_writer
    if _log_csv_fh:
        try:
            _log_csv_fh.flush()
            _log_csv_fh.close()
        except Exception:
            pass
        _log_csv_fh = None
    if _log_txt_fh:
        try:
            _log_txt_fh.flush()
            _log_txt_fh.close()
        except Exception:
            pass
        _log_txt_fh = None
    _log_csv_writer = None
    gui_queue.post(lambda: (dpg.set_value("lst_log_path", ""),
                            dpg.configure_item("lst_log_path", color=DIM_COLOR)))


def _on_clear(sender, app_data, user_data) -> None:
    global _row_counter
    _row_counter = 0
    children = dpg.get_item_children("lst_log_content", slot=1)
    for c in (children or []):
        dpg.delete_item(c)


# ── TCP start + server loop ───────────────────────────────────────────────────

def _start_tcp() -> None:
    global _server_sock, _server_thread, _listen_active

    port    = dpg.get_value("lst_port")
    mode    = dpg.get_value("lst_mode")
    fwd_str = dpg.get_value("lst_fwd_addr").strip() if mode == "Forward" else ""

    fwd_addr = None
    if mode == "Forward" and fwd_str:
        parts = fwd_str.rsplit(":", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            dpg.set_value("lst_status", "  ✖  Bad forward address (use host:port)")
            dpg.configure_item("lst_status", color=ERR_COLOR)
            return
        fwd_addr = (parts[0], int(parts[1]))

    try:
        _server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _server_sock.bind(("0.0.0.0", port))
        _server_sock.listen(10)
        _server_sock.settimeout(1.0)
    except OSError as e:
        dpg.set_value("lst_status", f"  ✖  {e}")
        dpg.configure_item("lst_status", color=ERR_COLOR)
        try:
            _server_sock.close()
        except Exception:
            pass
        return

    _listen_active = True
    dpg.configure_item("btn_lst_start", enabled=False)
    dpg.configure_item("btn_lst_stop",  enabled=True)
    dpg.set_value("lst_status", f"  ●  TCP :{port}  [{mode}]")
    dpg.configure_item("lst_status", color=OK_COLOR)

    _server_thread = threading.Thread(
        target=_tcp_server_loop, args=(mode, fwd_addr),
        daemon=True, name="ModbusTCPListenerThread",
    )
    _server_thread.start()


def _tcp_server_loop(mode: str, fwd_addr) -> None:
    global _server_sock
    while _listen_active:
        try:
            conn, addr = _server_sock.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        peer = f"{addr[0]}:{addr[1]}"
        _log_event(peer, "CON", "connected", OK_COLOR)
        threading.Thread(
            target=_tcp_handle_client,
            args=(conn, peer, mode, fwd_addr),
            daemon=True, name=f"LstClient-{peer}",
        ).start()

    try:
        _server_sock.close()
    except Exception:
        pass
    _teardown_ui()


def _tcp_handle_client(conn: socket.socket, peer: str,
                       mode: str, fwd_addr) -> None:
    fwd_sock: socket.socket | None = None
    if fwd_addr:
        try:
            fwd_sock = socket.create_connection(fwd_addr, timeout=3.0)
        except OSError as e:
            _log_event(peer, "ERR", f"forward connect failed: {e}", ERR_COLOR)

    try:
        while _listen_active:
            hdr = _recv_exact(conn, 6)
            if hdr is None:
                break
            tid, pid, length = struct.unpack(">HHH", hdr)
            if pid != 0 or length < 1 or length > 260:
                _log_event(peer, "ERR",
                           f"invalid MBAP (pid={pid} len={length})", ERR_COLOR)
                break

            body = _recv_exact(conn, length)
            if body is None:
                break

            uid     = body[0]
            pdu     = body[1:]
            raw     = hdr + body
            raw_hex = " ".join(f"{b:02X}" for b in raw)
            desc    = _decode_pdu(uid, pdu)

            _log_event(peer, "RX →", desc, TX_COLOR)
            _log_event(peer, "    ", f"RAW: {raw_hex}", DIM_COLOR)

            if fwd_sock:
                try:
                    fwd_sock.sendall(raw)
                    r_hdr = _recv_exact(fwd_sock, 6)
                    if r_hdr:
                        _, _, r_len = struct.unpack(">HHH", r_hdr)
                        r_body    = _recv_exact(fwd_sock, r_len) or b""
                        reply     = r_hdr + r_body
                        reply_hex = " ".join(f"{b:02X}" for b in reply)
                        _log_event(peer, "← TX", f"slave reply: {reply_hex}", RX_COLOR)
                        conn.sendall(reply)
                except OSError as e:
                    _log_event(peer, "ERR", f"forward error: {e}", ERR_COLOR)

            elif mode == "Respond":
                fc      = pdu[0] if pdu else 0
                exc_pdu = bytes([uid, fc | 0x80, 0x01])
                reply   = struct.pack(">HHH", tid, 0, len(exc_pdu)) + exc_pdu
                try:
                    conn.sendall(reply)
                    _log_event(peer, "← TX", "sent error reply (Respond mode)", DIM_COLOR)
                except OSError:
                    break

    except Exception:
        pass
    finally:
        conn.close()
        if fwd_sock:
            fwd_sock.close()
        _log_event(peer, "DIS", "disconnected", WARN_COLOR)


# ── RTU start + sniffer loop ──────────────────────────────────────────────────

def _start_rtu() -> None:
    global _server_thread, _listen_active

    if not _SERIAL_OK:
        dpg.set_value("lst_status",
                      "  ✖  pyserial not installed  (pip install pyserial)")
        dpg.configure_item("lst_status", color=ERR_COLOR)
        return

    com      = dpg.get_value("lst_rtu_com")
    baudrate = int(dpg.get_value("lst_rtu_baud"))
    parity   = _PARITY_MAP.get(dpg.get_value("lst_rtu_parity"), "N")
    stopbits = float(dpg.get_value("lst_rtu_stopbits"))
    bytesize = int(dpg.get_value("lst_rtu_bytesize"))

    # Inter-frame silence: 3.5 character times
    bits_per_char = 1 + bytesize + (1 if parity != "N" else 0) + stopbits
    interframe_s  = max(0.002, 3.5 * bits_per_char / baudrate)

    _listen_active = True
    dpg.configure_item("btn_lst_start", enabled=False)
    dpg.configure_item("btn_lst_stop",  enabled=True)
    dpg.set_value("lst_status", f"  ●  RTU  {com}  {baudrate} baud")
    dpg.configure_item("lst_status", color=OK_COLOR)

    _server_thread = threading.Thread(
        target=_rtu_sniff_loop,
        args=(com, baudrate, parity, stopbits, bytesize, interframe_s),
        daemon=True, name="ModbusRTUSniffThread",
    )
    _server_thread.start()


def _rtu_sniff_loop(com: str, baudrate: int, parity: str,
                    stopbits: float, bytesize: int,
                    interframe_s: float) -> None:
    try:
        ser = serial.Serial(
            port=com, baudrate=baudrate, parity=parity,
            stopbits=stopbits, bytesize=bytesize,
            timeout=interframe_s,
        )
    except serial.SerialException as e:
        msg = str(e)
        gui_queue.post(lambda m=msg: dpg.set_value("lst_status", f"  ✖  {m}"))
        gui_queue.post(lambda: dpg.configure_item("lst_status", color=ERR_COLOR))
        _teardown_ui()
        return

    _log_event(com, "SYS",
               f"Opened {com} @ {baudrate} {parity}{bytesize}{stopbits}",
               OK_COLOR)

    buf = bytearray()
    try:
        while _listen_active:
            chunk = ser.read(256)        # returns after timeout if no bytes
            if chunk:
                buf += chunk
            elif buf:
                # Silence detected — treat accumulated bytes as one frame
                frame   = bytes(buf)
                buf.clear()
                raw_hex = " ".join(f"{b:02X}" for b in frame)
                if len(frame) >= 4:
                    desc      = _decode_rtu_frame(frame)
                    crc_color = OK_COLOR if "CRC OK" in desc else WARN_COLOR
                    _log_event(com, "RX", desc, crc_color)
                    _log_event(com, "   ", f"RAW: {raw_hex}", DIM_COLOR)
                else:
                    _log_event(com, "RX",
                               f"short frame: {raw_hex}", WARN_COLOR)
    except Exception as e:
        _log_event(com, "ERR", str(e), ERR_COLOR)
    finally:
        try:
            ser.close()
        except Exception:
            pass
        _log_event(com, "SYS", f"Closed {com}", DIM_COLOR)
        _teardown_ui()


# ── Row helpers ───────────────────────────────────────────────────────────────

def _log_event(source: str, direction: str,
               message: str, color: tuple) -> None:
    """Called from any thread — posts the row creation to the main thread."""
    ts = _ts()
    gui_queue.post(
        lambda t=ts, s=source, d=direction, m=message, c=color:
            _add_row(t, s, d, m, c)
    )


def _add_row(ts: str, source: str, direction: str,
             message: str, color: tuple) -> None:
    """Must be called on the main (DPG) thread only."""
    global _row_counter
    _row_counter += 1
    tag = f"lst_row_{_row_counter}"

    children = dpg.get_item_children("lst_log_content", slot=1)
    if children and len(children) >= _MAX_ROWS:
        dpg.delete_item(children[0])

    with dpg.group(horizontal=True, tag=tag,
                   parent="lst_log_content", indent=8):
        dpg.add_text(f"{ts:<14}",       color=(160, 170, 180))
        dpg.add_text(f"{source:<22}",   color=(160, 170, 180))
        dpg.add_text(f"{direction:<6}", color=color)
        dpg.add_text(message,           color=color)

    if dpg.get_value("lst_autoscroll"):
        dpg.set_y_scroll("lst_log_scroll",
                         dpg.get_y_scroll_max("lst_log_scroll"))

    if _log_csv_writer and _log_txt_fh:
        _log_csv_writer.writerow([ts, source, direction, message])
        _log_csv_fh.flush()
        _log_txt_fh.write(f"{ts:<14}  {source:<22}  {direction:<5}  {message}\n")
        _log_txt_fh.flush()


def _teardown_ui() -> None:
    _close_log_file()
    gui_queue.post(lambda: dpg.configure_item("btn_lst_start", enabled=True))
    gui_queue.post(lambda: dpg.configure_item("btn_lst_stop",  enabled=False))
    gui_queue.post(lambda: dpg.set_value("lst_status", "  ●  IDLE"))
    gui_queue.post(lambda: dpg.configure_item("lst_status", color=DIM_COLOR))
