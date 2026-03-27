"""
gui/response_panel.py
─────────────────────
Builds the Response panel UI and exposes ``update_response(result)`` which is
called by the Modbus manager after every completed operation.

``dpg.set_value()`` and ``dpg.configure_item()`` are thread-safe in DPG 1.x,
so this module can be called directly from worker threads.
"""
import dearpygui.dearpygui as dpg

from config.defaults import (
    RESP_PANEL_H, HEADER_COLOR, OK_COLOR, ERR_COLOR, DIM_COLOR,
)


# ── Public build function ─────────────────────────────────────────────────────

def build() -> None:
    """Create the response panel as a child_window in the current DPG context."""
    with dpg.child_window(tag="resp_panel", width=-1,
                          height=RESP_PANEL_H, border=True):
        dpg.add_text("RESPONSE", color=HEADER_COLOR)
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # ── Raw Hex ───────────────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            _label("Raw (HEX) :", 90)
            dpg.add_input_text(tag="resp_hex", default_value="",
                               readonly=True, width=-1)

        dpg.add_spacer(height=5)

        # ── Parsed ────────────────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            _label("Parsed    :", 90)
            dpg.add_input_text(tag="resp_parsed", default_value="",
                               readonly=True, width=-1)

        dpg.add_spacer(height=6)
        dpg.add_separator()
        dpg.add_spacer(height=5)

        # ── Status ────────────────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            _label("Status    :", 90)
            dpg.add_text("  –– awaiting command ––",
                         tag="resp_status", color=DIM_COLOR)


def _label(text: str, width: int) -> None:
    dpg.add_text(text)
    dpg.add_spacer(width=max(1, width - len(text) * 7))


# ── Thread-safe update function ───────────────────────────────────────────────

def update_response(result: dict) -> None:
    """
    Update the response panel with the latest Modbus result.
    Safe to call from any thread (uses only dpg.set_value / configure_item).
    """
    if result["is_error"]:
        dpg.set_value("resp_hex",    "")
        dpg.set_value("resp_parsed", "")
        dpg.set_value("resp_status", f"  ✖  {result['error']}")
        dpg.configure_item("resp_status", color=ERR_COLOR)
    else:
        dpg.set_value("resp_hex",    result["raw_hex"])
        dpg.set_value("resp_parsed", result["parsed"])
        dpg.set_value("resp_status", "  ✔  OK")
        dpg.configure_item("resp_status", color=OK_COLOR)
