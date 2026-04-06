#!/usr/bin/env python3
"""
Modbus TCP passive listener.

Binds a TCP server socket and waits for Modbus masters to connect.
Every incoming frame is decoded and printed in real-time — nothing is
forwarded, so the device (slave) never sees the traffic.

Usage:
    # listen on port 5020 (non-privileged)
    python tools/modbus_listener.py --port 5020

    # listen on standard port 502 (needs admin/root on most OSes)
    python tools/modbus_listener.py --port 502

    # reply with a Modbus error so the master does not time out
    python tools/modbus_listener.py --port 5020 --respond

    # also forward traffic to a real slave (transparent proxy/sniffer)
    python tools/modbus_listener.py --port 5020 --forward 192.168.1.10:502
"""
import argparse
import datetime
import socket
import struct
import sys
import threading


FC_NAMES = {
    1:  "Read Coils",
    2:  "Read Discrete Inputs",
    3:  "Read Holding Regs",
    4:  "Read Input Regs",
    5:  "Write Single Coil",
    6:  "Write Single Register",
    15: "Write Multiple Coils",
    16: "Write Multiple Registers",
    23: "Read/Write Multiple Regs",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def recv_exact(sock: socket.socket, n: int) -> bytes | None:
    """Receive exactly *n* bytes; returns None on disconnect."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def decode_request(uid: int, pdu: bytes) -> str:
    """Return a human-readable description of the Modbus PDU."""
    if not pdu:
        return "empty PDU"
    fc = pdu[0]
    fc_name = FC_NAMES.get(fc, f"FC{fc:02d}")
    addr = qty = None
    if len(pdu) >= 5 and fc in (1, 2, 3, 4, 5, 6, 15, 16):
        addr = struct.unpack(">H", pdu[1:3])[0]
        qty  = struct.unpack(">H", pdu[3:5])[0]
    detail = f"unit={uid}"
    if addr is not None:
        detail += f"  addr={addr}  qty={qty}"
    return f"{fc_name}  ({detail})"


# ── Per-connection handler ────────────────────────────────────────────────────

def handle_client(conn: socket.socket, peer: str,
                  respond: bool, forward_addr: tuple | None) -> None:
    fwd_sock: socket.socket | None = None
    if forward_addr:
        try:
            fwd_sock = socket.create_connection(forward_addr, timeout=3.0)
            print(f"[{ts()}]    ↪  forwarding to {forward_addr[0]}:{forward_addr[1]}")
        except OSError as e:
            print(f"[{ts()}]  ✖  cannot connect to forward target: {e}")

    print(f"[{ts()}]  ⟶  Connected  {peer}")
    try:
        while True:
            # ── Read MBAP header (6 bytes) ────────────────────────────────
            header = recv_exact(conn, 6)
            if header is None:
                break
            tid, pid, length = struct.unpack(">HHH", header)
            if pid != 0 or length < 1 or length > 260:
                print(f"[{ts()}]  ?  {peer}  invalid MBAP (pid={pid} len={length}), dropping")
                break

            # ── Read (Unit ID + PDU) = length bytes ───────────────────────
            body = recv_exact(conn, length)
            if body is None:
                break

            uid = body[0]
            pdu = body[1:]
            raw = header + body
            raw_hex = " ".join(f"{b:02X}" for b in raw)

            desc = decode_request(uid, pdu)
            print(f"[{ts()}]  ◀  {peer}  |  {desc}")
            print(f"           RAW: {raw_hex}")

            # ── Optional: forward to real slave ───────────────────────────
            if fwd_sock:
                try:
                    fwd_sock.sendall(raw)
                    resp_hdr = recv_exact(fwd_sock, 6)
                    if resp_hdr:
                        _, _, resp_len = struct.unpack(">HHH", resp_hdr)
                        resp_body = recv_exact(fwd_sock, resp_len) or b""
                        resp_full = resp_hdr + resp_body
                        resp_hex  = " ".join(f"{b:02X}" for b in resp_full)
                        print(f"[{ts()}]  ▶  slave reply  |  {resp_hex}")
                        conn.sendall(resp_full)
                except OSError as e:
                    print(f"[{ts()}]  ✖  forward error: {e}")

            # ── Optional: send Modbus error so master does not time out ───
            elif respond:
                fc = pdu[0] if pdu else 0
                exc_pdu = bytes([uid, fc | 0x80, 0x01])   # Illegal Function
                reply   = struct.pack(">HHH", tid, 0, len(exc_pdu)) + exc_pdu
                try:
                    conn.sendall(reply)
                except OSError:
                    break

    except Exception as e:
        print(f"[{ts()}]  ✖  {peer} error: {e}")
    finally:
        conn.close()
        if fwd_sock:
            fwd_sock.close()
        print(f"[{ts()}]  ✕  Disconnected  {peer}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Modbus TCP passive listener / sniffer")
    p.add_argument("--port", type=int, default=5020,
                   help="Local port to bind (default 5020; use 502 for standard Modbus, may need admin)")
    p.add_argument("--respond", action="store_true",
                   help="Reply with a Modbus error frame so the master does not time out")
    p.add_argument("--forward", metavar="HOST:PORT",
                   help="Forward every request to a real slave and show its reply (transparent proxy mode)")
    args = p.parse_args()

    forward_addr: tuple | None = None
    if args.forward:
        parts = args.forward.rsplit(":", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            print("--forward must be HOST:PORT  e.g. 192.168.1.10:502")
            sys.exit(1)
        forward_addr = (parts[0], int(parts[1]))

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("0.0.0.0", args.port))
    except PermissionError:
        print(f"Permission denied on port {args.port}. Try a port > 1024 or run as admin.")
        sys.exit(1)
    srv.listen(10)

    mode = "forward → " + args.forward if args.forward else ("respond (error)" if args.respond else "silent (log only)")
    print(f"[{ts()}]  Modbus TCP listener  port={args.port}  mode={mode}")
    print(f"           Waiting for connections…  (Ctrl+C to stop)\n")

    try:
        while True:
            conn, addr = srv.accept()
            peer = f"{addr[0]}:{addr[1]}"
            t = threading.Thread(
                target=handle_client,
                args=(conn, peer, args.respond, forward_addr),
                daemon=True,
            )
            t.start()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
