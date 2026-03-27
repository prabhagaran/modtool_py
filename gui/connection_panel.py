"""
gui/connection_panel.py
───────────────────────
Builds the Connection panel UI and handles connect / disconnect callbacks.

Layout
------
  ┌─ CONNECTION ─────────────────────────────┐
  │  Mode:  ● TCP   ○ RTU                    │
  │  ── TCP fields ──                         │
  │    IP Address     [ 192.168.1.1        ]  │
  │    Port           [ 502 ]                 │
  │    Unit ID        [ 1   ]                 │
  │  ── RTU fields (hidden by default) ──     │
  │    COM Port  Baudrate  Parity …           │
  │  Status: ● DISCONNECTED                   │
  │  [ Connect ]  [ Disconnect ]              │
  └──────────────────────────────────────────┘
"""
import threading
import dearpygui.dearpygui as dpg

from config.defaults import (
    DEFAULT_COM_PORT, DEFAULT_BAUDRATE, DEFAULT_PARITY,
    DEFAULT_STOPBITS, DEFAULT_BYTESIZE, DEFAULT_SLAVE_ID,
    DEFAULT_IP, DEFAULT_TCP_PORT, DEFAULT_UNIT_ID,
    BAUDRATES, PARITIES, STOP_BITS, BYTE_SIZES,
    CONN_PANEL_H, LEFT_COL_W,
    OK_COLOR, ERR_COLOR, WARN_COLOR, HEADER_COLOR,
)
from modbus.manager import manager
from utils.logger   import logger

_LABEL_W = 100   # fixed label column width (px)
_INPUT_W = 175   # input/combo width (px)


# ── Public build function ─────────────────────────────────────────────────────

def build() -> None:
    """Create the connection panel as a child_window in the current DPG context."""
    with dpg.child_window(tag="conn_panel", width=LEFT_COL_W,
                          height=CONN_PANEL_H, border=True):
        # Title
        dpg.add_text("CONNECTION", color=HEADER_COLOR)
        dpg.add_separator()
        dpg.add_spacer(height=4)

        # ── Mode selection ────────────────────────────────────────────────
        with dpg.group(horizontal=True):
            dpg.add_text("Mode:", indent=4)
            dpg.add_radio_button(
                tag="mode_radio", items=["TCP", "RTU"],
                default_value="TCP", horizontal=True,
                callback=_on_mode_change,
            )

        dpg.add_spacer(height=6)

        # ── TCP fields ────────────────────────────────────────────────────
        with dpg.group(tag="tcp_fields", indent=4):
            _row("IP Address",  "tcp_ip",       DEFAULT_IP,       _INPUT_W)
            _row("Port",        "tcp_port_in",  DEFAULT_TCP_PORT,  80)
            _row("Unit ID",     "tcp_unit_id",  DEFAULT_UNIT_ID,   60)

        # ── RTU fields (hidden by default) ────────────────────────────────
        with dpg.group(tag="rtu_fields", show=False, indent=4):
            _row_combo("COM Port",  "rtu_com",      _com_ports(),    DEFAULT_COM_PORT, _INPUT_W)
            _row_combo("Baudrate",  "rtu_baud",     BAUDRATES,       DEFAULT_BAUDRATE,  110)
            _row_combo("Parity",    "rtu_parity",   PARITIES,        DEFAULT_PARITY,    _INPUT_W)
            _row_combo("Stop Bits", "rtu_stopbits", STOP_BITS,       DEFAULT_STOPBITS,  70)
            _row_combo("Byte Size", "rtu_bytesize", BYTE_SIZES,      DEFAULT_BYTESIZE,  60)
            _row("Slave ID", "rtu_slave_id", DEFAULT_SLAVE_ID, 60)

        dpg.add_spacer(height=6)
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # ── Status indicator ──────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            dpg.add_text("Status:")
            dpg.add_text("  ●  DISCONNECTED", tag="conn_status",
                         color=ERR_COLOR)

        dpg.add_spacer(height=8)

        # ── Buttons ───────────────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            dpg.add_button(tag="btn_connect",    label="  Connect  ",
                           callback=_on_connect,    width=110, height=30)
            dpg.add_spacer(width=6)
            dpg.add_button(tag="btn_disconnect", label=" Disconnect ",
                           callback=_on_disconnect, width=110, height=30,
                           enabled=False)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row(label: str, tag: str, default, width: int) -> None:
    with dpg.group(horizontal=True):
        dpg.add_text(f"{label}:", indent=0)
        dpg.add_spacer(width=max(1, _LABEL_W - len(label) * 7))
        dpg.add_input_text(tag=tag, default_value=str(default), width=width)


