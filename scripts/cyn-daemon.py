#!/usr/bin/env python3
"""
Cynthion Daemon - Background service for cyn

Long-running service that clients (cyn_main.py) attach to over a local
AF_UNIX socket speaking JSON-lines. Per the awto-dan orchestration standard
(docs/coding/orchestration.md §5), local IPC is AF_UNIX + JSON-lines, never
HTTP — there are no non-Python or cross-host clients here.

Wire protocol (one JSON object per line, request → response):
  -> {"cmd": "status"}\n
  <- {"ok": true, "daemon": "cyn", "pid": 1234, ...}\n

Usage:
  cyn-daemon start      - Start daemon
  cyn-daemon stop       - Stop daemon
  cyn-daemon status     - Check status
  cyn-daemon restart    - Restart daemon (prefers-alive)

Or from cyn CLI:
  cyn daemon start|stop|status|restart
"""

import argparse
import json
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

# Paths. Pidfile and socket live under the workspace ./tmp (not system /tmp),
# and the pidfile is JSON carrying {pid, socket, started, args} so `status`
# can report *what* is running, not merely that something is (standard §5.2).
REPO_ROOT = Path(__file__).resolve().parent.parent
TMP_DIR = REPO_ROOT / "tmp"
PID_FILE = TMP_DIR / "cyn-daemon.pid"
SOCKET_FILE = TMP_DIR / "cyn-daemon.sock"
LOG_FILE = TMP_DIR / "logs" / "cyn-daemon.log"

# How long `stop` waits for graceful exit after SIGTERM before escalating to
# SIGKILL: the daemon only has to unlink its socket and close the listener, so
# a fraction of a second is ample; 5 s is a generous ceiling before we assume
# it is wedged. Polled, never slept-through (standard §5.5).
STOP_DEADLINE_S = 5.0
STOP_POLL_S = 0.05


def _now_iso() -> str:
    """ISO 8601 timestamp with offset (local-aware)."""
    return datetime.now(timezone.utc).astimezone().isoformat()


