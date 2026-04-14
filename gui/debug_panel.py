"""
gui/debug_panel.py
──────────────────
Scrollable, auto-updating debug log panel.

Architecture
────────────
• Logger callback fires from any worker thread and posts a lambda to
  ``gui_queue`` (never calls dpg.add_* directly from a worker thread).
• ``gui_queue.drain()`` is processed in the main render loop in main.py,
  guaranteeing all DPG item-creation calls happen on the main thread.

Display format
──────────────
  [HH:MM:SS.mmm]  TX   01 03 00 00 00 0A 00 00  →  FC03 Read Holding Regs …
  [HH:MM:SS.mmm]  RX   0064 00C8 012C            →  [100, 200, 300]
  [HH:MM:SS.mmm]  ERR  Connection lost: …
"""
import os
from datetime import datetime

import dearpygui.dearpygui as dpg

from config.defaults import HEADER_COLOR, TX_COLOR, RX_COLOR, ERR_COLOR, INFO_COLOR, LOG_DIR
from utils.logger    import logger
from utils import gui_queue

_MAX_ROWS   = 300    # max visible rows before the oldest are pruned
_row_tags: list[str] = []
_row_counter        = 0


# ── Public build function ─────────────────────────────────────────────────────

def build() -> None:
    """Create the debug panel as a child_window in the current DPG context."""
    with dpg.child_window(tag="debug_panel", width=-1,
                          height=-1, border=True):

        # ── Title + toolbar ───────────────────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            dpg.add_text("DEBUG LOG", color=HEADER_COLOR)
            dpg.add_spacer(width=20)
            dpg.add_button(label=" Clear ", callback=_on_clear,   width=60)
            dpg.add_spacer(width=4)
            dpg.add_button(label=" Save ",  callback=_on_save,    width=60)
            dpg.add_spacer(width=14)
            dpg.add_text("Auto-scroll:")
            dpg.add_checkbox(tag="dbg_autoscroll", default_value=True)

        dpg.add_separator()

        # ── Column headers (static text) ─────────────────────────────────
        with dpg.group(horizontal=True, indent=4):
            dpg.add_text("Time           ", color=(160, 170, 180, 255))
            dpg.add_text("Dir  ", color=(160, 170, 180, 255))
            dpg.add_text("Frame / Message", color=(160, 170, 180, 255))

        dpg.add_separator()

        # ── Scrollable log content ────────────────────────────────────────
        with dpg.child_window(tag="dbg_scroll", width=-1,
                              height=-1, border=False):
            dpg.add_group(tag="dbg_content")   # dynamic rows added here

    # Register logger callback AFTER the panel is built
    logger.set_gui_callback(_on_log_entry)


# ── Logger callback (called from any thread) ──────────────────────────────────

def _on_log_entry(entry: dict) -> None:
    """
    Posts a GUI-update lambda to gui_queue.
    Never touches DPG directly – this may be called from a worker thread.
    """
    # Capture entry by value with default arg binding
    gui_queue.post(lambda e=entry: _add_row(e))


# ── Row creation (main thread only, via gui_queue) ────────────────────────────

def _add_row(entry: dict) -> None:
    global _row_counter

    direction = entry["direction"]
    color = {
        "TX":  TX_COLOR,
        "RX":  RX_COLOR,
        "ERR": ERR_COLOR,
        "INF": INFO_COLOR,
    }.get(direction, INFO_COLOR)

    frame_str = entry["frame"]
    if entry.get("parsed"):
        frame_str += f"   →   {entry['parsed']}"

    line = f"[{entry['timestamp']}]  {direction:<3}  {frame_str}"

    row_tag = f"dbg_r{_row_counter}"
    _row_counter += 1
    _row_tags.append(row_tag)

    dpg.add_text(line, parent="dbg_content", tag=row_tag, color=color)

    # Prune oldest rows
    while len(_row_tags) > _MAX_ROWS:
        old = _row_tags.pop(0)
        try:
            dpg.delete_item(old)
        except Exception:
            pass

    # Auto-scroll
    try:
        if dpg.get_value("dbg_autoscroll"):
            dpg.set_y_scroll("dbg_scroll", -1.0)
    except Exception:
        pass


# ── Toolbar callbacks ─────────────────────────────────────────────────────────

def _on_clear(sender, app_data, user_data) -> None:
    global _row_tags, _row_counter
    for tag in _row_tags:
        try:
            dpg.delete_item(tag)
        except Exception:
            pass
    _row_tags.clear()
    logger.clear()


def _on_save(sender, app_data, user_data) -> None:
    entries = logger.get_entries()
    if not entries:
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(LOG_DIR, f"debug_export_{ts}.txt")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# ModTool Debug Export\n")
            fh.write("=" * 80 + "\n\n")
            for e in entries:
                fh.write(f"[{e['timestamp']}] [{e['direction']:<3}] {e['frame']}\n")
                if e.get("parsed"):
                    fh.write(f"               → {e['parsed']}\n")
        logger.log_info(f"Log saved → {path}")
    except Exception as exc:
        logger.log_error(f"Save failed: {exc}")
