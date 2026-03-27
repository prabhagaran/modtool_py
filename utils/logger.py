"""
utils/logger.py
───────────────
Thread-safe in-memory + file logger for Modbus TX/RX frames and
application events.  Exposes a module-level singleton: ``logger``.
"""
import os
import csv
import threading
from datetime import datetime

from config.defaults import LOG_DIR


class ModbusLogger:
    """
    Thread-safe logger that:
      • keeps an in-memory ring buffer of entries
      • writes to CSV + TXT files for the current session
      • notifies a registered GUI callback for live log display
    """

    MAX_MEMORY = 2000   # ring-buffer cap

    def __init__(self):
        self._lock          = threading.Lock()
        self._entries: list = []
        self._csv_fh        = None
        self._txt_fh        = None
        self._csv_writer    = None
        self._file_active   = False
        self._gui_callback  = None   # called with each new entry dict
        self._session_ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._tx_counter    = 0

    # ── Callback registration ─────────────────────────────────────────────────

    def set_gui_callback(self, fn) -> None:
        """Register *fn(entry: dict)* to be called on every new log entry."""
        self._gui_callback = fn

    # ── Session management ────────────────────────────────────────────────────

    def start_file_session(self) -> str:
        """Open CSV + TXT log files.  Returns the base file path (no extension)."""
        os.makedirs(LOG_DIR, exist_ok=True)
        base = os.path.join(LOG_DIR, f"modbus_{self._session_ts}")
        self._csv_fh    = open(f"{base}.csv", "w", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv_fh)
        self._csv_writer.writerow(["Timestamp", "Direction", "Frame", "Parsed", "Status"])
        self._txt_fh    = open(f"{base}.txt", "w", encoding="utf-8")
        self._txt_fh.write(f"# ModTool Session Log – {self._session_ts}\n")
        self._txt_fh.write("=" * 80 + "\n\n")
        self._file_active = True
        return base

    def stop_file_session(self) -> None:
        self._file_active = False
        for fh in (self._csv_fh, self._txt_fh):
            if fh:
                try:
                    fh.close()
                except Exception:
                    pass
        self._csv_fh = self._txt_fh = self._csv_writer = None

    # ── Public log API ────────────────────────────────────────────────────────

    def log_tx(self, frame: str, description: str = "") -> None:
        self._record("TX", frame, description)

    def log_rx(self, frame: str, description: str = "") -> None:
        self._record("RX", frame, description)

    def log_error(self, message: str) -> None:
        self._record("ERR", message, "")

    def log_info(self, message: str) -> None:
        self._record("INF", message, "")

    # ── In-memory access ──────────────────────────────────────────────────────

    def get_entries(self) -> list:
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _record(self, direction: str, frame: str, parsed: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        entry = {"timestamp": ts, "direction": direction,
                 "frame": frame, "parsed": parsed}

        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self.MAX_MEMORY:
                self._entries.pop(0)

            if self._file_active:
                status = "ERROR" if direction == "ERR" else "OK"
                try:
                    self._csv_writer.writerow([ts, direction, frame, parsed, status])
                    self._csv_fh.flush()
                    self._txt_fh.write(f"[{ts}] [{direction:<3}] {frame}\n")
                    if parsed:
                        self._txt_fh.write(f"             → {parsed}\n")
                    self._txt_fh.flush()
                except Exception:
                    pass

        if self._gui_callback:
            try:
                self._gui_callback(entry)
            except Exception:
                pass


# ── Module-level singleton ────────────────────────────────────────────────────
logger = ModbusLogger()
