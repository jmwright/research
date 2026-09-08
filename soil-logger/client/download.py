#!/usr/bin/env python3
"""
download.py -- pull the soil log from the device over TCP and save it to CSV.

The device sends its entire log on connect, then closes. That's the whole
protocol: connect, read to EOF, save. Because the device holds the full log,
we simply overwrite the local file with each download (idempotent).

Examples
--------
  python download.py --host 192.168.1.50
  python download.py --host 192.168.1.50 --port 8266 --out soil_log.csv
"""
import argparse
import socket
import sys


def download(host, port, timeout):
    chunks = []
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        while True:
            try:
                b = s.recv(4096)
            except socket.timeout:
                break
            if not b:
                break
            chunks.append(b)
    return b"".join(chunks)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, help="device IP address")
    ap.add_argument("--port", type=int, default=8266)
    ap.add_argument("--out", default="soil_log.csv")
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()

    try:
        data = download(args.host, args.port, args.timeout)
    except OSError as e:
        print("connection failed: %s" % e, file=sys.stderr)
        return 1

    if not data:
        print("no data received", file=sys.stderr)
        return 2

    with open(args.out, "wb") as f:
        f.write(data)

    lines = data.count(b"\n")
    print("saved %d bytes (%d lines) to %s" % (len(data), lines, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