def _row_combo(label: str, tag: str, items: list, default, width: int) -> None:
    with dpg.group(horizontal=True):
        dpg.add_text(f"{label}:", indent=0)
        dpg.add_spacer(width=max(1, _LABEL_W - len(label) * 7))
        dpg.add_combo(tag=tag, items=items, default_value=str(default), width=width)


def _com_ports() -> list:
    try:
        import serial.tools.list_ports
        ports = [p.device for p in serial.tools.list_ports.comports()]
        return ports if ports else [DEFAULT_COM_PORT]
    except Exception:
        return [DEFAULT_COM_PORT, "COM2", "COM3", "COM4", "COM8",
                "/dev/ttyUSB0", "/dev/ttyS0"]


# ── Callbacks ─────────────────────────────────────────────────────────────────

def _on_mode_change(sender, app_data, user_data) -> None:
    mode = str(app_data)
    if mode == "TCP":
        dpg.show_item("tcp_fields")
        dpg.hide_item("rtu_fields")
    else:
        dpg.hide_item("tcp_fields")
        dpg.show_item("rtu_fields")
    manager.set_mode(mode)


def _on_connect(sender, app_data, user_data) -> None:
    mode = dpg.get_value("mode_radio")
    _set_status("  CONNECTING …", WARN_COLOR)
    dpg.configure_item("btn_connect",    enabled=False)
    dpg.configure_item("btn_disconnect", enabled=False)
    threading.Thread(target=_do_connect, args=(mode,),
                     daemon=True, name="ConnectThread").start()


def _do_connect(mode: str) -> None:
    success = False
    try:
        if mode == "TCP":
            host    = dpg.get_value("tcp_ip").strip()
            port    = int(dpg.get_value("tcp_port_in").strip() or 502)
            unit_id = int(dpg.get_value("tcp_unit_id").strip() or 1)
            success = manager.connect_tcp(host, port, unit_id)
        else:
            port     = dpg.get_value("rtu_com").strip()
            baud     = dpg.get_value("rtu_baud").strip()
            parity   = dpg.get_value("rtu_parity")
            stopbits = dpg.get_value("rtu_stopbits").strip()
            bytesize = dpg.get_value("rtu_bytesize").strip()
            slave_id = int(dpg.get_value("rtu_slave_id").strip() or 1)
            success  = manager.connect_rtu(port, baud, parity,
                                           stopbits, bytesize, slave_id)
    except Exception as exc:
        logger.log_error(f"Connect exception: {exc}")

    # dpg.configure_item / dpg.set_value are thread-safe
    if success:
        _set_status("  ●  CONNECTED", OK_COLOR)
        dpg.configure_item("btn_connect",    enabled=False)
        dpg.configure_item("btn_disconnect", enabled=True)
    else:
        _set_status("  ●  FAILED", ERR_COLOR)
        dpg.configure_item("btn_connect",    enabled=True)
        dpg.configure_item("btn_disconnect", enabled=False)


def _on_disconnect(sender, app_data, user_data) -> None:
    manager.disconnect()
    _set_status("  ●  DISCONNECTED", ERR_COLOR)
    dpg.configure_item("btn_connect",    enabled=True)
    dpg.configure_item("btn_disconnect", enabled=False)


def _set_status(text: str, color: tuple) -> None:
    dpg.set_value("conn_status", text)
    dpg.configure_item("conn_status", color=color)
