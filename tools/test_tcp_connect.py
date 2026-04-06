#!/usr/bin/env python3
"""
Simple diagnostic for TCP/Modbus connectivity.
Usage: python tools/test_tcp_connect.py --host 192.168.1.123 --port 502 --unit 1
"""
import argparse
import socket
import sys
import traceback
from pymodbus.client import ModbusTcpClient


def socket_check(host, port, timeout=3.0):
    try:
        s = socket.create_connection((host, int(port)), timeout=timeout)
        s.close()
        return True, "OK"
    except Exception as e:
        return False, str(e)


def pymodbus_check(host, port, timeout=3.0, unit=1):
    client = ModbusTcpClient(host, port=int(port), timeout=float(timeout), retries=0)
    try:
        ok = client.connect()
        if not ok:
            return False, "pymodbus.connect() returned False"
        # Try a lightweight read (holding register 0, qty 1)
        try:
            # Try both keyword variants for unit/slave for compatibility
            rr = client.read_holding_registers(0, 1, unit=unit)
            return True, f"Read result: {rr}"
        except TypeError:
            rr = client.read_holding_registers(0, 1, slave=unit)
            return True, f"Read result (slave=): {rr}"
        except Exception as e:
            return True, f"connected but read failed: {e}"
    except Exception as e:
        return False, f"pymodbus exception: {e}"
    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == '__main__':
    p = argparse.ArgumentParser(description="TCP / Modbus connectivity test")
    p.add_argument("--host", required=True, help="IP or hostname of device")
    p.add_argument("--port", required=True, help="TCP port (e.g. 502)")
    p.add_argument("--unit", type=int, default=1, help="Modbus unit id (default 1)")
    p.add_argument("--timeout", type=float, default=3.0, help="Timeout seconds")
    args = p.parse_args()

    print(f"Testing socket connectivity to {args.host}:{args.port} (timeout={args.timeout}s)")
    ok, msg = socket_check(args.host, args.port, timeout=args.timeout)
    print("Socket:", "OK" if ok else "FAILED", msg)

    print(f"Testing pymodbus connection to {args.host}:{args.port} unit={args.unit}")
    ok2, msg2 = pymodbus_check(args.host, args.port, timeout=args.timeout, unit=args.unit)
    print("pymodbus:", "OK" if ok2 else "FAILED", msg2)

    if not ok or not ok2:
        print("\nHints: check IP, port, firewall, and device listening on that port.")
        print("On Windows try: Test-NetConnection -ComputerName <host> -Port <port>\n  or use PowerShell: Test-NetConnection -ComputerName {0} -Port {1}".format(args.host, args.port))
        print("Or try: python -c \"import socket; socket.create_connection(('{0}',{1}),timeout=3)\"".format(args.host, args.port))
        sys.exit(2)
    sys.exit(0)
