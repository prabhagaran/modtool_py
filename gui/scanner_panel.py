"""
gui/scanner_panel.py
────────────────────
Modbus network scanner panel — TCP and RTU.

TCP  – probes a subnet, IP range, or single host using a raw FC03 request
       in parallel across a thread pool.
RTU  – probes unit IDs on a serial port (sequentially) using pymodbus.

All DPG mutations happen on the main thread via gui_queue.
"""
import concurrent.futures
import csv
import datetime
import ipaddress
import os
import socket
import struct
import threading

import dearpygui.dearpygui as dpg
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException, ConnectionException

from config.defaults import (
    HEADER_COLOR, OK_COLOR, ERR_COLOR, DIM_COLOR, WARN_COLOR,
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
_scan_thread: threading.Thread | None = None
_scan_active = False
_row_counter = 0
_scan_results: list[dict] = []

_FC_NAMES = {
    1: "Read Coils",        2: "Read Discrete Inputs",
    3: "Read Holding Regs", 4: "Read Input Regs",
    5: "Write Single Coil", 6: "Write Single Register",
    15: "Write Multiple Coils", 16: "Write Multiple Registers",
}
_PARITY_MAP = {"N - None": "N", "E - Even": "E", "O - Odd": "O"}


# ── TCP raw FC03 probe ────────────────────────────────────────────────────────

def _build_tcp_request(unit_id: int) -> bytes:
    """Build a Modbus TCP FC03 frame: read 1 holding register at address 0."""
    return struct.pack(">HHHBBHH",
        0x0001, 0x0000, 0x0006, unit_id, 0x03, 0x0000, 0x0001,
    )


def _probe_tcp(host: str, port: int, unit_id: int, timeout: float) -> tuple:
    """Returns (host, port, unit_id, ok: bool, detail: str)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((host, port))
            s.sendall(_build_tcp_request(unit_id))
            data = s.recv(256)

        if len(data) < 8:
            return host, port, unit_id, False, "response too short"

        _, pid, _ = struct.unpack(">HHH", data[:6])
        if pid != 0:
            return host, port, unit_id, False, f"invalid protocol ID {pid}"

        uid_resp = data[6]
        fc = data[7]

        if fc == 0x03 and len(data) >= 10:
            byte_count = data[8]
            regs = []
            for i in range(0, byte_count, 2):
                idx = 9 + i
                if idx + 1 <= len(data):
                    regs.append(struct.unpack(">H", data[idx:idx + 2])[0])
            return host, port, uid_resp, True, f"FC03 OK  regs={regs}"

        if fc & 0x80:
            exc = data[8] if len(data) > 8 else 0
            exc_msg = {
                0x01: "Illegal Function", 0x02: "Illegal Data Address",
                0x03: "Illegal Data Value", 0x04: "Device Failure",
            }.get(exc, f"0x{exc:02X}")
            return host, port, uid_resp, True, f"Exception: {exc_msg}  (device is live)"

        raw = " ".join(f"{b:02X}" for b in data)
        return host, port, uid_resp, True, f"unknown response  raw={raw}"

    except ConnectionRefusedError:
        return host, port, unit_id, False, ""
    except (socket.timeout, TimeoutError):
        return host, port, unit_id, False, ""
    except OSError as e:
        return host, port, unit_id, False, str(e) if str(e) else ""


# ── RTU probe (pymodbus) ──────────────────────────────────────────────────────

def _probe_rtu(client: ModbusSerialClient, com: str, uid: int) -> tuple:
    """Returns (com, uid, ok: bool, detail: str)."""
    try:
        try:
            rr = client.read_holding_registers(0, 1, slave=uid)
        except TypeError:
            rr = client.read_holding_registers(0, 1, unit=uid)

        if rr is None:
            return com, uid, False, ""
        if hasattr(rr, "isError") and rr.isError():
            exc = getattr(rr, "exception_code", None)
            if exc:
                exc_msg = {
                    0x01: "Illegal Function", 0x02: "Illegal Data Address",
                    0x03: "Illegal Data Value", 0x04: "Device Failure",
                }.get(exc, f"0x{exc:02X}")
                return com, uid, True, f"Exception: {exc_msg}  (device is live)"
            return com, uid, False, ""
        if hasattr(rr, "registers"):
            return com, uid, True, f"FC03 OK  regs={list(rr.registers)}"
        return com, uid, False, ""
    except (ConnectionException, ModbusException) as e:
        return com, uid, False, str(e)
    except Exception as e:
        return com, uid, False, str(e)


# ── Build ─────────────────────────────────────────────────────────────────────

def build() -> None:
    """Create the scanner panel as a child_window in the current DPG context."""
    with dpg.child_window(tag="scanner_panel", width=-1, height=-1, border=True):

        dpg.add_text("MODBUS SCANNER", color=HEADER_COLOR, indent=4)
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # ── Transport selector ────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            dpg.add_text("Transport:")
            dpg.add_spacer(width=8)
            dpg.add_radio_button(
                tag="scan_transport", items=["TCP", "RTU"],
                default_value="TCP", horizontal=True,
                callback=_on_transport_change,
            )

        dpg.add_spacer(height=8)
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # ══ TCP SECTION ════════════════════════════════════════════════════
        with dpg.group(tag="scan_tcp_section", indent=0):

            with dpg.group(horizontal=True, indent=4):
                dpg.add_text("Scan Target:")
                dpg.add_spacer(width=8)
                dpg.add_radio_button(
                    tag="scan_tcp_target", items=["Subnet", "Range", "Host"],
                    default_value="Subnet", horizontal=True,
                    callback=_on_tcp_target_change,
                )

            dpg.add_spacer(height=8)

            with dpg.group(tag="scan_subnet_row", horizontal=True, indent=4):
                dpg.add_text("Subnet (CIDR):")
                dpg.add_spacer(width=6)
                dpg.add_input_text(tag="scan_subnet",
                                   default_value="192.168.1.0/24", width=220)

            with dpg.group(tag="scan_range_row", show=False, indent=4):
                with dpg.group(horizontal=True):
                    dpg.add_text("Start IP:")
                    dpg.add_spacer(width=4)
                    dpg.add_input_text(tag="scan_range_start",
                                       default_value="192.168.1.1", width=160)
                    dpg.add_spacer(width=14)
                    dpg.add_text("End IP:")
                    dpg.add_spacer(width=4)
                    dpg.add_input_text(tag="scan_range_end",
                                       default_value="192.168.1.254", width=160)

            with dpg.group(tag="scan_host_row", show=False, indent=4):
                with dpg.group(horizontal=True):
                    dpg.add_text("Host:")
                    dpg.add_spacer(width=4)
                    dpg.add_input_text(tag="scan_host",
                                       default_value="192.168.1.1", width=160)
                    dpg.add_spacer(width=14)
                    dpg.add_text("Unit ID range:")
                    dpg.add_spacer(width=4)
                    dpg.add_input_int(tag="scan_uid_start", default_value=1,
                                      min_value=1, max_value=247,
                                      min_clamped=True, max_clamped=True, width=70)
                    dpg.add_text(" – ")
                    dpg.add_input_int(tag="scan_uid_end", default_value=247,
                                      min_value=1, max_value=247,
                                      min_clamped=True, max_clamped=True, width=70)

            dpg.add_spacer(height=8)

            with dpg.group(horizontal=True, indent=4):
                dpg.add_text("Port:")
                dpg.add_spacer(width=4)
                dpg.add_input_int(tag="scan_port", default_value=502,
                                  min_value=1, max_value=65535,
                                  min_clamped=True, max_clamped=True, width=80)
                dpg.add_spacer(width=20)
                dpg.add_text("Timeout (s):")
                dpg.add_spacer(width=4)
                dpg.add_input_float(tag="scan_timeout", default_value=0.5,
                                    min_value=0.1, max_value=30.0,
                                    min_clamped=True, max_clamped=True,
                                    format="%.1f", step=0.1, width=80)
                dpg.add_spacer(width=20)
                dpg.add_text("Workers:")
                dpg.add_spacer(width=4)
                dpg.add_input_int(tag="scan_workers", default_value=64,
                                  min_value=1, max_value=512,
                                  min_clamped=True, max_clamped=True, width=80)

        # ══ RTU SECTION (hidden by default) ════════════════════════════════
        with dpg.group(tag="scan_rtu_section", show=False, indent=0):

            with dpg.group(horizontal=True, indent=4):
                dpg.add_text("COM Port:")
                dpg.add_spacer(width=4)
                _ports = _list_com_ports()
                dpg.add_combo(
                    tag="scan_rtu_com",
                    items=_ports,
                    default_value=_ports[0] if _ports else "",
                    width=110,
                )
                dpg.add_spacer(width=6)
                dpg.add_button(
                    label=" ↺ ",
                    callback=lambda: (
                        dpg.configure_item("scan_rtu_com",
                                           items=_list_com_ports())
                    ),
                    width=30,
                )
                dpg.add_spacer(width=16)
                dpg.add_text("Baudrate:")
                dpg.add_spacer(width=4)
                dpg.add_combo(tag="scan_rtu_baud", items=BAUDRATES,
                              default_value="9600", width=100)

            dpg.add_spacer(height=6)

            with dpg.group(horizontal=True, indent=4):
                dpg.add_text("Parity:")
                dpg.add_spacer(width=4)
                dpg.add_combo(tag="scan_rtu_parity", items=PARITIES,
                              default_value="N - None", width=110)
                dpg.add_spacer(width=16)
                dpg.add_text("Stop Bits:")
                dpg.add_spacer(width=4)
                dpg.add_combo(tag="scan_rtu_stopbits", items=STOP_BITS,
                              default_value="1", width=60)
                dpg.add_spacer(width=16)
                dpg.add_text("Byte Size:")
                dpg.add_spacer(width=4)
                dpg.add_combo(tag="scan_rtu_bytesize", items=BYTE_SIZES,
                              default_value="8", width=60)

            dpg.add_spacer(height=8)

            with dpg.group(horizontal=True, indent=4):
                dpg.add_text("Unit ID range:")
                dpg.add_spacer(width=4)
                dpg.add_input_int(tag="scan_rtu_uid_start", default_value=1,
                                  min_value=1, max_value=247,
                                  min_clamped=True, max_clamped=True, width=70)
                dpg.add_text(" – ")
                dpg.add_input_int(tag="scan_rtu_uid_end", default_value=247,
                                  min_value=1, max_value=247,
                                  min_clamped=True, max_clamped=True, width=70)
                dpg.add_spacer(width=24)
                dpg.add_text("Timeout / unit (s):")
                dpg.add_spacer(width=4)
                dpg.add_input_float(tag="scan_rtu_timeout", default_value=0.5,
                                    min_value=0.05, max_value=10.0,
                                    min_clamped=True, max_clamped=True,
                                    format="%.2f", step=0.05, width=80)

        dpg.add_spacer(height=10)

        # ── Controls ──────────────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            dpg.add_button(tag="btn_scan_start", label="  ▶  START SCAN  ",
                           callback=_on_start, width=160, height=32)
            dpg.add_spacer(width=8)
            dpg.add_button(tag="btn_scan_stop", label="  ■  STOP  ",
                           callback=_on_stop, width=100, height=32, enabled=False)
            dpg.add_spacer(width=20)
            dpg.add_text("", tag="scan_progress", color=DIM_COLOR)

        dpg.add_spacer(height=8)
        dpg.add_separator()
        dpg.add_spacer(height=4)

        # ── Results area ──────────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            dpg.add_text("RESULTS", color=HEADER_COLOR)
            dpg.add_spacer(width=16)
            dpg.add_text("", tag="scan_summary", color=OK_COLOR)
            dpg.add_spacer(width=16)
            dpg.add_button(label=" Save Log ", callback=_on_save_scan_log, width=82)
            dpg.add_spacer(width=6)
            dpg.add_button(label=" Clear ", callback=_on_clear, width=60)
            dpg.add_spacer(width=10)
            dpg.add_text("", tag="scan_log_status", color=DIM_COLOR)

        dpg.add_separator()

        with dpg.group(horizontal=True, indent=8):
            dpg.add_text(f"{'Host / Port':<26}", color=DIM_COLOR)
            dpg.add_text(f"{'Unit':<8}",          color=DIM_COLOR)
            dpg.add_text("Detail",                 color=DIM_COLOR)

        dpg.add_separator()

        with dpg.child_window(tag="scan_results_scroll", width=-1, height=-1, border=False):
            dpg.add_group(tag="scan_results_content")


# ── Callbacks ─────────────────────────────────────────────────────────────────

def _on_transport_change(sender, app_data, user_data) -> None:
    tcp = (app_data == "TCP")
    dpg.configure_item("scan_tcp_section", show=tcp)
    dpg.configure_item("scan_rtu_section", show=not tcp)


def _on_tcp_target_change(sender, app_data, user_data) -> None:
    m = app_data
    dpg.configure_item("scan_subnet_row", show=(m == "Subnet"))
    dpg.configure_item("scan_range_row",  show=(m == "Range"))
    dpg.configure_item("scan_host_row",   show=(m == "Host"))


def _on_start(sender, app_data, user_data) -> None:
    global _scan_thread, _scan_active
    if _scan_active:
        return
    _scan_active = True
    dpg.configure_item("btn_scan_start", enabled=False)
    dpg.configure_item("btn_scan_stop",  enabled=True)
    dpg.set_value("scan_progress", "  Scanning…")
    dpg.set_value("scan_summary", "")
    transport = dpg.get_value("scan_transport")
    target = _run_rtu_scan if transport == "RTU" else _run_tcp_scan
    _scan_thread = threading.Thread(
        target=target, daemon=True, name="ModbusScanThread"
    )
    _scan_thread.start()


def _on_stop(sender, app_data, user_data) -> None:
    global _scan_active
    _scan_active = False
    gui_queue.post(lambda: dpg.set_value("scan_progress", "  Stopped by user."))


def _on_clear(sender, app_data, user_data) -> None:
    global _row_counter
    _row_counter = 0
    _scan_results.clear()
    children = dpg.get_item_children("scan_results_content", slot=1)
    for c in (children or []):
        dpg.delete_item(c)
    dpg.set_value("scan_summary", "")
    dpg.set_value("scan_progress", "")
    dpg.set_value("scan_log_status", "")


def _on_save_scan_log(sender, app_data, user_data) -> None:
    if not _scan_results:
        dpg.set_value("scan_log_status", "  No results to save")
        dpg.configure_item("scan_log_status", color=WARN_COLOR)
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(LOG_DIR, f"scan_{ts}")
    csv_path = base + ".csv"
    txt_path = base + ".txt"
    with open(csv_path, "w", newline="", encoding="utf-8") as cf, \
         open(txt_path, "w", encoding="utf-8") as tf:
        writer = csv.writer(cf)
        writer.writerow(["Host / Port", "Unit ID", "Detail"])
        tf.write(f"Scan Log  {ts}\n")
        tf.write("=" * 60 + "\n")
        tf.write(f"{'Host / Port':<26}  {'Unit ID':<8}  Detail\n")
        tf.write("-" * 60 + "\n")
        for row in _scan_results:
            writer.writerow([row["host"], row["unit"], row["detail"]])
            tf.write(f"{row['host']:<26}  {row['unit']:<8}  {row['detail']}\n")
    short = os.path.basename(csv_path)
    dpg.set_value("scan_log_status", f"  Saved: {short}")
    dpg.configure_item("scan_log_status", color=OK_COLOR)


# ── TCP scan worker ───────────────────────────────────────────────────────────

def _run_tcp_scan() -> None:
    global _scan_active, _row_counter

    target  = dpg.get_value("scan_tcp_target")
    port    = dpg.get_value("scan_port")
    timeout = dpg.get_value("scan_timeout")
    workers = dpg.get_value("scan_workers")
    tasks: list[tuple] = []

    try:
        if target == "Subnet":
            net   = ipaddress.ip_network(
                        dpg.get_value("scan_subnet").strip(), strict=False)
            tasks = [(str(ip), port, 1, timeout) for ip in net.hosts()]

        elif target == "Range":
            s_ip = int(ipaddress.ip_address(
                        dpg.get_value("scan_range_start").strip()))
            e_ip = int(ipaddress.ip_address(
                        dpg.get_value("scan_range_end").strip()))
            tasks = [(str(ipaddress.ip_address(i)), port, 1, timeout)
                     for i in range(s_ip, e_ip + 1)]

        else:  # Host
            host  = dpg.get_value("scan_host").strip()
            uid_s = dpg.get_value("scan_uid_start")
            uid_e = dpg.get_value("scan_uid_end")
            tasks = [(host, port, uid, timeout) for uid in range(uid_s, uid_e + 1)]

    except ValueError as exc:
        msg = str(exc)
        gui_queue.post(lambda m=msg: dpg.set_value("scan_progress", f"  Error: {m}"))
        _finish_scan()
        return

    total        = len(tasks)
    found        = 0
    done         = 0
    report_every = max(1, total // 20)
    gui_queue.post(lambda n=total: dpg.set_value("scan_progress", f"  0 / {n}  …"))

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_probe_tcp, *t): t for t in tasks}
        for f in concurrent.futures.as_completed(futures):
            if not _scan_active:
                ex.shutdown(wait=False, cancel_futures=True)
                break

            host_r, port_r, uid_r, ok, detail = f.result()
            done += 1

            if ok:
                found += 1
                _row_counter += 1
                rc = _row_counter
                h, p, u, d = host_r, port_r, uid_r, detail
                gui_queue.post(
                    lambda rh=h, rp=p, ru=u, rd=d, rt=rc:
                        _add_result_row(f"{rh}:{rp}", ru, rd, rt)
                )

            if done % report_every == 0:
                fd, dn, tot = found, done, total
                gui_queue.post(
                    lambda f2=fd, d2=dn, t2=tot:
                        dpg.set_value("scan_progress",
                                      f"  {d2} / {t2}  ({f2} found)")
                )

    _post_summary(found, done, total)


# ── RTU scan worker ───────────────────────────────────────────────────────────

def _run_rtu_scan() -> None:
    global _scan_active, _row_counter

    com      = dpg.get_value("scan_rtu_com")
    baudrate = int(dpg.get_value("scan_rtu_baud"))
    parity   = _PARITY_MAP.get(dpg.get_value("scan_rtu_parity"), "N")
    stopbits = float(dpg.get_value("scan_rtu_stopbits"))
    bytesize = int(dpg.get_value("scan_rtu_bytesize"))
    timeout  = dpg.get_value("scan_rtu_timeout")
    uid_s    = dpg.get_value("scan_rtu_uid_start")
    uid_e    = dpg.get_value("scan_rtu_uid_end")

    total = uid_e - uid_s + 1
    gui_queue.post(lambda n=total: dpg.set_value("scan_progress", f"  0 / {n}  …"))

    client = ModbusSerialClient(
        port=com, baudrate=baudrate, parity=parity,
        stopbits=stopbits, bytesize=bytesize,
        timeout=float(timeout), retries=0,
    )
    if not client.connect():
        msg = f"Cannot open {com}"
        gui_queue.post(lambda m=msg: dpg.set_value("scan_progress", f"  Error: {m}"))
        _finish_scan()
        return

    found = 0
    done  = 0
    try:
        for uid in range(uid_s, uid_e + 1):
            if not _scan_active:
                break
            c, u, ok, detail = _probe_rtu(client, com, uid)
            done += 1
            if ok:
                found += 1
                _row_counter += 1
                rc = _row_counter
                lbl, uu, dd = c, u, detail
                gui_queue.post(
                    lambda lb=lbl, ru=uu, rd=dd, rt=rc:
                        _add_result_row(lb, ru, rd, rt)
                )
            fd, dn, tot = found, done, total
            gui_queue.post(
                lambda f2=fd, d2=dn, t2=tot:
                    dpg.set_value("scan_progress",
                                  f"  {d2} / {t2}  ({f2} found)")
            )
    finally:
        try:
            client.close()
        except Exception:
            pass

    _post_summary(found, done, total)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _post_summary(found: int, done: int, total: int) -> None:
    gui_queue.post(
        lambda f2=found, d2=done, t2=total:
            dpg.set_value("scan_progress",
                          f"  Done.  {d2} probed / {f2} found")
    )
    gui_queue.post(
        lambda f2=found: dpg.set_value(
            "scan_summary",
            f"  {f2} device(s) found" if f2 else "  No devices found"
        )
    )
    if not found:
        gui_queue.post(lambda: dpg.configure_item("scan_summary", color=WARN_COLOR))
    else:
        gui_queue.post(lambda: dpg.configure_item("scan_summary", color=OK_COLOR))
    _finish_scan()


def _add_result_row(host_label: str, unit_id: int,
                    detail: str, counter: int) -> None:
    color = OK_COLOR if "OK" in detail else WARN_COLOR
    tag = f"scan_row_{counter}"
    with dpg.group(horizontal=True, tag=tag,
                   parent="scan_results_content", indent=8):
        dpg.add_text(f"{host_label:<26}", color=color)
        dpg.add_text(f"{unit_id:<8}",     color=color)
        dpg.add_text(detail,              color=color)
    _scan_results.append({"host": host_label, "unit": unit_id, "detail": detail})


def _finish_scan() -> None:
    global _scan_active
    _scan_active = False
    gui_queue.post(lambda: dpg.configure_item("btn_scan_start", enabled=True))
    gui_queue.post(lambda: dpg.configure_item("btn_scan_stop",  enabled=False))
