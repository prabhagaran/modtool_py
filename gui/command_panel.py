"""
gui/command_panel.py
────────────────────
Builds the Command panel UI.

Features
--------
• Function-code dropdown (FC01–FC16)
• Address / Quantity / Value(s) inputs with dynamic show/hide
• Data-type selector  (UINT16 / INT16 / FLOAT32 / HEX)
• Auto-poll section   (enable checkbox + interval combo)
• SEND button         (disabled when not connected)
"""
import threading
import dearpygui.dearpygui as dpg

from config.defaults import (
    FUNCTION_CODES, DATA_TYPES, POLL_INTERVALS,
    LEFT_COL_W, HEADER_COLOR, ERR_COLOR,
)
from modbus.manager import manager
from utils.logger   import logger
from utils          import gui_queue


_LABEL_W = 130   # fixed label column width (px)


# ── Public build function ─────────────────────────────────────────────────────

def build() -> None:
    """Create the command panel as a child_window in the current DPG context."""
    with dpg.child_window(tag="cmd_panel", width=LEFT_COL_W,
                          height=-1, border=True):
        dpg.add_text("COMMAND", color=HEADER_COLOR)
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # ── Function code ─────────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            dpg.add_text("Function Code:", indent=0)
            dpg.add_combo(
                tag="cmd_fc", items=FUNCTION_CODES,
                default_value=FUNCTION_CODES[2],   # 03 Read Holding Regs
                width=210, callback=_on_fc_change,
            )

        dpg.add_spacer(height=4)

        # ── Address ───────────────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            dpg.add_text("Start Address:", indent=0)
            dpg.add_spacer(width=4)
            dpg.add_input_int(tag="cmd_address", default_value=0,
                              min_value=0, max_value=65535,
                              min_clamped=True, max_clamped=True, width=110)

        dpg.add_spacer(height=4)

        # ── Quantity (hidden for FC05/FC06 single-write) ──────────────────
        with dpg.group(tag="qty_row", horizontal=True, indent=4):
            dpg.add_text("Quantity:", indent=0)
            dpg.add_spacer(width=_LABEL_W - 65)
            dpg.add_input_int(tag="cmd_quantity", default_value=1,
                              min_value=1, max_value=125,
                              min_clamped=True, max_clamped=True, width=80)

        dpg.add_spacer(height=4)

        # ── Values (write ops only – hidden by default) ───────────────────
        with dpg.group(tag="val_row", show=False, indent=4):
            with dpg.group(horizontal=True):
                dpg.add_text("Value(s):", indent=0)
                dpg.add_spacer(width=_LABEL_W - 66)
                dpg.add_input_text(tag="cmd_values", default_value="0",
                                   hint="0   or   1,2,3", width=200)
            dpg.add_text("  (comma-separated for multi-write)",
                         color=(140, 150, 160, 255))

        dpg.add_spacer(height=4)

        # ── Data type ─────────────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            dpg.add_text("Data Type:", indent=0)
            dpg.add_spacer(width=_LABEL_W - 72)
            dpg.add_combo(tag="cmd_dtype", items=DATA_TYPES,
                          default_value="UINT16", width=110)

        dpg.add_separator()
        dpg.add_spacer(height=6)

        # ── Auto-poll ─────────────────────────────────────────────────────
        dpg.add_text("AUTO POLL", color=HEADER_COLOR, indent=4)
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True, indent=4):
            dpg.add_checkbox(tag="poll_enable", label=" Enable",
                             callback=_on_poll_toggle)
            dpg.add_spacer(width=14)
            dpg.add_text("Interval (ms):")
            dpg.add_combo(tag="poll_interval", items=POLL_INTERVALS,
                          default_value="1000", width=85)

        dpg.add_spacer(height=8)
        dpg.add_separator()
        dpg.add_spacer(height=8)

        # ── Send button ───────────────────────────────────────────────────
        dpg.add_button(tag="btn_send", label="  ▶  SEND COMMAND  ",
                       callback=_on_send, width=-1, height=38)

    # Register poll-stopped callback so the checkbox resets on connection drop
    manager.set_poll_stopped_callback(
        lambda: gui_queue.post(_reset_poll_ui)
    )


# ── Callbacks ─────────────────────────────────────────────────────────────────

def _on_fc_change(sender, app_data, user_data) -> None:
    fc = _parse_fc(app_data)
    is_write  = fc in (5, 6, 15, 16)
    is_single = fc in (5, 6)

    if is_write:
        dpg.show_item("val_row")
        if is_single:
            dpg.hide_item("qty_row")
        else:
            dpg.show_item("qty_row")
    else:
        dpg.hide_item("val_row")
        dpg.show_item("qty_row")


def _on_send(sender, app_data, user_data) -> None:
    if not manager.connected:
        manager.fire_error("Not connected – connect first.")
        logger.log_error("Send attempted while not connected")
        return
    threading.Thread(target=_do_send, daemon=True,
                     name="SendThread").start()


def _do_send() -> None:
    try:
        fc       = _parse_fc(dpg.get_value("cmd_fc"))
        address  = dpg.get_value("cmd_address")
        quantity = dpg.get_value("cmd_quantity")
        dtype    = dpg.get_value("cmd_dtype")
        values   = None

        if fc in (5, 6, 15, 16):
            raw    = dpg.get_value("cmd_values").strip()
            values = [_parse_scalar(v.strip()) for v in raw.split(",") if v.strip()]

        manager.execute(fc, address, count=quantity, values=values,
                        data_type=dtype)
    except ValueError as exc:
        logger.log_error(f"Input validation: {exc}")
        manager.fire_error(str(exc))
    except Exception as exc:
        logger.log_error(f"Send error: {exc}")


def _on_poll_toggle(sender, app_data, user_data) -> None:
    if app_data:   # checkbox turned ON
        if not manager.connected:
            dpg.set_value("poll_enable", False)
            logger.log_error("Polling enabled while not connected")
            return
        _start_poll()
    else:
        manager.stop_polling()
        logger.log_info("Auto-poll stopped")


def _start_poll() -> None:
    fc       = _parse_fc(dpg.get_value("cmd_fc"))
    address  = dpg.get_value("cmd_address")
    quantity = dpg.get_value("cmd_quantity")
    dtype    = dpg.get_value("cmd_dtype")
    interval = int(dpg.get_value("poll_interval"))
    manager.start_polling(fc, address, quantity,
                          manager.slave_id, interval, dtype)
    logger.log_info(f"Auto-poll started: FC{fc:02d} @ {address}, every {interval} ms")


# ── Utility ───────────────────────────────────────────────────────────────────
def _reset_poll_ui() -> None:
    """Called (on the main thread) when the poll loop exits due to a
    connection drop, so the Enable checkbox reflects the real state."""
    dpg.set_value("poll_enable", False)
    logger.log_info("Auto-poll stopped (connection lost)")

def _parse_fc(text: str) -> int:
    """Extract integer function code from e.g. '03  –  Read Holding Registers'."""
    return int(text.split()[0])


def _parse_scalar(s: str) -> int:
    """Parse a decimal or 0x-hex integer string."""
    s = s.strip()
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(s)
