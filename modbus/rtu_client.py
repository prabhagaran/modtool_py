"""
modbus/rtu_client.py
────────────────────
Thread-safe Modbus RTU client wrapper (pymodbus 3.x ModbusSerialClient).
"""
import threading
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException, ConnectionException


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


# ── Shared dispatcher (also used by TCPClient) ───────────────────────────────

def _dispatch(client, fc: int, address: int, slave_id: int,
              count: int, values):
    """Route the call to the correct pymodbus client method."""
    fc = int(fc)

    def _invoke(method, kwargs: dict, sid: int):
        """Call *method* with several calling conventions to support
        multiple pymodbus versions and client implementations.

        Order tried:
          1. keyword `slave=` (pymodbus 3.x)
          2. keyword `unit=`  (pymodbus 2.x)
          3. positional: method(<address>, <count>, <unit>) (older styles)
          4. positional: method(<address>, <value>, <unit>) for writes

        Any TypeError from incompatible signature will be caught and the
        next convention tried. Other exceptions propagate normally.
        """
        # 1) try `slave=`
        try:
            return method(**{**kwargs, "slave": sid})
        except TypeError:
            pass

        # 2) try `unit=`
        try:
            return method(**{**kwargs, "unit": sid})
        except TypeError:
            pass

        # 3) try positional calling conventions
        try:
            # Build positional args from kwargs ordering (address, count/value)
            pos = []
            if "address" in kwargs:
                pos.append(kwargs["address"])
            if "count" in kwargs:
                pos.append(kwargs["count"])
            if "value" in kwargs:
                pos.append(kwargs["value"])
            # Append unit as final positional
            pos.append(sid)
            return method(*pos)
        except TypeError:
            pass

        # Give up and call once more with the original kwargs to raise the
        # original error for easier debugging.
        return method(**{**kwargs})

    if   fc == 1:
        return _invoke(client.read_coils, dict(address=address, count=count), slave_id)
    elif fc == 2:
        return _invoke(client.read_discrete_inputs, dict(address=address, count=count), slave_id)
    elif fc == 3:
        return _invoke(client.read_holding_registers, dict(address=address, count=count), slave_id)
    elif fc == 4:
        return _invoke(client.read_input_registers, dict(address=address, count=count), slave_id)
    elif fc == 5:
        val = bool(values[0]) if values else False
        return _invoke(client.write_coil, dict(address=address, value=val), slave_id)
    elif fc == 6:
        val = int(values[0]) & 0xFFFF if values else 0
        return _invoke(client.write_register, dict(address=address, value=val), slave_id)
    elif fc == 15:
        vals = [bool(v) for v in values] if values else [False]
        return _invoke(client.write_coils, dict(address=address, values=vals), slave_id)
    elif fc == 16:
        vals = [int(v) & 0xFFFF for v in values] if values else [0]
        return _invoke(client.write_registers, dict(address=address, values=vals), slave_id)
    else:
        raise ValueError(f"Unsupported function code: {fc}")
