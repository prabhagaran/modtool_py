"""
modbus/manager.py
─────────────────
Abstraction layer over RTUClient / TCPClient.
  • Single point of entry for all Modbus operations
  • Owns the auto-poll background thread
  • Integrates with logger and parser
  • Exposes response_callback so the GUI stays decoupled

Singleton: ``from modbus.manager import manager``
"""
import threading
import time

from modbus.rtu_client import RTUClient
from modbus.tcp_client import TCPClient
from utils.parser      import parse_response, format_tx_frame
from utils.logger      import logger


_FC_NAMES = {
    1: "Read Coils",        2: "Read Discrete Inputs",
    3: "Read Holding Regs", 4: "Read Input Regs",
    5: "Write Coil",        6: "Write Register",
    15: "Write Coils",      16: "Write Registers",
}


class ModbusManager:
    """
    Central coordinator for Modbus communication.
    All public methods are thread-safe.
    """

    def __init__(self):
        self._rtu  = RTUClient()
        self._tcp  = TCPClient()
        self._mode = "TCP"       # "RTU" | "TCP"
        self._slave_id = 1

        # Callbacks (set by GUI)
        self._response_cb     = None  # fn(result: dict)
        self._poll_stopped_cb = None  # fn() – called when poll exits unexpectedly

        # Auto-poll state
        self._poll_active   = False
        self._poll_thread   = None
        self._poll_lock     = threading.Lock()
        self._poll_params   = {}
        self._poll_interval = 1.0   # seconds

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._rtu.connected if self._mode == "RTU" else self._tcp.connected

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def slave_id(self) -> int:
        return self._slave_id

    # ── Configuration ─────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        self._mode = mode.upper()

    def set_response_callback(self, fn) -> None:
        """Register *fn(result: dict)* – called after every completed operation."""
        self._response_cb = fn

    def set_poll_stopped_callback(self, fn) -> None:
        """Register *fn()* – called when the poll loop exits (e.g. on disconnect)."""
        self._poll_stopped_cb = fn

    def fire_error(self, message: str) -> None:
        """Push an error result through the response callback."""
        result = dict(raw_hex="", parsed="", values=[],
                      error=message, is_error=True)
        if self._response_cb:
            self._response_cb(result)

    # ── Connection ────────────────────────────────────────────────────────────

    def connect_tcp(self, host: str, port: int, unit_id: int,
                    timeout: float = 3.0) -> bool:
        self._mode     = "TCP"
        self._slave_id = int(unit_id)
        success = self._tcp.connect(host, port, timeout)
        if success:
            logger.log_info(f"TCP connected → {host}:{port}  unit={unit_id}")
        else:
            logger.log_error(f"TCP connection failed → {host}:{port}")
        return success

    def connect_rtu(self, port: str, baudrate, parity: str,
                    stopbits, bytesize, slave_id,
                    timeout: float = 1.0) -> bool:
        self._mode     = "RTU"
        self._slave_id = int(slave_id)
        success = self._rtu.connect(port, baudrate, parity, stopbits, bytesize, timeout)
        if success:
            logger.log_info(
                f"RTU connected → {port}  {baudrate}/{parity[0]}/{stopbits}/{bytesize}"
                f"  slave={slave_id}"
            )
        else:
            logger.log_error(f"RTU connection failed → {port}")
        return success

    def disconnect(self) -> None:
        self.stop_polling()
        if self._mode == "RTU":
            self._rtu.disconnect()
        else:
            self._tcp.disconnect()
        logger.log_info("Disconnected")

    # ── Execute a single Modbus operation ────────────────────────────────────

    def execute(self, fc: int, address: int,
                slave_id: int = None, count: int = 1,
                values=None, data_type: str = "UINT16") -> dict:
        """
        Perform one Modbus transaction.
        Logs TX + RX frames, parses the response, fires response_callback.
        Always returns a result dict (never raises).
        """
        sid = slave_id if slave_id is not None else self._slave_id
        fc  = int(fc)

        # ── TX log ──────────────────────────────────────────────────────────
        tx_frame = format_tx_frame(self._mode, fc, sid, address, count, values)
        fc_label  = _FC_NAMES.get(fc, f"FC{fc:02d}")
        logger.log_tx(
            tx_frame,
            f"FC{fc:02d} {fc_label}  addr={address}  qty={count}"
        )

        # ── Execute ─────────────────────────────────────────────────────────
        if self._mode == "RTU":
            response, err = self._rtu.execute(fc, address, sid, count, values)
        else:
            response, err = self._tcp.execute(fc, address, sid, count, values)

        # ── Handle transport-level error ─────────────────────────────────────
        if err:
            logger.log_error(err)
            result = dict(raw_hex="", parsed="", values=[],
                          error=err, is_error=True)
            if self._response_cb:
                self._response_cb(result)
            return result

        # ── Parse + RX log ───────────────────────────────────────────────────
        result = parse_response(response, data_type)
        if result["is_error"]:
            logger.log_error(result["error"])
        else:
            logger.log_rx(result["raw_hex"], result["parsed"])

        if self._response_cb:
            self._response_cb(result)
        return result

    # ── Auto-poll ─────────────────────────────────────────────────────────────

    def start_polling(self, fc: int, address: int, count: int,
                      slave_id: int, interval_ms: int,
                      data_type: str) -> None:
        """Start background polling.  Any previous poll is stopped first."""
        self.stop_polling()
        with self._poll_lock:
            self._poll_params   = dict(
                fc=fc, address=address, count=count,
                slave_id=slave_id, data_type=data_type
            )
            self._poll_interval = max(interval_ms, 50) / 1000.0
            self._poll_active   = True
            self._poll_thread   = threading.Thread(
                target=self._poll_loop, daemon=True, name="ModbusPollThread"
            )
            self._poll_thread.start()

    def stop_polling(self) -> None:
        self._poll_active = False
        t = self._poll_thread
        if t and t.is_alive():
            t.join(timeout=3.0)
        self._poll_thread = None

    def _poll_loop(self) -> None:
        while self._poll_active and self.connected:
            p = self._poll_params
            self.execute(
                p["fc"], p["address"],
                slave_id=p["slave_id"], count=p["count"],
                data_type=p["data_type"]
            )
            # Sleep in small slices so stop_polling() is responsive
            deadline = time.monotonic() + self._poll_interval
            while self._poll_active and time.monotonic() < deadline:
                time.sleep(0.05)
        # Notify the GUI if the loop exited due to a connection drop
        if not self.connected and self._poll_stopped_cb:
            self._poll_stopped_cb()


# ── Module-level singleton ────────────────────────────────────────────────────
manager = ModbusManager()
