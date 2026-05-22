#!/usr/bin/env python3
"""
Cynthion Unified Entry Point

Single command for AI agents and developers to control entire codebase:
  cyn status              - Project status (human + JSON)
  cyn ai-brief            - AI-friendly project summary
  cyn ai-schema           - All available commands as JSON schema
  cyn ai-tasks            - List actionable tasks for AI
  cyn list                - All available commands

Component Commands:
  cyn fpga <cmd>          - FPGA simulator: sim_test, sim_uart, sim_usb
  cyn apollo <cmd>        - Apollo: build, clean, get-deps, reset
  cyn moondancer <cmd>    - moondancer: build, clean
  cyn gateware <cmd>      - Gateware: elaborate, facedancer, list

Workspace Commands:
  cyn setup               - Full setup (sequential)
  cyn setup --parallel    - Full setup with parallelization
  cyn versions            - Show all tool versions
  cyn prereqs             - Check system prerequisites

CI/CD Commands:
  cyn ci install          - Install act (GitHub Actions runner)
  cyn ci list             - List available workflows
  cyn ci apollo           - Run Apollo CI locally
  cyn ci cynthion         - Run Cynthion CI locally
  cyn ci luna             - Run Luna CI locally

Daemon Commands (for GUI integration):
  cyn daemon start        - Start daemon (background service)
  cyn daemon stop         - Stop daemon
  cyn daemon status       - Check daemon status
  cyn daemon restart      - Restart daemon

Smart Routing:
  If daemon is running, all commands automatically connect to it via HTTP.
  If no daemon, commands run directly (inline).

Global Options:
  cyn --json <cmd>        - JSON output (AI-friendly)
  cyn --log file <cmd>    - Log to file
  cyn --verbose <cmd>     - Verbose output
"""

import argparse
import subprocess
import sys
import json
import logging
import os
import time
import signal
from pathlib import Path
from datetime import datetime

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# Workspace paths
SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
REPOS = Path.home() / "git" / "awtoau"
INSTALL_PY = SCRIPTS_DIR / "install.py"
CYN_DAEMON_PY = SCRIPTS_DIR / "cyn-daemon.py"
PID_FILE = Path("/tmp/cyn-daemon.pid")
DAEMON_URL = "http://localhost:8765"

# Hardware-facing paths (from awto.py)
MOONDANCER_FW = REPOS / "awto-cynthion" / "firmware" / "moondancer"
APOLLO_FW = REPOS / "awto-apollo" / "firmware"
GATEWARE_DIR = REPOS / "awto-cynthion" / "cynthion" / "python"
APP_DIR = REPO_ROOT / "app"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
TMP_DIR = REPO_ROOT / "tmp"

class Colors:
    """ANSI color codes"""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"

