#!/usr/bin/env python3
"""
Cynthion Unified CLI - Target-Based Architecture

Design: cyn <target> <verb> [options]

Targets:
  riscv                   - moondancer RISC-V firmware
  apollo                  - Apollo ARM debug firmware
  fpga                    - FPGA gateware (Amaranth HDL)
  app                     - Flutter application

Verbs (per target):
  build [--release]       - Build target
  flash                   - Flash to connected device
  check                   - Run linters/static checks
  test [--destructive]    - Run tests (hardware required)
  clean                   - Clean build artifacts

Meta-Commands (all targets):
  cyn build [--release]   - Build all
  cyn flash               - Flash all
  cyn check               - Check all
  cyn test [--destructive]- Test all
  cyn clean               - Clean all

Device Management:
  cyn deploy [--release]  - Build --release + flash riscv + fpga
  cyn reset               - Reset device to Apollo mode
  cyn monitor             - Live device monitoring (stub)

Workspace:
  cyn setup [--parallel]  - Full setup
  cyn status              - Project status
  cyn versions            - Show tool versions
  cyn prereqs             - Check prerequisites

CI/CD:
  cyn ci [install|list|apollo|cynthion|luna]

Daemon:
  cyn daemon [start|stop|status|restart]

AI/Discovery:
  cyn ai-brief            - AI-friendly summary
  cyn ai-schema           - Command schema (JSON)
  cyn ai-tasks            - Available tasks (JSON)
  cyn list                - All available commands

Global Options:
  --json                  - JSON output (AI-friendly)
  --log FILE              - Log to file
  --verbose               - Verbose output
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

def _resolve_repos_root() -> Path:
    """Find the awtoau repo root on this machine."""
    env_root = os.getenv("CYN_REPOS_ROOT")
    if env_root:
        base = Path(env_root).expanduser().resolve()
        if (base / "awto-cynthion").exists() and (base / "awto-apollo").exists():
            return base
        print(
            f"WARNING: CYN_REPOS_ROOT={base} missing awto-cynthion and/or awto-apollo; "
            "falling back to auto-detection"
        )

    candidates = [
        REPO_ROOT.parent / "awtoau",
        Path.home() / "git" / "awtoau",
    ]
    for base in candidates:
        if (base / "awto-cynthion").exists() and (base / "awto-apollo").exists():
            return base
    # Keep default behavior if repos are not checked out yet.
    return candidates[-1]

REPOS = _resolve_repos_root()
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
            "version": "2.0",
            "architecture": "target-based",
            "entry_point": "cyn",
            "global_options": [
                {"name": "--json", "description": "JSON output", "type": "boolean"},
                {"name": "--log FILE", "description": "Log to file", "type": "string"},
                {"name": "--verbose", "description": "Verbose output", "type": "boolean"}
            ],
            "targets": {
                "riscv": {
                    "description": "moondancer RISC-V firmware",
                    "verbs": {
                        "build": {"args": ["--release"], "description": "Build RISC-V firmware"},
                        "flash": {"args": [], "description": "Flash to connected device"},
                        "check": {"args": [], "description": "Run cargo check/clippy/test"},
                        "test": {"args": ["--destructive"], "description": "Run tests"},
                        "clean": {"args": [], "description": "Clean build artifacts"}
                    }
                },
                "apollo": {
                    "description": "Apollo ARM debug firmware",
                    "verbs": {
                        "build": {"args": ["--release"], "description": "Build Apollo firmware"},
                        "flash": {"args": [], "description": "Flash via USB DFU (Saturn-V runtime)"},
                        "check": {"args": [], "description": "Run build verification"},
                        "test": {"args": ["--destructive"], "description": "Run tests"},
                        "clean": {"args": [], "description": "Clean build artifacts"}
                    }
                },
                "fpga": {
                    "description": "FPGA gateware (Amaranth HDL)",
                    "verbs": {
                        "build": {"args": ["--release"], "description": "Build gateware"},
                        "flash": {"args": [], "description": "Flash gateware to FPGA"},
                        "check": {"args": [], "description": "Elaborate gateware (syntax check)"},
                        "test": {"args": [], "description": "N/A for FPGA"},
                        "clean": {"args": [], "description": "Clean build artifacts"}
                    }
                },
                "app": {
                    "description": "Flutter application",
                    "verbs": {
                        "build": {"args": ["--release"], "description": "Build Flutter app"},
                        "flash": {"args": [], "description": "N/A for app"},
                        "check": {"args": [], "description": "N/A for app"},
                        "test": {"args": ["--destructive"], "description": "Run tests"},
                        "clean": {"args": [], "description": "Clean build artifacts"}
                    }
                }
            },
            "meta_commands": {
                "build": {"args": ["--release"], "description": "Build all components"},
                "flash": {"args": [], "description": "Flash all components"},
                "check": {"args": [], "description": "Check all components"},
                "test": {"args": ["--destructive"], "description": "Test all components"},
                "clean": {"args": [], "description": "Clean all components"}
            },
            "device_commands": {
                "deploy": {"args": ["--release"], "description": "Build --release + flash riscv + fpga"},
                "reset": {"args": [], "description": "Reset device to Apollo mode"},
                "monitor": {"args": [], "description": "Live device monitoring (stub, apollod pending)"}
            },
            "workspace_commands": {
                "setup": {"args": ["--parallel", "--jobs N"], "description": "Full setup"},
                "status": {"args": [], "description": "Project status"},
                "versions": {"args": [], "description": "Show tool versions"},
                "prereqs": {"args": [], "description": "Check prerequisites"}
            },
            "ci_commands": {
                "ci": {"subcommands": ["install", "list", "apollo", "cynthion", "luna"]}
            },
            "daemon_commands": {
                "daemon": {"subcommands": ["start", "stop", "status", "restart"]}
            },
            "ai_commands": {
                "ai-brief": {"args": [], "description": "AI-friendly summary"},
                "ai-schema": {"args": [], "description": "This schema (JSON)"},
                "ai-tasks": {"args": [], "description": "Available tasks for AI"},
                "list": {"args": [], "description": "List all commands"}
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
                    "id": "riscv_build",
                    "command": "cyn riscv build",
                    "description": "Build moondancer RISC-V firmware",
                    "time_estimate": "3-5 minutes",
                    "difficulty": "simple"
                },
                {
                    "id": "apollo_build",
                    "command": "cyn apollo build",
                    "description": "Build Apollo ARM firmware",
                    "time_estimate": "5-10 minutes",
                    "difficulty": "simple"
                },
                {
                    "id": "fpga_build",
                    "command": "cyn fpga build",
                    "description": "Build FPGA gateware (Amaranth)",
                    "time_estimate": "5-8 minutes",
                    "difficulty": "simple"
                },
                {
                    "id": "app_build",
                    "command": "cyn app build",
                    "description": "Build Flutter application",
                    "time_estimate": "5-10 minutes",
                    "difficulty": "simple"
                },
                {
                    "id": "riscv_check",
                    "command": "cyn riscv check",
                    "description": "Check moondancer (cargo check, clippy, test)",
                    "time_estimate": "2-3 minutes",
                    "difficulty": "simple"
                },
                {
                    "id": "apollo_check",
                    "command": "cyn apollo check",
                    "description": "Check Apollo firmware build",
                    "time_estimate": "5-10 minutes",
                    "difficulty": "simple"
                },
                {
                    "id": "fpga_check",
                    "command": "cyn fpga check",
                    "description": "Check gateware elaboration",
                    "time_estimate": "3-5 minutes",
                    "difficulty": "simple"
                },
                {
                    "id": "build_all",
                    "command": "cyn build",
                    "description": "Build all components (meta-command)",
                    "time_estimate": "15-20 minutes",
                    "difficulty": "medium"
                },
                {
                    "id": "check_all",
                    "command": "cyn check",
                    "description": "Check all components (meta-command)",
                    "time_estimate": "5-10 minutes",
                    "difficulty": "medium"
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
                    "id": "flash_riscv",
                    "command": "cyn riscv flash",
                    "description": "Flash moondancer to connected device",
                    "time_estimate": "2-3 minutes",
                    "difficulty": "medium"
                },
                {
                    "id": "flash_fpga",
                    "command": "cyn fpga flash",
                    "description": "Flash gateware to FPGA",
                    "time_estimate": "2-3 minutes",
                    "difficulty": "medium"
                },
                {
                    "id": "deploy",
                    "command": "cyn deploy",
                    "description": "Full release: build --release + flash riscv + fpga",
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
        print(f"\n{Colors.BOLD}Cynthion CLI - Target-Based Architecture{Colors.RESET}\n")

        print(f"{Colors.CYAN}Information Commands:{Colors.RESET}")
        print("  cyn status                - Project status")
        print("  cyn list                  - This list")
        print("  cyn versions              - Show tool versions")
        print("  cyn prereqs               - Check prerequisites")

        print(f"\n{Colors.CYAN}AI-Friendly Commands:{Colors.RESET}")
        print("  cyn ai-brief              - Project summary (for AI)")
        print("  cyn ai-schema             - Command schema (JSON)")
        print("  cyn ai-tasks              - Available tasks (JSON)")

        print(f"\n{Colors.CYAN}Target-Based Commands (cyn <target> <verb> [options]):{Colors.RESET}")
        print("\n  Targets:")
        print("    riscv                   - moondancer RISC-V firmware")
        print("    apollo                  - Apollo ARM debug firmware")
        print("    fpga                    - FPGA gateware (Amaranth HDL)")
        print("    app                     - Flutter application")
        print("\n  Verbs (for each target):")
        print("    build [--release]       - Build the target")
        print("    flash                   - Flash to connected device")
        print("    check                   - Run linters/static checks")
        print("    test [--destructive]    - Run tests (hardware required)")
        print("    clean                   - Clean build artifacts")
        print("\n  Examples:")
        print("    cyn riscv build --release")
        print("    cyn apollo flash")
        print("    cyn fpga check")

        print(f"\n{Colors.CYAN}Meta-Commands (all targets):{Colors.RESET}")
        print("  cyn build [--release]     - Build all components")
        print("  cyn flash                 - Flash all components")
        print("  cyn check                 - Check all components")
        print("  cyn clean                 - Clean all components")
        print("  cyn test [--destructive]  - Test all components")

        print(f"\n{Colors.CYAN}Device Management:{Colors.RESET}")
        print("  cyn deploy [--release]    - Build --release + flash riscv + fpga")
        print("  cyn reset                 - Reset device to Apollo mode")
        print("  cyn monitor               - Live device monitoring (stub)")

        print(f"\n{Colors.CYAN}Workspace Commands:{Colors.RESET}")
        print("  cyn setup                 - Full setup (sequential)")
        print("  cyn setup --parallel [--jobs N]")
        print("                            - Parallel setup (55% faster)")

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
        # Set up OSS CAD Suite in environment
        env = os.environ.copy()
        oss_cad_suite = Path.home() / "opt" / "oss-cad-suite" / "bin"
        if oss_cad_suite.exists():
            env["PATH"] = f"{oss_cad_suite}:{env.get('PATH', '')}"

        for target in ["analyzer", "selftest", "facedancer"]:
            ret = self._run_tee(f"gateware {target}", ["make", target],
                cwd=GATEWARE_DIR, log_file=log_file, env=env)
            if ret != 0: return ret
        return 0

    def _build_app(self, log_file, release=False):
        """Build Flutter app"""
        profile = "--release" if release else "--debug"
        return self._run_tee("flutter app", ["flutter", "build", "linux", profile],
            cwd=APP_DIR, log_file=log_file)


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
        for target in ["analyzer", "selftest", "facedancer"]:
            ret = self._run_tee(f"gateware {target} elaborate",
                [str(VENV_PYTHON), "-c",
                 f"from cynthion.gateware.{target} import top; print('elaborate ok')"],
                log_file=log_file, check=False)
            if ret != 0: return ret
        return 0

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


    def _flash_rust(self, log_file):
        """Flash moondancer via Apollo (SPI flash + FPGA configure)."""
        # Match moondancer's documented runner flow (.cargo/cynthion.sh):
        # 1) ELF -> BIN, 2) apollo flash-program, 3) apollo configure bitstream.
        cynthion_repo = MOONDANCER_FW.parents[1]
        bitstream = cynthion_repo / "cynthion" / "python" / "build" / "facedancer.bit"
        if not bitstream.exists():
            print("  ERROR: facedancer bitstream not found — build gateware first")
            print(f"         expected: {bitstream}")
            return 1

        image = MOONDANCER_FW / "target" / "moondancer.bin"
        image.parent.mkdir(parents=True, exist_ok=True)

        ret = self._run_tee("moondancer objcopy",
            ["cargo", "objcopy", "--release", "--bin", "moondancer", "--", "-Obinary", str(image)],
            cwd=MOONDANCER_FW,
            log_file=log_file,
            check=False)
        if ret != 0:
            print("  ERROR: failed to create moondancer binary image")
            return ret

        ret = self._run_tee("apollo flash-program",
            ["apollo", "flash-program", "--offset", "0x000b0000", str(image)],
            cwd=MOONDANCER_FW,
            log_file=log_file,
            check=False)
        if ret != 0:
            print("  ERROR: apollo flash-program failed")
            return ret

        ret = self._run_tee("apollo configure",
            ["apollo", "configure", str(bitstream)],
            cwd=MOONDANCER_FW,
            log_file=log_file,
            check=False)
        if ret != 0:
            print("  ERROR: apollo configure failed")
            return ret

        return 0

    def _flash_apollo(self, log_file):
        """Flash Apollo firmware via USB DFU."""
        image = APOLLO_FW / "_build" / "cynthion_d11" / "firmware.bin"
        if not image.exists():
            print("  ERROR: Apollo firmware image not found — run 'cyn build apollo' first")
            return 1

        ret = self._run_tee(
            "apollo flash (dfu)",
            ["make", "APOLLO_BOARD=cynthion", "dfu"],
            cwd=APOLLO_FW,
            log_file=log_file,
            check=False,
        )
        print("  note: DFU expects Apollo runtime VID:PID (1d50:615c or 1209:0010).")
        return ret

    def _flash_gateware(self, log_file):
        """Flash gateware to FPGA"""
        self._check_venv()
        bitstream = GATEWARE_DIR / "build" / "top.bit"
        ret = self._run_tee(
            "gateware upload",
            ["apollo", "configure", str(bitstream)],
            log_file=log_file, check=False)
        print("  note: ensure device is in Apollo mode (run 'cyn reset' first if needed)")
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
        mode = getattr(args, "mode", "normal")
        hold_apollo = (mode == "hold-apollo")
        boot_dfu = (mode == "boot-dfu")
        log_path = self._log_path(
            "reset-boot-dfu" if boot_dfu else ("reset-hold-apollo" if hold_apollo else "reset")
        )
        print(f"==> reset (log: {log_path.relative_to(REPO_ROOT)})")
        print(f"  mode: {mode}")

        if boot_dfu:
            with log_path.open("w") as log:
                ret = self._run_tee(
                    "boot-to-dfu",
                    [
                        str(VENV_PYTHON),
                        "-c",
                        """