class CynDaemon:
    """Cynthion daemon: AF_UNIX + JSON-lines local IPC."""

    def __init__(self, pidfile=None, socketfile=None, logfile=None):
        self.pidfile = pidfile or PID_FILE
        self.socketfile = socketfile or SOCKET_FILE
        self.logfile = logfile or LOG_FILE
        self.running = True
        self.start_time = datetime.now(timezone.utc).astimezone()
        self.request_count = 0
        self._sock = None

        self.logfile.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[logging.FileHandler(self.logfile), logging.StreamHandler()],
        )
        self.logger = logging.getLogger("cyn-daemon")

    # ── lifecycle ────────────────────────────────────────────────────────────

    def signal_handler(self, signum, frame):
        """Graceful SIGTERM/SIGINT: stop the accept loop and clean up."""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

    def start(self):
        """Install signal handlers and serve. Pidfile written after socket binds.

        The parent (cyn's `daemon start`) launches this via
        Popen(start_new_session=True), so the process is already detached from
        the controlling terminal and its stdio redirected — no python-daemon
        dependency is needed. We only own signal handling and the pidfile here.
        """
        if self.is_running():
            info = self.read_pidfile() or {}
            print(f"Daemon already running (PID: {info.get('pid')})")
            return 1

        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        self.logger.info("Cynthion daemon starting")
        self.run_server()
        return 0

    def run_server(self):
        """Bind the AF_UNIX socket, publish the pidfile, serve JSON-lines."""
        if self.socketfile.exists():
            self.socketfile.unlink()

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(self.socketfile))
        self._sock.listen(8)

        # Health = socket is bound and listening. Only now is the daemon
        # actually answering, so only now do we publish the JSON pidfile
        # (standard §5.3 — pidfile after health probe passes).
        self._write_pidfile()
        self.logger.info(f"Listening on {self.socketfile}")

        try:
            while self.running:
                try:
                    conn, _ = self._sock.accept()
                except OSError:
                    break  # socket closed by signal_handler
                with conn:
                    self._serve_conn(conn)
        finally:
            self.socketfile.unlink(missing_ok=True)
            self.pidfile.unlink(missing_ok=True)
            self.logger.info("Cynthion daemon stopped")

    def _serve_conn(self, conn):
        """Handle one connection: read a JSON-line request, write a response."""
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        try:
            req = json.loads(line.decode())
        except (ValueError, UnicodeDecodeError):
            self._send(conn, {"ok": False, "error": "malformed request"})
            return
        self.request_count += 1
        self._send(conn, self.dispatch(req))

    @staticmethod
    def _send(conn, obj):
        conn.sendall((json.dumps(obj) + "\n").encode())

    # ── request handlers ─────────────────────────────────────────────────────

    def dispatch(self, req: dict) -> dict:
        cmd = req.get("cmd")
        handler = {
            "health": self._h_health,
            "status": self._h_status,
            "project-status": self._h_project_status,
            "commands": self._h_commands,
        }.get(cmd)
        if handler is None:
            return {"ok": False, "error": f"unknown cmd: {cmd!r}"}
        return handler()

    def _uptime_s(self) -> float:
        return (datetime.now(timezone.utc).astimezone() - self.start_time).total_seconds()

    def _h_health(self) -> dict:
        return {"ok": True, "status": "running",
                "uptime_seconds": self._uptime_s(),
                "requests_processed": self.request_count}

    def _h_status(self) -> dict:
        return {"ok": True, "daemon": "cyn", "version": "1.0", "pid": os.getpid(),
                "started": self.start_time.isoformat(),
                "uptime_seconds": self._uptime_s(),
                "requests": self.request_count, "status": "running"}

    def _h_project_status(self) -> dict:
        return {"ok": True, "project": "Cynthion", "phase": "Phase 1",
                "status": "3/4 builds successful",
                "components": {
                    "apollo": "building", "moondancer": "building",
                    "analyzer_gateware": "building",
                    "facedancer_gateware": "known_issue"}}

    def _h_commands(self) -> dict:
        return {"ok": True, "available_commands": [
            "fpga sim_test", "apollo build", "moondancer build",
            "gateware elaborate", "setup", "setup --parallel",
            "status", "versions", "prereqs"]}

    # ── control commands (run from the CLI process, not the daemon) ──────────

    def stop(self):
        """TERM → poll for exit (deadline) → KILL. Never a bare sleep (§5.5)."""
        if not self.is_running():
            print("Daemon not running")
            return 1
        info = self.read_pidfile() or {}
        pid = info.get("pid")
        if pid is None:
            print("Pidfile has no PID; removing")
            self.pidfile.unlink(missing_ok=True)
            return 1
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            print(f"Process {pid} not found")
            self.pidfile.unlink(missing_ok=True)
            return 0

        deadline = time.monotonic() + STOP_DEADLINE_S
        while time.monotonic() < deadline:
            if not self.is_running():
                print(f"Daemon stopped (PID {pid})")
                self.pidfile.unlink(missing_ok=True)
                self.socketfile.unlink(missing_ok=True)
                return 0
            time.sleep(STOP_POLL_S)  # bounded poll toward a deadline, not a fixed wait

        self.logger.warning(f"Daemon {pid} did not exit in {STOP_DEADLINE_S}s; SIGKILL")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.pidfile.unlink(missing_ok=True)
        self.socketfile.unlink(missing_ok=True)
        print(f"Daemon killed (PID {pid})")
        return 0

    def status(self):
        info = self.read_pidfile()
        if self.is_running() and info:
            print(f"Daemon running (PID: {info.get('pid')})")
            print(f"Socket: {self.socketfile}")
            print(f"Started: {info.get('started')}")
            return 0
        print("Daemon not running")
        return 1

    def restart(self):
        """Prefers-alive: restart always stops then starts (args are fixed here)."""
        if self.is_running():
            self.stop()
        return self.start()

    # ── pidfile / liveness ───────────────────────────────────────────────────

    def _write_pidfile(self):
        record = {
            "pid": os.getpid(),
            "socket": str(self.socketfile),
            "started": self.start_time.isoformat(),
            "args": sys.argv[1:],
        }
        self.pidfile.write_text(json.dumps(record) + "\n")

    def read_pidfile(self):
        """Return the JSON pidfile record, or None if absent/unreadable."""
        if not self.pidfile.exists():
            return None
        try:
            return json.loads(self.pidfile.read_text())
        except (ValueError, OSError):
            return None

    def is_running(self):
        info = self.read_pidfile()
        if not info or "pid" not in info:
            return False
        try:
            os.kill(int(info["pid"]), 0)
            return True
        except (ProcessLookupError, ValueError, PermissionError):
            return False


def main():
    parser = argparse.ArgumentParser(
        description="Cynthion Daemon (AF_UNIX + JSON-lines)",
        epilog="Examples:\n"
               "  cyn-daemon start | stop | status | restart\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=["start", "stop", "status", "restart"],
                        help="Daemon command")
    parser.add_argument("--pidfile", type=Path, default=PID_FILE)
    parser.add_argument("--socketfile", type=Path, default=SOCKET_FILE)
    parser.add_argument("--logfile", type=Path, default=LOG_FILE)
    args = parser.parse_args()

    d = CynDaemon(pidfile=args.pidfile, socketfile=args.socketfile, logfile=args.logfile)
    return {
        "start": d.start,
        "stop": d.stop,
        "status": d.status,
        "restart": d.restart,
    }[args.command]()


if __name__ == "__main__":
    sys.exit(main())
