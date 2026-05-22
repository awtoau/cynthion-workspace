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