import sys
import time
import usb.core
from apollo_fpga import ApolloDebugger, DebuggerNotFound


def _is_apollo_debugger_device(dev):
    request_type = 0xC0  # IN | VENDOR | DEVICE
    try:
        response = dev.ctrl_transfer(request_type, 0xa0, data_or_wLength=64, timeout=200)
    except usb.core.USBError:
        return False

    ident = bytes(response).decode('utf-8', errors='ignore').split('\\x00')[0]
    return "Apollo" in ident


print('Sending bootloader request...')
try:
    d = ApolloDebugger(force_offline=True)
except DebuggerNotFound as e:
    print(f'Apollo debugger not found (possibly already in bootloader): {e}')
else:
    try:
        d.out_request(0xed)
        print('Bootloader request sent.')
    except usb.core.USBError as e:
        # Reboot can race the status stage and cause timeout/pipe errors.
        print(f'Reboot request caused expected USB interruption: {e}')
    finally:
        d.close()

# Pause briefly, then confirm the post-reset USB state.
time.sleep(0.35)
deadline = time.time() + 6.0

while time.time() < deadline:
    dev_60e6 = usb.core.find(idVendor=0x1d50, idProduct=0x60e6)
    if dev_60e6 is not None:
        print('Detected Saturn-V bootloader (1d50:60e6).')
        sys.exit(0)

    dev_615c = usb.core.find(idVendor=0x1d50, idProduct=0x615c)
    dev_1209 = usb.core.find(idVendor=0x1209, idProduct=0x0010)
    candidate = dev_615c or dev_1209

    if candidate is not None and not _is_apollo_debugger_device(candidate):
        print('Detected non-Apollo responder on shared VID:PID (bootloader-like state).')
        sys.exit(0)

    time.sleep(0.1)

