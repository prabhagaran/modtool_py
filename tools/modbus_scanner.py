#!/usr/bin/env python3
"""
Modbus TCP network scanner.

Probes a subnet, IP range, or single host to discover Modbus TCP devices.
Uses raw TCP sockets (no pymodbus) so it's fast and has no extra dependencies.

Usage examples:
    # Scan an entire /24 subnet
    python tools/modbus_scanner.py --subnet 192.168.1.0/24

    # Scan a specific IP range
    python tools/modbus_scanner.py --range 192.168.1.1 192.168.1.50

    # Try every unit ID (1-247) on one host
    python tools/modbus_scanner.py --host 192.168.1.100 --unit-range 1 247

    # Custom port / timeout / worker count
    python tools/modbus_scanner.py --subnet 10.0.0.0/24 --port 5020 --timeout 0.5 --workers 100
"""
import argparse
import concurrent.futures
import datetime
import ipaddress
import socket
import struct
import sys


FC_NAMES = {
    3:  "Read Holding Regs",
    4:  "Read Input Regs",
    1:  "Read Coils",
    2:  "Read Discrete Inputs",
}


# ── Request builder ───────────────────────────────────────────────────────────

def build_fc3(unit_id: int) -> bytes:
    """Build a Modbus TCP FC03 Read Holding Registers frame (addr=0, qty=1)."""
    return struct.pack(">HHHBBHH",
        0x0001,     # Transaction ID
        0x0000,     # Protocol ID (Modbus)
        0x0006,     # Length
        unit_id,    # Unit/Slave ID
        0x03,       # FC03
        0x0000,     # Start address 0
        0x0001,     # Quantity 1
    )


def ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


# ── Single probe ──────────────────────────────────────────────────────────────

def probe(host: str, port: int, unit_id: int, timeout: float) -> tuple:
    """
    Returns (host, port, unit_id, ok: bool, detail: str)
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((host, port))
            s.sendall(build_fc3(unit_id))
            data = s.recv(256)

        if len(data) < 8:
            return host, port, unit_id, False, "response too short"

        # Validate MBAP
        tid, pid, length = struct.unpack(">HHH", data[:6])
        if pid != 0:
            return host, port, unit_id, False, f"invalid Protocol ID {pid}"

        uid = data[6]
        fc  = data[7] if len(data) > 7 else 0

        # Normal register response
        if fc == 0x03 and len(data) >= 10:
            byte_count = data[8]
            regs = []
            for i in range(0, byte_count, 2):
                idx = 9 + i
                if idx + 1 <= len(data):
                    regs.append(struct.unpack(">H", data[idx:idx + 2])[0])
            return host, port, uid, True, f"FC03 OK  regs={regs}"

        # Modbus exception — device responded, just refused the request
        if fc & 0x80:
            exc = data[8] if len(data) > 8 else 0
            exc_names = {
                0x01: "Illegal Function",
                0x02: "Illegal Data Address",
                0x03: "Illegal Data Value",
                0x04: "Device Failure",
            }
            exc_str = exc_names.get(exc, f"0x{exc:02X}")
            return host, port, uid, True, f"Modbus exception: {exc_str} (device IS responding)"

        raw_hex = " ".join(f"{b:02X}" for b in data)
        return host, port, uid, True, f"unknown response  raw={raw_hex}"

    except ConnectionRefusedError:
        return host, port, unit_id, False, "connection refused"
    except (socket.timeout, TimeoutError):
        return host, port, unit_id, False, "timeout"
    except OSError as e:
        return host, port, unit_id, False, str(e)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Modbus TCP network scanner")

    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--subnet",  metavar="CIDR",
                   help="e.g. 192.168.1.0/24")
    g.add_argument("--range",   nargs=2, metavar=("START", "END"),
                   help="e.g. 192.168.1.1  192.168.1.50")
    g.add_argument("--host",    metavar="IP",
                   help="Single host (pair with --unit-range to enumerate unit IDs)")

    p.add_argument("--unit",       type=int, default=1,
                   help="Unit ID for subnet/range scan (default 1)")
    p.add_argument("--unit-range", nargs=2, type=int, metavar=("START", "END"),
                   help="Enumerate unit IDs on --host, e.g. --unit-range 1 247")
    p.add_argument("--port",    type=int,   default=502,
                   help="TCP port to probe (default 502)")
    p.add_argument("--timeout", type=float, default=1.0,
                   help="Timeout per probe in seconds (default 1.0)")
    p.add_argument("--workers", type=int,   default=64,
                   help="Parallel worker threads (default 64)")

    args = p.parse_args()

    # ── Build task list ───────────────────────────────────────────────────────
    tasks: list[tuple] = []

    if args.host and args.unit_range:
        uid_s, uid_e = args.unit_range
        tasks = [(args.host, args.port, uid, args.timeout)
                 for uid in range(uid_s, uid_e + 1)]
        print(f"[{ts()}]  Scanning {args.host}:{args.port}  unit IDs {uid_s}–{uid_e}  ({len(tasks)} probes)")

    elif args.host:
        tasks = [(args.host, args.port, args.unit, args.timeout)]
        print(f"[{ts()}]  Probing {args.host}:{args.port}  unit={args.unit}")

    elif args.subnet:
        net = ipaddress.ip_network(args.subnet, strict=False)
        tasks = [(str(ip), args.port, args.unit, args.timeout) for ip in net.hosts()]
        print(f"[{ts()}]  Scanning subnet {args.subnet}  ({len(tasks)} hosts)")

    else:  # --range
        start = int(ipaddress.ip_address(args.range[0]))
        end   = int(ipaddress.ip_address(args.range[1]))
        tasks = [(str(ipaddress.ip_address(i)), args.port, args.unit, args.timeout)
                 for i in range(start, end + 1)]
        print(f"[{ts()}]  Scanning {args.range[0]} → {args.range[1]}  ({len(tasks)} hosts)")

    print(f"           port={args.port}  unit={args.unit}  timeout={args.timeout}s  workers={args.workers}\n")

    # ── Run probes in parallel ────────────────────────────────────────────────
    found: list[tuple] = []
    done  = 0
    total = len(tasks)
    report_every = max(1, total // 10)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(probe, *t): t for t in tasks}
        for f in concurrent.futures.as_completed(futures):
            host, port, uid, ok, detail = f.result()
            done += 1

            if ok:
                found.append((host, port, uid, detail))
                print(f"  ✔  {host}:{port}  unit={uid}  → {detail}")

            if done % report_every == 0 and done < total:
                print(f"     … {done}/{total} probed", flush=True)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n[{ts()}]  Done.  {len(found)} / {total} responded.\n")

    if found:
        print("Responding hosts:")
        for host, port, uid, detail in sorted(found):
            print(f"  {host}:{port}  unit={uid}  {detail}")
    else:
        print("No Modbus devices found. Check subnet, port, and firewall.")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
