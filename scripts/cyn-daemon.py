#!/usr/bin/env python3
"""
Cynthion Daemon - Background service for cyn

Runs `cyn` as a daemon process that clients can connect to via HTTP/socket.

Usage:
  cyn-daemon start      - Start daemon
  cyn-daemon stop       - Stop daemon
  cyn-daemon status     - Check status
  cyn-daemon restart    - Restart daemon

Or from cyn CLI:
  cyn daemon start
  cyn daemon status
  cyn daemon stop
"""

import argparse
import daemon
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from datetime import datetime

# Try to import FastAPI for HTTP daemon
try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

# Paths
REPO_ROOT = Path(__file__).resolve().parent
PID_FILE = Path("/tmp/cyn-daemon.pid")
LOG_FILE = REPO_ROOT / "tmp" / "logs" / "cyn-daemon.log"
SOCKET_FILE = Path("/tmp/cyn-daemon.sock")

class CynDaemon:
    """Cynthion Daemon with HTTP API"""

    def __init__(self, pidfile=None, logfile=None):
        self.pidfile = pidfile or PID_FILE
        self.logfile = logfile or LOG_FILE
        self.running = True
        self.start_time = datetime.now()
        self.request_count = 0

        # Setup logging
        self.logfile.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(self.logfile),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger("cyn-daemon")

    def signal_handler(self, signum, frame):
        """Handle signals gracefully"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
        sys.exit(0)

    def start(self):
        """Start daemon (with daemonisation)"""
        if self.is_running():
            print(f"Daemon already running (PID: {self.read_pidfile()})")
            return 1

        # Use daemon context for proper daemonisation
        context = daemon.DaemonContext(
            pidfile=daemon.pidfile.PIDFile(str(self.pidfile)),
            stdout=open(self.logfile, 'a'),
            stderr=open(self.logfile, 'a'),
            signal_map={
                signal.SIGTERM: self.signal_handler,
                signal.SIGINT: self.signal_handler,
            }
        )

        with context:
            self.logger.info("Cynthion Daemon started")
            self.run_server()

        return 0

    def run_server(self):
        """Run HTTP server"""
        if not HAS_FASTAPI:
            self.logger.error("FastAPI not installed. Install with: pip install fastapi uvicorn")
            self._run_simple_server()
            return

        app = FastAPI(title="Cynthion Daemon")

        @app.get("/health")
        def health():
            return {
                "status": "running",
                "uptime_seconds": (datetime.now() - self.start_time).total_seconds(),
                "requests_processed": self.request_count
            }

        @app.get("/status")
        def status():
            self.request_count += 1
            return {
                "daemon": "cyn",
                "version": "1.0",
                "pid": os.getpid(),
                "started": self.start_time.isoformat(),
                "uptime_seconds": (datetime.now() - self.start_time).total_seconds(),
                "requests": self.request_count,
                "status": "running"
            }

        @app.get("/project/status")
        def project_status():
            self.request_count += 1
            return {
                "project": "Cynthion",
                "phase": "Phase 1",
                "status": "3/4 builds successful",
                "components": {
                    "apollo": "building",
                    "moondancer": "building",
                    "analyzer_gateware": "building",
                    "facedancer_gateware": "known_issue"
                }
            }

        @app.get("/commands")
        def list_commands():
            self.request_count += 1
            return {
                "available_commands": [
                    "fpga sim_test",
                    "apollo build",
                    "moondancer build",
                    "gateware elaborate",
                    "setup",
                    "setup --parallel",
                    "status",
                    "versions",
                    "prereqs"
                ]
            }

        self.logger.info("Starting HTTP server on 0.0.0.0:8765")
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8765,
            log_level="info"
        )

    def _run_simple_server(self):
        """Fallback: simple socket-based server (no FastAPI)"""
        self.logger.warning("Running in socket mode (FastAPI not available)")
        # Simple socket server listening for commands
        import socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        # Remove existing socket
        if SOCKET_FILE.exists():
            SOCKET_FILE.unlink()

        sock.bind(str(SOCKET_FILE))
        sock.listen(1)
        self.logger.info(f"Listening on {SOCKET_FILE}")

        while self.running:
            try:
                conn, _ = sock.accept()
                msg = conn.recv(1024).decode()
                response = json.dumps({"status": "ok", "message": msg})
                conn.send(response.encode())
                conn.close()
            except KeyboardInterrupt:
                break

        sock.close()

    def stop(self):
        """Stop daemon"""
        if not self.is_running():
            print("Daemon not running")
            return 1

        pid = self.read_pidfile()
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Daemon stopped (PID {pid})")
            # Wait for process to exit
            for _ in range(10):
                if not self.is_running():
                    self.pidfile.unlink(missing_ok=True)
                    return 0
                time.sleep(0.5)
            print("Warning: daemon may not have stopped")
            return 0
        except ProcessNotFoundError:
            print(f"Process {pid} not found")
            self.pidfile.unlink(missing_ok=True)
            return 0

    def status(self):
        """Check daemon status"""
        if self.is_running():
            pid = self.read_pidfile()
            print(f"Daemon running (PID: {pid})")
            if HAS_FASTAPI:
                print(f"HTTP API: http://localhost:8765")
                print(f"  /health     - Health check")
                print(f"  /status     - Daemon status")
                print(f"  /commands   - List commands")
            else:
                print(f"Socket: {SOCKET_FILE}")
            return 0
        else:
            print("Daemon not running")
            return 1

    def is_running(self):
        """Check if daemon is running"""
        if not self.pidfile.exists():
            return False
        try:
            pid = self.read_pidfile()
            os.kill(pid, 0)  # Check if process exists
            return True
        except (ProcessNotFoundError, ValueError):
            return False

    def read_pidfile(self):
        """Read PID from file"""
        return int(self.pidfile.read_text().strip())

def main():
    parser = argparse.ArgumentParser(
        description="Cynthion Daemon",
        epilog="""
Examples:
  cyn-daemon start      - Start daemon
  cyn-daemon stop       - Stop daemon
  cyn-daemon status     - Check status

  curl http://localhost:8765/status  - Query daemon via HTTP
        """
    )

    parser.add_argument("command", choices=["start", "stop", "status", "restart"],
                       help="Daemon command")
    parser.add_argument("--pidfile", type=Path, default=PID_FILE,
                       help="PID file location")
    parser.add_argument("--logfile", type=Path, default=LOG_FILE,
                       help="Log file location")

    args = parser.parse_args()

    daemon = CynDaemon(pidfile=args.pidfile, logfile=args.logfile)

    if args.command == "start":
        return daemon.start()
    elif args.command == "stop":
        return daemon.stop()
    elif args.command == "status":
        return daemon.status()
    elif args.command == "restart":
        daemon.stop()
        time.sleep(1)
        return daemon.start()

if __name__ == "__main__":
    sys.exit(main())