print('Bootloader was not detected on the USB bus after reboot request.')
sys.exit(1)
""",
                    ],
                    cwd=REPO_ROOT,
                    log_file=log,
                )

            print("Reset complete.")
            return ret

        reset_script = REPOS / "awto-cynthion" / "scripts" / "reset-cynthion.sh"
        with log_path.open("w") as log:
            if reset_script.exists() and not hold_apollo:
                ret = self._run_tee("reset-cynthion", [str(reset_script)],
                    cwd=REPO_ROOT, log_file=log)
            else:
                if reset_script.exists() and hold_apollo:
                    print("  hold-apollo mode bypasses reset-cynthion.sh to prevent auto-handoff.")
                allow_takeover = "False" if hold_apollo else "True"
                ret = self._run_tee("force-offline",
                    [str(VENV_PYTHON), "-c", f"""
import sys
try:
    import usb.core
    from apollo_fpga import ApolloDebugger
    ALLOW_FPGA_TAKEOVER = {allow_takeover}
    FPGA_VID, FPGA_PID = 0x1d50, 0x615b
    if usb.core.find(idVendor=FPGA_VID, idProduct=FPGA_PID) is None:
        print("Device already in Apollo mode (or not connected).")
        if not ALLOW_FPGA_TAKEOVER:
            print("Hold mode active; triggering soft reset without USB handoff.")
            d = ApolloDebugger()
            d.soft_reset()
            d.close()
            print("Reset complete (Apollo hold mode, takeover not allowed).")
        sys.exit(0)
    d = ApolloDebugger(force_offline=True)
    d.soft_reset()
    if ALLOW_FPGA_TAKEOVER:
        d.allow_fpga_takeover_usb()
    d.close()
    if ALLOW_FPGA_TAKEOVER:
        print("Reset complete (FPGA takeover allowed).")
    else:
        print("Reset complete (Apollo hold mode, takeover not allowed).")