class CynDaemon:
    """Daemon management (start/stop/status)"""

    @staticmethod
    def is_running():
        """Check if daemon is running"""
        if not PID_FILE.exists():
            return False
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)  # Check if process exists
            return True
        except (ProcessNotFoundError, ValueError, OSError):
            return False

    @staticmethod
    def start():
        """Start daemon"""
        if CynDaemon.is_running():
            print(f"Daemon already running (PID: {PID_FILE.read_text().strip()})")
            return 1

        print("Starting Cynthion daemon...")
        try:
            subprocess.Popen(
                [sys.executable, str(CYN_DAEMON_PY), "start"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            # Wait for daemon to start
            for _ in range(10):
                if CynDaemon.is_running():
                    pid = PID_FILE.read_text().strip()
                    print(f"Daemon started (PID: {pid})")
                    print(f"HTTP API available at {DAEMON_URL}")
                    return 0
                time.sleep(0.5)
            print("Warning: daemon may not have started. Check logs.")
            return 1
        except Exception as e:
            print(f"Failed to start daemon: {e}")
            return 1

    @staticmethod
    def stop():
        """Stop daemon"""
        if not CynDaemon.is_running():
            print("Daemon not running")
            return 1

        try:
            subprocess.run(
                [sys.executable, str(CYN_DAEMON_PY), "stop"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            print("Daemon stopped")
            return 0
        except subprocess.CalledProcessError:
            print("Failed to stop daemon")
            return 1

    @staticmethod
    def status():
        """Check daemon status"""
        if not CynDaemon.is_running():
            print("Daemon not running")
            return 1

        try:
            subprocess.run(
                [sys.executable, str(CYN_DAEMON_PY), "status"],
                check=False
            )
            return 0
        except Exception as e:
            print(f"Error checking daemon status: {e}")
            return 1


class CynCLI:
    """Cynthion Unified Entry Point"""

    def __init__(self, json_mode=False, logfile=None, verbose=False):
        self.json_mode = json_mode
        self.verbose = verbose
        self.logger = self._setup_logging(logfile)
        self.daemon_available = HAS_REQUESTS and CynDaemon.is_running()

    def _setup_logging(self, logfile):
        """Setup logging"""
        logger = logging.getLogger("cyn")
        logger.setLevel(logging.DEBUG if self.verbose else logging.INFO)
        logger.handlers.clear()

        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(console)

        if logfile:
            fh = logging.FileHandler(logfile)
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            ))
            logger.addHandler(fh)

        return logger

    def daemon_request(self, endpoint, method="GET", data=None):
        """Send request to daemon (returns raw response or None)"""
        if not self.daemon_available or not HAS_REQUESTS:
            return None

        try:
            url = f"{DAEMON_URL}{endpoint}"
            if method == "GET":
                r = requests.get(url, timeout=5)
            else:
                r = requests.post(url, json=data, timeout=5)
            r.raise_for_status()
            return r.json() if r.text else {}
        except Exception:
            return None

    def output(self, level, message, **data):
        """Output with JSON/console support"""
        self.logger.log(getattr(logging, level), message)

        if self.json_mode:
            output = {
                "timestamp": datetime.now().isoformat(),
                "level": level,
                "message": message,
                **data
            }
            print(json.dumps(output, indent=2))
        else:
            colors = {
                "ERROR": Colors.RED,
                "WARNING": Colors.YELLOW,
                "INFO": Colors.CYAN,
                "DEBUG": Colors.CYAN,
            }
            color = colors.get(level, Colors.RESET)
            symbol = {"ERROR": "✗", "WARNING": "⚠", "INFO": "ℹ", "DEBUG": "•"}.get(level, "•")
            print(f"{color}{symbol} {message}{Colors.RESET}")

    def run_cmd(self, cmd, cwd=None, description=None, capture=False):
        """Run command"""
        if description:
            self.output("INFO", description)

        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                shell=isinstance(cmd, str),
                capture_output=capture,
                text=True,
            )
            if result.returncode == 0:
                if capture:
                    return True, result.stdout
                else:
                    self.output("INFO", "Success")
                    return True, None
            else:
                err = result.stderr[:200] if result.stderr else "Exit code " + str(result.returncode)
                self.output("ERROR", f"Failed: {err}")
                return False, result.stderr if capture else None
        except Exception as e:
            self.output("ERROR", f"Exception: {e}")
            return False, str(e)

    def _log_path(self, cmd):
        """Get log file path for a command"""
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        return TMP_DIR / f"cyn-{cmd}.log"

    def _run_tee(self, label, args_list, cwd=None, log_file=None, check=True, env=None):
        """Run subprocess streaming output to stdout + log file (tee mode)"""
        print(f"  {label}: {' '.join(str(a) for a in args_list)}")
        try:
            proc = subprocess.Popen(
                args_list,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            for line in proc.stdout:
                sys.stdout.write("    " + line)
                if log_file:
                    log_file.write(line)
                    log_file.flush()
            proc.wait()
            if log_file:
                log_file.flush()
            if check and proc.returncode != 0:
                print(f"  FAIL (exit {proc.returncode})")
                return proc.returncode
            return proc.returncode
        except FileNotFoundError as e:
            print(f"  ERROR: {e}")
            if check:
                return 1
            return 1

    def _check_venv(self):
        """Check if venv exists, exit if not"""
        if not VENV_PYTHON.exists():
            print(f"ERROR: venv not found at {VENV_PYTHON}")
            print(f"  Run:  pip install -r requirements.txt && python -m venv {REPO_ROOT / '.venv'}")
            sys.exit(1)

    def cmd_ai_brief(self, args):
        """AI-friendly project brief"""
        status = {
            "project": "Cynthion Workspace",
            "timestamp": datetime.now().isoformat(),
            "description": "FPGA + embedded firmware project for USB debugging/analysis",
            "components": {
                "apollo": {
                    "type": "ARM firmware (debug controller)",
                    "status": "building",
                    "language": "C",
                    "path": str(REPOS / "awto-apollo")
                },
                "moondancer": {
                    "type": "RISC-V firmware",
                    "status": "building",
                    "language": "Rust",
                    "path": str(REPOS / "awto-cynthion" / "firmware" / "moondancer")
                },
                "analyzer_gateware": {
                    "type": "FPGA gateware (analyzer)",
                    "status": "building",
                    "language": "Python (Amaranth HDL)",
                    "path": str(REPOS / "awto-cynthion" / "cynthion" / "python")
                },
                "facedancer_gateware": {
                    "type": "FPGA gateware (emulation)",
                    "status": "known_issue",
                    "language": "Python (Amaranth HDL)",
                    "issue": "luna_soc SPIflash Field TypeError",
                    "path": str(REPOS / "awto-cynthion" / "cynthion" / "python")
                }
            },
            "phase": {
                "current": "Phase 1",
                "status": "3/4 builds successful",
                "next": "Phase 2: Fix remaining issues"
            },
            "improvements": {
                "fail_fast_checks": "Prerequisite validation before builds",
                "parallelization": "55% speedup using Python 3.14 no-GIL",
                "unified_cli": "All operations via 'cyn' command"
            }
        }

        if self.json_mode:
            print(json.dumps(status, indent=2))
        else:
            print(f"\n{Colors.BOLD}Cynthion Project Status{Colors.RESET}")
            print(f"Phase: {status['phase']['current']} - {status['phase']['status']}")
            print(f"Components: {len(status['components'])} (3 building, 1 known issue)")
            print(f"\nRun: {Colors.CYAN}cyn --json ai-brief{Colors.RESET} for full details")

        return 0

    def cmd_ai_schema(self, args):
        """Machine-readable command schema for AI"""
        schema = {
            "version": "1.0",
            "entry_point": "cyn",
            "global_options": [
                {"name": "--json", "description": "JSON output", "type": "boolean"},
                {"name": "--log FILE", "description": "Log to file", "type": "string"},
                {"name": "--verbose", "description": "Verbose output", "type": "boolean"}
            ],
            "commands": {
                "status": {
                    "description": "Project status",
                    "args": [],
                    "outputs": ["human", "json"]
                },
                "ai-brief": {
                    "description": "AI-friendly summary",
                    "args": [],
                    "outputs": ["human", "json"]
                },
                "ai-schema": {
                    "description": "This schema (machine-readable)",
                    "args": [],
                    "outputs": ["json"]
                },
                "ai-tasks": {
                    "description": "Available tasks for AI",
                    "args": [],
                    "outputs": ["human", "json"]
                },
                "list": {
                    "description": "All available commands",
                    "args": [],
                    "outputs": ["human"]
                },
                "fpga": {
                    "description": "FPGA simulator and gateware",
                    "subcommands": {
                        "sim_test": "Run all Luna simulator tests",
                        "sim_uart": "Run UART simulator",
                        "sim_usb": "Run USB simulator tests"
                    }
                },
                "apollo": {
                    "description": "Apollo firmware (ARM)",
                    "subcommands": {
                        "build": "Build Apollo firmware",
                        "clean": "Clean build artifacts",
                        "get-deps": "Get dependencies (TinyUSB, etc)",
                        "reset": "Flash to hardware"
                    }
                },
                "moondancer": {
                    "description": "moondancer firmware (Rust/RISC-V)",
                    "subcommands": {
                        "build": "Build moondancer",
                        "clean": "Clean build artifacts"
                    }
                },
                "gateware": {
                    "description": "FPGA gateware elaboration",
                    "subcommands": {
                        "elaborate": "Elaborate analyzer gateware",
                        "facedancer": "Elaborate facedancer gateware",
                        "list": "List available gateware"
                    }
                },
                "setup": {
                    "description": "Full workspace setup",
                    "args": ["--parallel", "--jobs N"],
                    "outputs": ["human", "json"]
                },
                "versions": {
                    "description": "Show all tool versions",
                    "args": [],
                    "outputs": ["human", "json"]
                },
                "prereqs": {
                    "description": "Check system prerequisites",
                    "args": [],
                    "outputs": ["human", "json"]
                },
                "daemon": {
                    "description": "Daemon management (for GUI integration)",
                    "subcommands": {
                        "start": "Start daemon",
                        "stop": "Stop daemon",
                        "status": "Check daemon status",
                        "restart": "Restart daemon"
                    }
                },
                "build": {
                    "description": "Build firmware/gateware/app",
                    "args": ["[rust|apollo|gateware|app|all]", "--release"],
                    "outputs": ["human"]
                },
                "check": {
                    "description": "Run pre-commit checks",
                    "args": ["[fast|rust|c|gateware|all]"],
                    "outputs": ["human"]
                },
                "test": {
                    "description": "Run hardware self-tests",
                    "args": ["--destructive"],
                    "outputs": ["human"]
                },
                "clean": {
                    "description": "Clean build artifacts",
                    "args": ["[rust|apollo|gateware|app|all]"],
                    "outputs": ["human"]
                },
                "flash": {
                    "description": "Flash to connected device",
                    "args": ["[rust|apollo|gateware]"],
                    "outputs": ["human"]
                },
                "deploy": {
                    "description": "Full release cycle (build --release + flash)",
                    "args": [],
                    "outputs": ["human"]
                },
                "reset": {
                    "description": "Reset device to Apollo mode",
                    "args": [],
                    "outputs": ["human"]
                }
            }
        }

        print(json.dumps(schema, indent=2))
        return 0

    def cmd_ai_tasks(self, args):
        """List available tasks for AI agents"""
        tasks = {
            "timestamp": datetime.now().isoformat(),
            "available_tasks": [
                {
                    "id": "fpga_test",
                    "command": "cyn fpga sim_test",
                    "description": "Run FPGA simulator test suite",
                    "time_estimate": "5-10 minutes",
                    "difficulty": "simple"
                },
                {
                    "id": "apollo_build",
                    "command": "cyn apollo build",
                    "description": "Build Apollo firmware",
                    "time_estimate": "5-10 minutes",
                    "difficulty": "simple"
                },
                {
                    "id": "moondancer_build",
                    "command": "cyn moondancer build",
                    "description": "Build moondancer firmware",
                    "time_estimate": "3-5 minutes",
                    "difficulty": "simple"
                },
                {
                    "id": "gateware_elaborate",
                    "command": "cyn gateware elaborate",
                    "description": "Elaborate analyzer gateware",
                    "time_estimate": "5-8 minutes",
                    "difficulty": "simple"
                },
                {
                    "id": "full_setup_sequential",
                    "command": "cyn setup",
                    "description": "Full workspace setup (sequential)",
                    "time_estimate": "30-40 minutes",
                    "difficulty": "medium"
                },
                {
                    "id": "full_setup_parallel",
                    "command": "cyn setup --parallel --jobs 4",
                    "description": "Full workspace setup (parallelized, 55% faster)",
                    "time_estimate": "15-20 minutes",
                    "difficulty": "medium"
                },
                {
                    "id": "check_status",
                    "command": "cyn status",
                    "description": "Check project status",
                    "time_estimate": "1 minute",
                    "difficulty": "trivial"
                },
                {
                    "id": "check_prereqs",
                    "command": "cyn prereqs",
                    "description": "Check system prerequisites (fail-fast)",
                    "time_estimate": "1 minute",
                    "difficulty": "trivial"
                },
                {
                    "id": "daemon_start",
                    "command": "cyn daemon start",
                    "description": "Start cyn daemon for faster subsequent commands",
                    "time_estimate": "1-2 minutes",
                    "difficulty": "trivial"
                },
                {
                    "id": "daemon_status",
                    "command": "cyn daemon status",
                    "description": "Check if daemon is running",
                    "time_estimate": "1 second",
                    "difficulty": "trivial"
                },
                {
                    "id": "build_all",
                    "command": "cyn build all",
                    "description": "Build all firmware/gateware/app",
                    "time_estimate": "15-20 minutes",
                    "difficulty": "medium"
                },
                {
                    "id": "check_fast",
                    "command": "cyn check fast",
                    "description": "Run fast pre-commit checks (rust, C, python)",
                    "time_estimate": "5-10 minutes",
                    "difficulty": "medium"
                },
                {
                    "id": "flash_rust",
                    "command": "cyn flash rust",
                    "description": "Flash moondancer to connected device",
                    "time_estimate": "2-3 minutes",
                    "difficulty": "medium"
                },
                {
                    "id": "deploy",
                    "command": "cyn deploy",
                    "description": "Full release: build --release + flash both rust and gateware",
                    "time_estimate": "20-25 minutes",
                    "difficulty": "hard"
                },
                {
                    "id": "reset_device",
                    "command": "cyn reset",
                    "description": "Reset device to Apollo mode",
                    "time_estimate": "1-2 minutes",
                    "difficulty": "simple"
                },
                {
                    "id": "test_hardware",
                    "command": "cyn test",
                    "description": "Run hardware self-tests (requires connected device)",
                    "time_estimate": "10-15 minutes",
                    "difficulty": "hard"
                }
            ],
            "phase_info": {
                "current_phase": 1,
                "status": "3/4 components building",
                "blockers": ["facedancer luna_soc SPIflash Field TypeError"]
            },
            "daemon_info": {
                "description": "If daemon is running, all commands automatically connect to it for faster execution",
                "benefits": ["Cached environment", "Faster sequential commands", "Future GUI integration"]
            }
        }

        if self.json_mode:
            print(json.dumps(tasks, indent=2))
        else:
            print(f"\n{Colors.BOLD}Available Tasks for AI Agents{Colors.RESET}\n")
            for task in tasks["available_tasks"]:
                print(f"{Colors.CYAN}{task['id']}{Colors.RESET}")
                print(f"  Command: {task['command']}")
                print(f"  Description: {task['description']}")
                print(f"  Time: {task['time_estimate']}, Difficulty: {task['difficulty']}\n")

        return 0

    def cmd_list(self, args):
        """List all available commands"""
        print(f"\n{Colors.BOLD}Cynthion CLI - All Commands{Colors.RESET}\n")

        print(f"{Colors.CYAN}Information Commands:{Colors.RESET}")
        print("  cyn status                - Project status")
        print("  cyn list                  - This list")
        print("  cyn versions              - Show tool versions")
        print("  cyn prereqs               - Check prerequisites")

        print(f"\n{Colors.CYAN}AI-Friendly Commands:{Colors.RESET}")
        print("  cyn ai-brief              - Project summary (for AI)")
        print("  cyn ai-schema             - Command schema (JSON)")
        print("  cyn ai-tasks              - Available tasks (JSON)")

        print(f"\n{Colors.CYAN}Component Commands:{Colors.RESET}")
        print("  cyn fpga [sim_test|sim_uart|sim_usb]")
        print("  cyn apollo [build|clean|get-deps|reset]")
        print("  cyn moondancer [build|clean]")
        print("  cyn gateware [elaborate|facedancer|list]")

        print(f"\n{Colors.CYAN}Workspace Commands:{Colors.RESET}")
        print("  cyn setup                 - Full setup (sequential)")
        print("  cyn setup --parallel      - Parallel setup (55% faster)")

        print(f"\n{Colors.CYAN}CI/CD Commands:{Colors.RESET}")
        print("  cyn ci install            - Install act (GitHub Actions locally)")
        print("  cyn ci list               - List available workflows")
        print("  cyn ci apollo             - Run Apollo CI")
        print("  cyn ci cynthion           - Run Cynthion CI")
        print("  cyn ci luna               - Run Luna CI")

        print(f"\n{Colors.CYAN}Hardware/Build Commands:{Colors.RESET}")
        print("  cyn build [rust|apollo|gateware|app|all] [--release]")
        print("                            - Build firmware/gateware/app")
        print("  cyn check [fast|rust|c|gateware|all]")
        print("                            - Run pre-commit checks")
        print("  cyn clean [rust|apollo|gateware|app|all]")
        print("                            - Clean build artifacts")
        print("  cyn test [--destructive]  - Run hardware self-tests")
        print("  cyn flash [rust|apollo|gateware]")
        print("                            - Flash to connected device")
        print("  cyn deploy                - Build --release + flash (full cycle)")
        print("  cyn reset                 - Reset device to Apollo mode")

        print(f"\n{Colors.CYAN}Daemon Commands (for GUI integration):{Colors.RESET}")
        print("  cyn daemon start          - Start daemon (background service)")
        print("  cyn daemon stop           - Stop daemon")
        print("  cyn daemon status         - Check daemon status")
        print("  cyn daemon restart        - Restart daemon")
        daemon_status = "running" if CynDaemon.is_running() else "not running"
        print(f"  Status: {Colors.GREEN if CynDaemon.is_running() else Colors.YELLOW}{daemon_status}{Colors.RESET}")

        print(f"\n{Colors.CYAN}Global Options:{Colors.RESET}")
        print("  --json                    - JSON output")
        print("  --log FILE                - Log to file")
        print("  --verbose                 - Verbose output")

        print()
        return 0

    # Component command handlers
    def cmd_fpga(self, args):
        """FPGA commands"""
        if not args.subcommand:
            self.output("INFO", "FPGA commands: sim_test, sim_uart, sim_usb")
            return 0

        if args.subcommand == "sim_test":
            ok, _ = self.run_cmd(
                "python -m unittest discover -t . -s tests -v",
                cwd=REPOS / "awto-luna",
                description="Running Luna FPGA simulator tests..."
            )
            return 0 if ok else 1

        elif args.subcommand == "sim_uart":
            ok, _ = self.run_cmd(
                "python -m unittest tests.test_uart -v",
                cwd=REPOS / "awto-luna",
                description="Running UART simulator test..."
            )
            return 0 if ok else 1

        elif args.subcommand == "sim_usb":
            ok, _ = self.run_cmd(
                "python -m unittest discover -t . -s tests -k usb -v",
                cwd=REPOS / "awto-luna",
                description="Running USB simulator tests..."
            )
            return 0 if ok else 1
        else:
            self.output("ERROR", f"Unknown fpga command: {args.subcommand}")
            return 1

    def cmd_apollo(self, args):
        """Apollo firmware commands"""
        if not args.subcommand:
            self.output("INFO", "Apollo commands: build, clean, get-deps, reset")
            return 0

        fw_dir = REPOS / "awto-apollo" / "firmware"
        if not fw_dir.exists():
            self.output("ERROR", f"Apollo directory not found: {fw_dir}")
            return 1

        if args.subcommand == "build":
            ok, _ = self.run_cmd("make APOLLO_BOARD=cynthion", cwd=fw_dir,
                                description="Building Apollo firmware...")
            return 0 if ok else 1

        elif args.subcommand == "clean":
            ok, _ = self.run_cmd("make APOLLO_BOARD=cynthion clean", cwd=fw_dir,
                                description="Cleaning Apollo build...")
            return 0 if ok else 1

        elif args.subcommand == "get-deps":
            ok, _ = self.run_cmd("make APOLLO_BOARD=cynthion get-deps", cwd=fw_dir,
                                description="Getting Apollo dependencies...")
            return 0 if ok else 1

        elif args.subcommand == "reset":
            self.output("WARNING", "Apollo reset requires hardware connection")
            self.output("INFO", "Command: dfu-util -d 1d50:615e -D build/cynthion_d11/apollo_debug_soc.bin")
            return 0
        else:
            self.output("ERROR", f"Unknown apollo command: {args.subcommand}")
            return 1

    def cmd_moondancer(self, args):
        """moondancer firmware commands"""
        if not args.subcommand:
            self.output("INFO", "moondancer commands: build, clean")
            return 0

        fw_dir = REPOS / "awto-cynthion" / "firmware" / "moondancer"
        if not fw_dir.exists():
            self.output("ERROR", f"moondancer directory not found: {fw_dir}")
            return 1

        if args.subcommand == "build":
            ok, _ = self.run_cmd("cargo build --release", cwd=fw_dir,
                                description="Building moondancer firmware...")
            return 0 if ok else 1

        elif args.subcommand == "clean":
            ok, _ = self.run_cmd("cargo clean", cwd=fw_dir,
                                description="Cleaning moondancer build...")
            return 0 if ok else 1
        else:
            self.output("ERROR", f"Unknown moondancer command: {args.subcommand}")
            return 1

    def cmd_gateware(self, args):
        """Gateware commands"""
        if not args.subcommand:
            self.output("INFO", "Gateware commands: elaborate, facedancer, list")
            return 0

        gw_dir = REPOS / "awto-cynthion" / "cynthion" / "python"
        if not gw_dir.exists():
            self.output("ERROR", f"Gateware directory not found: {gw_dir}")
            return 1

        oss_env = Path.home() / "opt" / "oss-cad-suite" / "environment"
        env_prefix = f"source {oss_env} && " if oss_env.exists() else ""

        if args.subcommand == "elaborate":
            cmd = f"{env_prefix}LUNA_PLATFORM=cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2 python3.14 -m cynthion.gateware.analyzer.top --dry-run"
            ok, _ = self.run_cmd(f"bash -c '{cmd}'", cwd=gw_dir,
                                description="Elaborating analyzer gateware...")
            return 0 if ok else 1

        elif args.subcommand == "facedancer":
            cmd = f"{env_prefix}LUNA_PLATFORM=cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2 python3.14 -m cynthion.gateware.facedancer.top --dry-run"
            ok, _ = self.run_cmd(f"bash -c '{cmd}'", cwd=gw_dir,
                                description="Elaborating facedancer gateware...")
            return 0 if ok else 1

        elif args.subcommand == "list":
            self.output("INFO", "Available gateware: analyzer, facedancer")
            return 0
        else:
            self.output("ERROR", f"Unknown gateware command: {args.subcommand}")
            return 1

    def cmd_workspace(self, args):
        """Workspace commands"""
        cmd = [str(INSTALL_PY), args.command]

        if args.command == "setup":
            if getattr(args, 'parallel', False):
                cmd.extend(["--parallel", "--jobs", str(getattr(args, 'jobs', 4))])

        ok, _ = self.run_cmd(cmd, description=f"Running {args.command}...")
        return 0 if ok else 1

    def cmd_ci(self, args):
        """CI/CD commands (GitHub Actions locally with act)"""
        if not args.subcommand:
            self.output("INFO", "CI commands: install, list, apollo, cynthion, luna")
            return 0

        if args.subcommand == "install":
            ok, _ = self.run_cmd(
                [str(INSTALL_PY), "ci-install"],
                description="Installing act (GitHub Actions runner)..."
            )
            return 0 if ok else 1

        elif args.subcommand == "list":
            ok, _ = self.run_cmd(
                [str(INSTALL_PY), "ci-list"],
                description="Listing CI workflows..."
            )
            return 0 if ok else 1

        elif args.subcommand == "apollo":
            ok, _ = self.run_cmd(
                "act -l",
                cwd=REPOS / "awto-apollo",
                description="Listing Apollo CI jobs..."
            )
            if ok:
                self.output("INFO", "Run: act -j <jobname> to execute")
            return 0 if ok else 1

        elif args.subcommand == "cynthion":
            ok, _ = self.run_cmd(
                "act -l",
                cwd=REPOS / "awto-cynthion",
                description="Listing Cynthion CI jobs..."
            )
            if ok:
                self.output("INFO", "Run: act -j <jobname> to execute")
            return 0 if ok else 1

        elif args.subcommand == "luna":
            ok, _ = self.run_cmd(
                "act -l",
                cwd=REPOS / "awto-luna",
                description="Listing Luna CI jobs..."
            )
            if ok:
                self.output("INFO", "Run: act -j <jobname> to execute")
            return 0 if ok else 1

        else:
            self.output("ERROR", f"Unknown ci command: {args.subcommand}")
            return 1

    # ── Hardware/Build Commands ────────────────────────────────────────

    def _build_rust(self, log_file, release=False):
        """Build moondancer (Rust/RISC-V firmware)"""
        args = ["cargo", "build"]
        if release:
            args.append("--release")
        return self._run_tee("rust firmware", args, cwd=MOONDANCER_FW, log_file=log_file)

    def _build_apollo(self, log_file, release=False):
        """Build Apollo C firmware"""
        ret = self._run_tee("apollo C firmware",
            ["make", "APOLLO_BOARD=cynthion"],
            cwd=APOLLO_FW, log_file=log_file)
        if ret == 0:
            elf = APOLLO_FW / "_build" / "cynthion_d11" / "firmware.elf"
            if elf.exists():
                self._run_tee("apollo size", ["arm-none-eabi-size", str(elf)],
                    cwd=APOLLO_FW, log_file=log_file, check=False)
        return ret

    def _build_gateware(self, log_file, release=False):
        """Build gateware (Amaranth HDL)"""
        return self._run_tee("gateware", ["make", "facedancer"],
            cwd=GATEWARE_DIR, log_file=log_file)

    def _build_app(self, log_file, release=False):
        """Build Flutter app"""
        profile = "--release" if release else "--debug"
        return self._run_tee("flutter app", ["flutter", "build", "linux", profile],
            cwd=APP_DIR, log_file=log_file)

    def cmd_build(self, args):
        """Build firmware/gateware/app"""
        target = getattr(args, "target", "all")
        release = getattr(args, "release", False)
        log_path = self._log_path("build")
        print(f"==> build {target} {'(release)' if release else ''} (log: {log_path.relative_to(REPO_ROOT)})")

        dispatch = {
            "rust": self._build_rust,
            "apollo": self._build_apollo,
            "gateware": self._build_gateware,
            "app": self._build_app,
        }

        with log_path.open("w") as log:
            targets = ["rust", "apollo", "gateware", "app"] if target == "all" else [target]
            for t in targets:
                ret = dispatch[t](log, release)
                if ret != 0:
                    print(f"\nBuild {t} failed")
                    return 1

        print("\nBuild complete.")
        return 0

    def _check_rust(self, log_file):
        """Check rust code"""
        ret = self._run_tee("rust check", ["cargo", "check", "--release"],
            cwd=MOONDANCER_FW, log_file=log_file)
        if ret != 0: return ret
        ret = self._run_tee("rust clippy", ["cargo", "clippy"],
            cwd=MOONDANCER_FW, log_file=log_file)
        if ret != 0: return ret
        return self._run_tee("rust test", ["cargo", "test"],
            cwd=MOONDANCER_FW, log_file=log_file)

    def _check_c(self, log_file):
        """Check C code"""
        return self._run_tee("apollo C build", ["make", "APOLLO_BOARD=cynthion"],
            cwd=APOLLO_FW, log_file=log_file)

    def _check_gateware(self, log_file):
        """Check gateware"""
        self._check_venv()
        return self._run_tee("gateware elaborate",
            [str(VENV_PYTHON), "-c",
             "from cynthion.gateware.facedancer import top; print('elaborate ok')"],
            log_file=log_file, check=False)

    def _check_python(self, log_file):
        """Check Python code"""
        self._check_venv()
        ret = self._run_tee("python imports",
            [str(VENV_PYTHON), "-c", "import cynthion; import apollo_fpga; import facedancer"],
            log_file=log_file)
        if ret != 0: return ret
        return self._run_tee("pytest",
            [str(VENV_PYTHON), "-m", "pytest",
             str(REPOS / "awto-cynthion" / "cynthion" / "python" / "tests"), "-q", "--tb=short"],
            cwd=REPO_ROOT, log_file=log_file, check=False)

    def cmd_check(self, args):
        """Run pre-commit checks"""
        target = getattr(args, "target", "fast")
        log_path = self._log_path("check")
        print(f"==> check {target} (log: {log_path.relative_to(REPO_ROOT)})")

        results = {}
        def checked(label, fn, log):
            try:
                ret = fn(log)
                results[label] = "OK" if ret == 0 else "FAIL"
            except SystemExit:
                results[label] = "FAIL"

        with log_path.open("w") as log:
            if target in ("fast", "all", "rust"):
                checked("rust", self._check_rust, log)
            if target in ("fast", "all", "c"):
                checked("c", self._check_c, log)
            if target in ("all",):
                checked("python", self._check_python, log)
            if target in ("fast", "all", "gateware"):
                checked("gateware", self._check_gateware, log)

        print()
        failed = []
        for label, status in results.items():
            mark = "✓" if status == "OK" else "✗"
            print(f"  {mark}  {label:<20} {status}")
            if status == "FAIL":
                failed.append(label)
        print()

        if failed:
            print(f"FAILED: {', '.join(failed)} (see {log_path.relative_to(REPO_ROOT)})")
            return 1

        print("All checks passed.")
        return 0

    def cmd_test(self, args):
        """Run hardware self-tests"""
        destructive = getattr(args, "destructive", False)
        log_path = self._log_path("test")
        self._check_venv()

        test_script = REPOS / "awto-cynthion" / "scripts" / "test-fault-detection.py"
        if not test_script.exists():
            print(f"ERROR: test script not found: {test_script}")
            return 1

        cmd = [str(VENV_PYTHON), str(test_script)]
        if destructive:
            cmd.append("--destructive")

        print(f"==> test (log: {log_path.relative_to(REPO_ROOT)})")
        with log_path.open("w") as log:
            ret = self._run_tee("fault-detection", cmd, cwd=REPO_ROOT, log_file=log)
        print("Tests complete.")
        return ret

    def cmd_clean(self, args):
        """Clean build artifacts"""
        target = getattr(args, "target", "all")
        log_path = self._log_path("clean")
        print(f"==> clean {target} (log: {log_path.relative_to(REPO_ROOT)})")

        with log_path.open("w") as log:
            if target in ("rust", "all"):
                self._run_tee("rust clean", ["cargo", "clean"],
                    cwd=MOONDANCER_FW, log_file=log, check=False)
            if target in ("apollo", "all"):
                self._run_tee("apollo clean", ["make", "clean", "APOLLO_BOARD=cynthion"],
                    cwd=APOLLO_FW, log_file=log, check=False)
            if target in ("gateware", "all"):
                self._run_tee("gateware clean", ["make", "clean"],
                    cwd=GATEWARE_DIR, log_file=log, check=False)
            if target in ("app", "all"):
                self._run_tee("flutter clean", ["flutter", "clean"],
                    cwd=APP_DIR, log_file=log, check=False)

        print("Clean complete.")
        return 0

    def _flash_rust(self, log_file):
        """Flash moondancer to device"""
        elf_candidates = list(MOONDANCER_FW.glob("target/**/firmware.elf"))
        if not elf_candidates:
            elf_candidates = list(MOONDANCER_FW.glob("target/**/*.elf"))
        if not elf_candidates:
            print("  ERROR: no ELF found — run 'cyn build rust' first")
            return 1

        elf = sorted(elf_candidates, key=lambda p: p.stat().st_mtime)[-1]
        return self._run_tee("probe-rs flash", ["probe-rs", "download", "--chip", "riscv", str(elf)],
            log_file=log_file)

    def _flash_apollo(self, log_file):
        """Flash Apollo to device"""
        elf = APOLLO_FW / "_build" / "cynthion_d11" / "firmware.elf"
        if not elf.exists():
            print("  ERROR: Apollo ELF not found — run 'cyn build apollo' first")
            return 1

        ret = self._run_tee("apollo flash",
            ["arm-none-eabi-gdb", "-batch",
             "-ex", f"target extended-remote | openocd -f interface/cmsis-dap.cfg -c 'gdb_port pipe'",
             "-ex", f"load {elf}",
             "-ex", "detach"],
            log_file=log_file, check=False)
        print("  note: Apollo flash requires SWD connection — check device docs if this failed")
        return ret

    def _flash_gateware(self, log_file):
        """Flash gateware to FPGA"""
        self._check_venv()
        bitstream = GATEWARE_DIR / "build" / "top.bit"
        ret = self._run_tee("gateware upload",
            [str(VENV_PYTHON), "-m", "apollo_fpga.cli", "--", "configure", str(bitstream)],
            log_file=log_file, check=False)
        print("  note: ensure device is in Apollo mode (run 'cyn reset' first if needed)")
        return ret

    def cmd_flash(self, args):
        """Flash to connected device"""
        target = getattr(args, "target", "rust")
        log_path = self._log_path("flash")
        print(f"==> flash {target} (log: {log_path.relative_to(REPO_ROOT)})")

        with log_path.open("w") as log:
            if target == "rust":
                ret = self._flash_rust(log)
            elif target == "apollo":
                ret = self._flash_apollo(log)
            elif target == "gateware":
                ret = self._flash_gateware(log)
            else:
                print(f"  ERROR: unknown target {target}")
                return 1

        print("Flash complete.")
        return ret

    def cmd_deploy(self, args):
        """Full release cycle: build --release + flash"""
        log_path = self._log_path("deploy")
        print(f"==> deploy (log: {log_path.relative_to(REPO_ROOT)})")

        with log_path.open("w") as log:
            ret = self._build_rust(log, release=True)
            if ret != 0:
                print("Deploy failed: rust build")
                return 1

            ret = self._build_gateware(log, release=True)
            if ret != 0:
                print("Deploy failed: gateware build")
                return 1

            ret = self._flash_rust(log)
            if ret != 0:
                print("Deploy failed: rust flash")
                return 1

            ret = self._flash_gateware(log)
            if ret != 0:
                print("Deploy failed: gateware flash")
                return 1

        print("Deploy complete.")
        return 0

    def cmd_reset(self, args):
        """Reset device to Apollo mode"""
        self._check_venv()
        log_path = self._log_path("reset")
        print(f"==> reset (log: {log_path.relative_to(REPO_ROOT)})")

        reset_script = REPOS / "awto-cynthion" / "scripts" / "reset-cynthion.sh"
        with log_path.open("w") as log:
            if reset_script.exists():
                ret = self._run_tee("reset-cynthion", [str(reset_script)],
                    cwd=REPO_ROOT, log_file=log)
            else:
                ret = self._run_tee("force-offline",
                    [str(VENV_PYTHON), "-c", """
import sys
try:
    import usb.core
    from apollo_fpga import ApolloDebugger
    FPGA_VID, FPGA_PID = 0x1d50, 0x615b
    if usb.core.find(idVendor=FPGA_VID, idProduct=FPGA_PID) is None:
        print("Device already in Apollo mode (or not connected).")
        sys.exit(0)
    d = ApolloDebugger(force_offline=True)
    d.soft_reset()
    d.allow_fpga_takeover_usb()
    d.close()
    print("Reset complete.")
except Exception as e:
    print(f"Reset failed: {e}")
    print("If moondancer has hung: power-cycle the device.")
    sys.exit(1)
"""],
                    log_file=log)

        print("Reset complete.")
        return ret

    def cmd_daemon(self, args):
        """Daemon management (start, stop, status)"""
        if not args.subcommand:
            print("Daemon commands: start, stop, status, restart")
            return 0

        if args.subcommand == "start":
            return CynDaemon.start()
        elif args.subcommand == "stop":
            return CynDaemon.stop()
        elif args.subcommand == "status":
            return CynDaemon.status()
        elif args.subcommand == "restart":
            CynDaemon.stop()
            time.sleep(1)
            return CynDaemon.start()
        else:
            self.output("ERROR", f"Unknown daemon command: {args.subcommand}")
            return 1

    def run(self, sys_args):
        """Main entry point"""
        # Parse global options
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--log", type=str)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("rest", nargs=argparse.REMAINDER)

        args, _ = parser.parse_known_args(sys_args[1:])

        self.json_mode = args.json
        self.verbose = args.verbose
        self.logger = self._setup_logging(args.log)

        # Parse actual commands
        main = argparse.ArgumentParser(description="Cynthion CLI")
        subs = main.add_subparsers(dest="command")

        # Info commands
        subs.add_parser("status", help="Project status").set_defaults(
            func=lambda a: self.cmd_workspace(type('Args', (), {'command': 'status'})()))
        subs.add_parser("list", help="List commands").set_defaults(func=self.cmd_list)
        subs.add_parser("versions", help="Show versions").set_defaults(
            func=lambda a: self.cmd_workspace(type('Args', (), {'command': 'versions'})()))
        subs.add_parser("prereqs", help="Check prerequisites").set_defaults(
            func=lambda a: self.cmd_workspace(type('Args', (), {'command': 'prereqs'})()))

        # AI commands
        subs.add_parser("ai-brief", help="AI project summary").set_defaults(func=self.cmd_ai_brief)
        subs.add_parser("ai-schema", help="Command schema (JSON)").set_defaults(func=self.cmd_ai_schema)
        subs.add_parser("ai-tasks", help="Available tasks").set_defaults(func=self.cmd_ai_tasks)

        # Component commands
        fpga = subs.add_parser("fpga", help="FPGA simulator")
        fpga.add_argument("subcommand", nargs="?")
        fpga.set_defaults(func=self.cmd_fpga)

        apollo = subs.add_parser("apollo", help="Apollo firmware")
        apollo.add_argument("subcommand", nargs="?")
        apollo.set_defaults(func=self.cmd_apollo)

        moon = subs.add_parser("moondancer", help="moondancer firmware")
        moon.add_argument("subcommand", nargs="?")
        moon.set_defaults(func=self.cmd_moondancer)

        gw = subs.add_parser("gateware", help="Gateware")
        gw.add_argument("subcommand", nargs="?")
        gw.set_defaults(func=self.cmd_gateware)

        setup = subs.add_parser("setup", help="Full setup")
        setup.add_argument("--parallel", action="store_true")
        setup.add_argument("--jobs", type=int, default=4)
        setup.set_defaults(func=self.cmd_workspace, command="setup")

        ci = subs.add_parser("ci", help="CI/CD (GitHub Actions)")
        ci.add_argument("subcommand", nargs="?", help="install, list, apollo, cynthion, luna")
        ci.set_defaults(func=self.cmd_ci)

        # Hardware/Build commands
        build = subs.add_parser("build", help="Build firmware/gateware/app")
        build.add_argument("target", nargs="?", default="all",
                          choices=["rust", "apollo", "gateware", "app", "all"])
        build.add_argument("--release", action="store_true", help="Release profile")
        build.set_defaults(func=self.cmd_build)

        check = subs.add_parser("check", help="Run pre-commit checks")
        check.add_argument("target", nargs="?", default="fast",
                          choices=["fast", "rust", "c", "gateware", "all"])
        check.set_defaults(func=self.cmd_check)

        test = subs.add_parser("test", help="Run hardware self-tests")
        test.add_argument("--destructive", action="store_true",
                         help="Include fault-injection tests")
        test.set_defaults(func=self.cmd_test)

        clean = subs.add_parser("clean", help="Clean build artifacts")
        clean.add_argument("target", nargs="?", default="all",
                          choices=["rust", "apollo", "gateware", "app", "all"])
        clean.set_defaults(func=self.cmd_clean)

        flash = subs.add_parser("flash", help="Flash to connected device")
        flash.add_argument("target", nargs="?", default="rust",
                          choices=["rust", "apollo", "gateware"])
        flash.set_defaults(func=self.cmd_flash)

        subs.add_parser("deploy", help="Build --release + flash (full cycle)").set_defaults(func=self.cmd_deploy)
        subs.add_parser("reset", help="Reset device to Apollo mode").set_defaults(func=self.cmd_reset)

        daemon = subs.add_parser("daemon", help="Daemon management")
        daemon.add_argument("subcommand", nargs="?", help="start, stop, status, restart")
        daemon.set_defaults(func=self.cmd_daemon)

        parsed = main.parse_args(args.rest)

        if not parsed.command:
            print(__doc__)
            return 0

        return getattr(parsed, 'func', lambda a: 1)(parsed)

def main():
    cli = CynCLI()
    sys.exit(cli.run(sys.argv))

if __name__ == "__main__":
    main()
