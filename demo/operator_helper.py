#!/usr/bin/env python3
"""Operator-side helper for the demo: runs the simulator and can put it back to a fresh state.

    python3 demo/operator_helper.py serve [--port 8765] [--state .state/live.sqlite] [--control-port 8766] [--fresh]
    python3 demo/operator_helper.py reset [--control-port 8766]
    python3 demo/operator_helper.py status [--control-port 8766]

`serve` starts `simulator.py serve --mode agent` as a child process (the same program the runbook starts
by hand, so `simulator.py approve` and `scenario` work exactly as before) and listens on a second local
port for one thing: `POST /reset` stops the simulator, deletes its state file and starts it again, which
gives a supplier with no drafts, no orders, dataset revision 1 and a new operator token. Marbly's
"Forget everything and start over" calls this from the browser when the assistant was told where it is
(MARBLE_OPERATOR_URL); the assistant process itself never calls it and never opens the state file.

This belongs in the operator's terminal, next to the credential. It binds 127.0.0.1 only, carries no
token of its own, and offers no approve or scenario route: those stay typed commands.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIMULATOR = ROOT / "simulator.py"


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for(port: int, up: bool, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(port) == up:
            return True
        time.sleep(0.1)
    return False


class Supplier:
    """The simulator as a child process, restartable from a fresh file."""

    def __init__(self, port: int, state: Path):
        self.port, self.state = port, state
        self.proc: subprocess.Popen | None = None
        self.resets = 0
        self.lock = threading.Lock()

    def start(self) -> None:
        if port_open(self.port):
            raise SystemExit(f"port {self.port} is already in use; stop the other simulator or pick --port")
        cmd = [sys.executable, str(SIMULATOR), "serve", "--mode", "agent", "--port", str(self.port), "--state", str(self.state)]
        self.proc = subprocess.Popen(cmd, cwd=ROOT)
        if not wait_for(self.port, True):
            raise SystemExit("the simulator did not come up")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()  # SIGTERM: a background job's child ignores SIGINT; the simulator commits per request, so this is safe
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        wait_for(self.port, False, 5)

    def wipe(self) -> list[str]:
        removed = []
        for suffix in ("", "-journal", "-wal", "-shm"):
            p = Path(str(self.state) + suffix)
            if p.exists():
                p.unlink()
                removed.append(p.name)
        return removed

    def reset(self) -> dict:
        with self.lock:
            self.stop()
            removed = self.wipe()
            self.start()
            self.resets += 1
            return {"reset": True, "removed": removed, "state": str(self.state), "port": self.port, "resets": self.resets,
                    "note": "fresh supplier: no drafts, no orders, dataset revision 1, new operator token"}

    def status(self) -> dict:
        return {"running": bool(self.proc and self.proc.poll() is None), "pid": self.proc.pid if self.proc else None,
                "port": self.port, "state": str(self.state), "resets": self.resets}


def make_handler(supplier: Supplier):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)

        def _cors(self) -> None:  # the browser on another local port calls this directly
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            if self.path == "/status":
                return self._send(200, supplier.status())
            self._send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path == "/reset":
                try:
                    return self._send(200, supplier.reset())
                except Exception as e:  # the operator sees the reason; nothing else is attempted
                    return self._send(500, {"reset": False, "error": str(e)})
            self._send(404, {"error": "not_found"})

        def log_message(self, fmt, *args):
            sys.stderr.write("operator helper: " + fmt % args + "\n")

    return Handler


def serve(args: argparse.Namespace) -> int:
    state = Path(args.state)
    supplier = Supplier(args.port, state)
    if args.fresh:
        removed = supplier.wipe()
        print(f"fresh start: removed {removed or 'nothing'} under {state.parent}", flush=True)
    supplier.start()
    httpd = HTTPServer(("127.0.0.1", args.control_port), make_handler(supplier))
    print(f"operator helper on http://127.0.0.1:{args.control_port} (POST /reset, GET /status); "
          f"simulator pid {supplier.proc.pid} on port {args.port}, state {state}", flush=True)
    print("Ctrl-C (or kill) stops both. Approve and scenario stay typed commands with the same --port and --state.", flush=True)
    # a plain `kill` must also take the simulator down with it, and a background job has SIGINT ignored
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        supplier.stop()
    return 0


def call(args: argparse.Namespace, path: str, method: str) -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{args.control_port}{path}", method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(json.dumps(json.load(r), indent=2))
            return 0
    except urllib.error.HTTPError as e:
        print(e.read().decode(), file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"no operator helper on port {args.control_port}: {e.reason}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["serve", "reset", "status"])
    parser.add_argument("--port", type=int, default=8765, help="the simulator's port (serve)")
    parser.add_argument("--state", default=str(ROOT / ".state" / "simulator.sqlite"), help="the simulator's state file (serve)")
    parser.add_argument("--control-port", type=int, default=8766, help="this helper's port")
    parser.add_argument("--fresh", action="store_true", help="serve: delete the state file before the first start")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve(args)
    return call(args, "/reset" if args.command == "reset" else "/status", "POST" if args.command == "reset" else "GET")


if __name__ == "__main__":
    os.chdir(ROOT)
    sys.exit(main())