except Exception as e:
    print(f"Reset failed: {{e}}")
    print("If moondancer has hung: power-cycle the device.")
    sys.exit(1)
"""],
                    log_file=log)

        print("Reset complete.")
        return ret

    # ── Target-Based Dispatcher ────────────────────────────────────────

    def cmd_target(self, args):
        """Route target + verb to appropriate handler"""
        target = getattr(args, "target", "all")
        verb = getattr(args, "verb", None)
        release = getattr(args, "release", False)
        destructive = getattr(args, "destructive", False)

        if not verb:
            print(f"ERROR: {target} requires a verb: build, flash, check, test, clean")
            return 1

        # Dispatch table: target → verb → (handler_fn, supports_release, supports_destructive)
        dispatch = {
            "riscv": {
                "build": (self._build_rust, True, False),
                "flash": (self._flash_rust, False, False),
                "check": (self._check_rust, False, False),
                "test": (lambda log: self._run_tee("rust test", ["cargo", "test"], cwd=MOONDANCER_FW, log_file=log), False, True),
                "clean": (lambda log: self._run_tee("rust clean", ["cargo", "clean"], cwd=MOONDANCER_FW, log_file=log, check=False), False, False),
            },
            "apollo": {
                "build": (self._build_apollo, True, False),
                "flash": (self._flash_apollo, False, False),
                "check": (self._check_c, False, False),
                "test": (lambda log: self._run_tee("apollo test", ["make", "APOLLO_BOARD=cynthion"], cwd=APOLLO_FW, log_file=log), False, True),
                "clean": (lambda log: self._run_tee("apollo clean", ["make", "clean", "APOLLO_BOARD=cynthion"], cwd=APOLLO_FW, log_file=log, check=False), False, False),
            },
            "fpga": {
                "build": (self._build_gateware, True, False),
                "flash": (self._flash_gateware, False, False),
                "check": (self._check_gateware, False, False),
                "test": (lambda log: 0, False, False),
                "clean": (lambda log: self._run_tee("gateware clean", ["make", "clean"], cwd=GATEWARE_DIR, log_file=log, check=False), False, False),
            },
            "app": {
                "build": (self._build_app, True, False),
                "flash": (lambda log: 0, False, False),
                "check": (lambda log: 0, False, False),
                "test": (lambda log: self._run_tee("flutter test", ["flutter", "test"], cwd=APP_DIR, log_file=log, check=False), False, True),
                "clean": (lambda log: self._run_tee("flutter clean", ["flutter", "clean"], cwd=APP_DIR, log_file=log, check=False), False, False),
            },
        }

        if target == "all":
            log_path = self._log_path(verb)
            print(f"==> {verb} all (log: {log_path.relative_to(REPO_ROOT)})")
            with log_path.open("w") as log:
                for t in ["riscv", "apollo", "fpga", "app"]:
                    if verb in dispatch[t]:
                        handler, has_release, has_destructive = dispatch[t][verb]
                        try:
                            if has_release:
                                ret = handler(log, release)
                            else:
                                ret = handler(log)
                            if ret != 0:
                                print(f"\n{verb} {t} failed")
                                return ret
                        except Exception as e:
                            print(f"Error in {t} {verb}: {e}")
                            return 1
            print(f"\n{verb} complete.")
            return 0
        else:
            if target not in dispatch:
                print(f"ERROR: unknown target '{target}'")
                return 1
            if verb not in dispatch[target]:
                print(f"ERROR: {target} doesn't support verb '{verb}'")
                return 1

            log_path = self._log_path(f"{target}-{verb}")
            print(f"==> {target} {verb} (log: {log_path.relative_to(REPO_ROOT)})")
            with log_path.open("w") as log:
                handler, has_release, has_destructive = dispatch[target][verb]
                try:
                    if has_release:
                        ret = handler(log, release)
                    else:
                        ret = handler(log)
                    if ret != 0:
                        print(f"\n{verb} failed")
                        return ret
                except Exception as e:
                    print(f"Error: {e}")
                    return 1

            print(f"\n{verb} complete.")
            return 0

    def cmd_monitor(self, args):
        """Device monitoring (stub — apollod integration pending)"""
        print("⚠ Monitor: apollod integration pending (not yet production-ready)")
        print("  See: /home/dan/git/awtoau/awto-cynthion/scripts/apollod.py")
        print("  apollod reads TTYs: ttyACM0=rv0, ttyACM1=fpg, ttyACM2=apl")
        print("  publishes JSON-lines on Unix socket for live device monitoring")
        print("  (architecture: integrate into cyn-daemon as HTTP /monitor endpoint)")
        return 0

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

        # Workspace setup
        setup = subs.add_parser("setup", help="Full setup")
        setup.add_argument("--parallel", action="store_true")
        setup.add_argument("--jobs", type=int, default=4)
        setup.set_defaults(func=self.cmd_workspace, command="setup")

        ci = subs.add_parser("ci", help="CI/CD (GitHub Actions)")
        ci.add_argument("subcommand", nargs="?", help="install, list, apollo, cynthion, luna")
        ci.set_defaults(func=self.cmd_ci)

        # Target-based architecture: cyn <target> <verb> [options]
        # Targets: riscv, apollo, fpga, app
        # Verbs: build, flash, check, test, clean
        for target_name, target_help in [
            ("riscv", "moondancer RISC-V firmware"),
            ("apollo", "Apollo ARM debug firmware"),
            ("fpga", "FPGA gateware (Amaranth HDL)"),
            ("app", "Flutter application"),
        ]:
            t = subs.add_parser(target_name, help=target_help)
            t.add_argument("verb", choices=["build", "flash", "check", "test", "clean"])
            t.add_argument("--release", action="store_true", help="Release profile")
            t.add_argument("--destructive", action="store_true", help="Include destructive tests")
            t.set_defaults(func=self.cmd_target, target=target_name)

        # Meta-commands: operate on all targets
        for verb in ["build", "flash", "check", "clean", "test"]:
            cmd = subs.add_parser(verb, help=f"{verb} all components")
            cmd.add_argument("--release", action="store_true", help="Release profile")
            cmd.add_argument("--destructive", action="store_true", help="Include destructive tests")
            cmd.set_defaults(func=self.cmd_target, target="all", verb=verb)

        # Device management
        subs.add_parser("deploy", help="Build --release + flash riscv + fpga (full cycle)").set_defaults(func=self.cmd_deploy)
        reset = subs.add_parser("reset", help="Reset device to Apollo mode")
        reset.add_argument(
            "--mode",
            choices=["normal", "hold-apollo", "boot-dfu"],
            default="normal",
            help="normal: allow FPGA takeover after reset; hold-apollo: keep Apollo in control for CDC/UART testing; boot-dfu: reboot Apollo into Saturn-V DFU bootloader",
        )
        reset.set_defaults(func=self.cmd_reset)
        subs.add_parser("monitor", help="Live device monitoring (stub)").set_defaults(func=self.cmd_monitor)

        # Daemon management
        daemon = subs.add_parser("daemon", help="Daemon management (start/stop/status/restart)")
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
