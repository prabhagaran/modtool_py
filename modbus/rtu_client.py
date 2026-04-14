"""
modbus/rtu_client.py
────────────────────
Thread-safe Modbus RTU client wrapper (pymodbus 3.x ModbusSerialClient).
"""
import threading
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException, ConnectionException
from modbus._dispatch import _dispatch


class RTUClient:
    """Thin, thread-safe wrapper around pymodbus ModbusSerialClient."""

    def __init__(self):
        self._client: ModbusSerialClient | None = None
        self._lock   = threading.Lock()
        self._connected = False

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Connection lifecycle ──────────────────────────────────────────────────

    def connect(self, port: str, baudrate=9600, parity="N",
                stopbits=1, bytesize=8, timeout: float = 1.0) -> bool:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._client = ModbusSerialClient(
                port     = port,
                baudrate = int(baudrate),
                parity   = str(parity)[0].upper(),
                stopbits = float(stopbits),
                bytesize = int(bytesize),
                timeout  = float(timeout),
                retries  = 3,
            )
            self._connected = bool(self._client.connect())
            return self._connected

    def disconnect(self) -> None:
        with self._lock:
            if self._client:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._connected = False

    # ── Execute ───────────────────────────────────────────────────────────────

    def execute(self, fc: int, address: int, slave_id: int = 1,
                count: int = 1, values=None):
        """
        Execute a Modbus function.
        Returns ``(response, error_str)`` tuple.
        *error_str* is ``None`` on success.
        """
        if not self._connected or self._client is None:
            return None, "Not connected"
        with self._lock:
            try:
                return _dispatch(self._client, fc, address, slave_id, count, values), None
            except ConnectionException as exc:
                self._connected = False
                return None, f"Connection lost: {exc}"
            except ModbusException as exc:
                return None, f"Modbus error: {exc}"
            except Exception as exc:
                return None, f"Unexpected error: {exc}"



