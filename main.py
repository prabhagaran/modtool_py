"""
main.py
───────
ModTool – Professional Modbus Debugging Tool
Entry point.  Builds all panels, wires up callbacks, runs the DPG render loop.
"""
import dearpygui.dearpygui as dpg

from config.defaults import APP_TITLE, APP_WIDTH, APP_HEIGHT
from modbus.manager  import manager
from utils.logger    import logger
from utils           import gui_queue

import gui.connection_panel as conn_panel
import gui.command_panel    as cmd_panel
import gui.response_panel   as resp_panel
import gui.debug_panel      as dbg_panel
import gui.scanner_panel    as scanner_panel
import gui.listener_panel   as listener_panel


# ─────────────────────────────────────────────────────────────────────────────
#  Theme
# ─────────────────────────────────────────────────────────────────────────────

def _apply_theme() -> None:
    """Dark industrial theme inspired by SCADA / PLC HMI panels."""
    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            # ── Background ────────────────────────────────────────────────
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,          ( 18,  22,  30))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,           ( 13,  17,  24))
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg,           ( 22,  27,  38))
            # ── Frame / input ─────────────────────────────────────────────
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,           ( 28,  36,  50))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered,    ( 38,  50,  68))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive,     ( 48,  65,  90))
            # ── Title-bar ─────────────────────────────────────────────────
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg,           ( 10,  14,  22))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive,     ( 14,  20,  32))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgCollapsed,  ( 10,  14,  22))
            # ── Buttons ───────────────────────────────────────────────────
            dpg.add_theme_color(dpg.mvThemeCol_Button,            ( 28,  78, 148))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,     ( 38, 100, 175))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,      ( 50, 120, 200))
            # ── Text ──────────────────────────────────────────────────────
            dpg.add_theme_color(dpg.mvThemeCol_Text,              (210, 220, 230))
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled,      (100, 110, 125))
            # ── Headers ───────────────────────────────────────────────────
            dpg.add_theme_color(dpg.mvThemeCol_Header,            ( 30,  65, 120))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered,     ( 40,  85, 150))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive,      ( 50, 100, 170))
            # ── Scrollbar ─────────────────────────────────────────────────
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg,       (  8,  12,  18))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab,     ( 45,  65, 100))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered, (60, 85, 130))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabActive,  (75, 105, 160))
            # ── Separators / borders ──────────────────────────────────────
            dpg.add_theme_color(dpg.mvThemeCol_Separator,         ( 45,  65, 100))
            dpg.add_theme_color(dpg.mvThemeCol_SeparatorHovered,  ( 65,  90, 135))
            dpg.add_theme_color(dpg.mvThemeCol_Border,            ( 40,  58,  90))
            dpg.add_theme_color(dpg.mvThemeCol_BorderShadow,      (  0,   0,   0,   0))
            # ── Check / combo ─────────────────────────────────────────────
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark,         (100, 200, 255))
            # ── Rounding / spacing ────────────────────────────────────────
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding,      4)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding,        4)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,        3)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding,         3)
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding,        4)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding,       10, 10)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding,         6,  4)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,          8,  5)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize,       10)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarRounding,    4)
    dpg.bind_theme(global_theme)


# ─────────────────────────────────────────────────────────────────────────────
#  UI layout
# ─────────────────────────────────────────────────────────────────────────────

def _build_ui() -> None:
    with dpg.window(tag="main_window",
                    no_title_bar=True, no_move=True, no_resize=True,
                    no_scrollbar=True, pos=(0, 0),
                    width=APP_WIDTH, height=APP_HEIGHT):

        # ── Header bar ────────────────────────────────────────────────────
        with dpg.group(horizontal=True):
            dpg.add_text("◈  ModTool", color=(100, 200, 255))
            dpg.add_text("  Professional Modbus Debugger",
                         color=(170, 185, 200))
            dpg.add_spacer(width=20)
            dpg.add_text("RTU + TCP  |  pymodbus  |  v1.0",
                         color=(80, 100, 125))

        dpg.add_separator()
        dpg.add_spacer(height=5)

        # ── Tab bar ───────────────────────────────────────────────────────
        with dpg.tab_bar(tag="main_tabs"):

            # ── Tab: Modbus ───────────────────────────────────────────────
            with dpg.tab(label="  Modbus  ", tag="tab_modbus"):
                dpg.add_spacer(height=4)
                with dpg.group(horizontal=True):

                    # LEFT: Connection + Command
                    with dpg.group():
                        conn_panel.build()
                        dpg.add_spacer(height=6)
                        cmd_panel.build()

                    dpg.add_spacer(width=6)

                    # RIGHT: Response + Debug
                    with dpg.group():
                        resp_panel.build()
                        dpg.add_spacer(height=6)
                        dbg_panel.build()

            # ── Tab: Scanner ──────────────────────────────────────────────
            with dpg.tab(label="  Scanner  ", tag="tab_scanner"):
                dpg.add_spacer(height=4)
                scanner_panel.build()

            # ── Tab: Listener ─────────────────────────────────────────────
            with dpg.tab(label="  Listener  ", tag="tab_listener"):
                dpg.add_spacer(height=4)
                listener_panel.build()


# ─────────────────────────────────────────────────────────────────────────────
#  Application entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    dpg.create_context()

    # Wire manager → response panel (thread-safe set_value calls)
    manager.set_response_callback(resp_panel.update_response)

    # Start file logging session
    log_base = logger.start_file_session()
    logger.log_info(f"Session started – logs → {log_base}.*")

    # Build UI
    _apply_theme()
    _build_ui()

    # Viewport
    dpg.create_viewport(
        title=APP_TITLE, width=APP_WIDTH, height=APP_HEIGHT,
        resizable=True, min_width=1060, min_height=720,
    )
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("main_window", True)

    # ── Render loop ───────────────────────────────────────────────────────
    # We use the manual loop so we can drain the GUI queue each frame.
    while dpg.is_dearpygui_running():
        gui_queue.drain()          # execute deferred add_* calls safely
        dpg.render_dearpygui_frame()

    # ── Cleanup ───────────────────────────────────────────────────────────
    manager.disconnect()
    logger.stop_file_session()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
