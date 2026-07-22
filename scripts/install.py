#!/usr/bin/env python3
"""
Unified installation script for Cynthion workspace.

Handles:
  - Cloning/pulling forked awtoau repositories
  - Initializing submodules (TinyUSB, etc)
  - Installing Python dependencies (Amaranth, cynthion package)
  - Building/testing all firmware and gateware components
  - Managing OSS CAD Suite toolchain (download, install, verify)

Workspace Commands:
  install.py setup               -- Full setup from scratch
  install.py clean               -- Remove all build artifacts
  install.py rebuild             -- Clean and rebuild everything
  install.py status              -- Check workspace status

Toolchain Commands:
  install.py toolchain-install   -- Download and install latest OSS CAD Suite
  install.py toolchain-status    -- Check OSS CAD Suite installation status

Global Options:
  --repo-only                    -- Only clone/update repos, skip builds
  --no-build                     -- Setup repos but skip all builds
  --no-submodules                -- Skip submodule initialization
  --verbose                      -- Show all command output (not just summary)
  --dry-run                      -- Show what would be done, don't execute
"""

import argparse
import subprocess
import sys
import os
import shutil
import json
import logging
import urllib.request
import urllib.error
import tarfile
import concurrent.futures
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add scripts directory to path for relative imports
sys.path.insert(0, str(Path(__file__).resolve().parent))
from logging_utils import setup_logging

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / "tmp"
LOGS = TMP / "logs"

def resolve_repos_root() -> Path:
    """Resolve the awto repository root with optional env override."""
    env_root = os.getenv("CYN_REPOS_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    candidates = [
        ROOT.parent / "awtoau",
        Path.home() / "git" / "awtoau",
    ]
    for base in candidates:
        if (base / "awto-cynthion").exists() and (base / "awto-apollo").exists():
            return base
    return candidates[-1]

REPOS = resolve_repos_root()
OSS_CAD_SUITE = Path.home() / "opt" / "oss-cad-suite"

# Global logger instance (initialized in main())
logger: Optional[logging.Logger] = None

# Forked repositories (all at ~/git/awtoau/)
REPOS_MANIFEST = {
    "awto-apollo": {
        "url": "https://github.com/greatscottgadgets/apollo.git",
        "path": REPOS / "awto-apollo",
        "required": True,
        "builds": ["apollo-firmware"],
    },
    "awto-cynthion": {
        "url": "https://github.com/greatscottgadgets/cynthion.git",
        "path": REPOS / "awto-cynthion",
        "required": True,
        "builds": ["moondancer-firmware", "gateware-analyzer", "gateware-facedancer"],
    },
    "awto-luna": {
        "url": "https://github.com/greatscottgadgets/luna.git",
        "path": REPOS / "awto-luna",
        "required": False,
        "builds": [],
    },
    "awto-luna-soc": {
        "url": "https://github.com/awtoau/awto-luna-soc.git",
        "path": REPOS / "awto-luna-soc",
        "required": True,
        "builds": [],
    },
    "awto-facedancer": {
        "url": "https://github.com/greatscottgadgets/facedancer.git",
        "path": REPOS / "awto-facedancer",
        "required": False,
        "builds": [],
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Logging and Output
# ─────────────────────────────────────────────────────────────────────────────

class Colors:
    """ANSI color codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"


def log(msg: str, level: str = "INFO") -> None:
    """
    Log a message using the global logger.

    Args:
        msg: Message to log
        level: Log level (INFO, OK, WARN, ERROR)
    """
    if logger is None:
        print(f"[{level}] {msg}")
        return

    level_map = {
        "INFO": logging.INFO,
        "OK": logging.INFO,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    log_level = level_map.get(level, logging.INFO)
    logger.log(log_level, msg)


def section(title: str) -> None:
    """
    Print a section header with decoration.

    Args:
        title: Section title to display
    """
    separator = "=" * 70
    print(f"\n{Colors.BOLD}{Colors.CYAN}{separator}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{title}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{separator}{Colors.RESET}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Command Execution
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CmdResult:
    """Result of command execution."""
    returncode: int
    stdout: str
    stderr: str
    cmd: str

    @property
    def success(self) -> bool:
        return self.returncode == 0

    def log_output(self, verbose: bool = False):
        """Log output, either verbose or summary."""
        if not self.success:
            log(f"FAILED: {self.cmd}", "ERROR")
            if self.stdout:
                print(f"  stdout: {self.stdout[-200:]}")
            if self.stderr:
                print(f"  stderr: {self.stderr[-200:]}")
            return False

        if verbose and self.stdout:
            print(self.stdout)
        return True


def run_cmd(
    cmd: List[str],
    *,
    cwd: Optional[Path] = None,
    verbose: bool = False,
    dry_run: bool = False,
) -> CmdResult:
    """Execute command, capturing output."""
    cmd_str = " ".join(str(c) for c in cmd)

    if dry_run:
        log(f"[DRY-RUN] {cmd_str}", "WARN")
        return CmdResult(returncode=0, stdout="", stderr="", cmd=cmd_str)

    if verbose:
        log(f"Running: {cmd_str}")

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min timeout
        )
        return CmdResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            cmd=cmd_str,
        )
    except subprocess.TimeoutExpired as e:
        return CmdResult(
            returncode=124,
            stdout=str(e),
            stderr="Command timed out after 600s",
            cmd=cmd_str,
        )
    except Exception as e:
        return CmdResult(
            returncode=-1,
            stdout="",
            stderr=str(e),
            cmd=cmd_str,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Repository Management
# ─────────────────────────────────────────────────────────────────────────────

def ensure_repos_dir() -> bool:
    """Ensure ~/git/awtoau/ exists."""
    REPOS.mkdir(parents=True, exist_ok=True)
    log(f"Repos directory: {REPOS}")
    return True


def clone_or_pull_repo(name: str, manifest: dict, verbose: bool, dry_run: bool) -> bool:
    """Clone or pull a single repository."""
    url = manifest["url"]
    path = manifest["path"]

    if path.exists():
        log(f"Pulling {name}...", "INFO")
        result = run_cmd(["git", "pull", "--quiet"], cwd=path, verbose=verbose, dry_run=dry_run)
        if result.success:
            log(f"✓ {name}", "OK")
            return True
        else:
            log(f"Failed to pull {name}", "ERROR")
            return False
    else:
        log(f"Cloning {name}...", "INFO")
        result = run_cmd(["git", "clone", url, str(path)], verbose=verbose, dry_run=dry_run)
        if result.success:
            log(f"✓ {name}", "OK")
            return True
        else:
            log(f"Failed to clone {name}", "ERROR")
            return False


def init_submodules(repo_path: Path, verbose: bool, dry_run: bool) -> bool:
    """Initialize git submodules in a repository."""
    result = run_cmd(
        ["git", "submodule", "update", "--init", "--recursive"],
        cwd=repo_path,
        verbose=verbose,
        dry_run=dry_run,
    )
    return result.success


# ─────────────────────────────────────────────────────────────────────────────
# Dependency Installation
# ─────────────────────────────────────────────────────────────────────────────

def setup_python_environment(verbose: bool, dry_run: bool) -> bool:
    """Install Python dependencies."""
    section("Python Environment")

    # Check Python version
    result = run_cmd(["python3.14", "--version"], verbose=verbose, dry_run=dry_run)
    if not result.success:
        log("Python 3.14 not found", "ERROR")
        return False
    log(f"✓ {result.stdout.strip()}", "OK")

    # Install cynthion package
    log("Installing cynthion package (editable)...", "INFO")
    cynthion_dir = REPOS / "awto-cynthion" / "cynthion" / "python"
    result = run_cmd(
        ["pip", "install", "--user", "-e", str(cynthion_dir)],
        verbose=verbose,
        dry_run=dry_run,
    )
    if result.success:
        log("✓ cynthion package installed", "OK")
    else:
        log("Failed to install cynthion package", "ERROR")
        return False

    return True


def get_latest_oss_cad_suite_release() -> Optional[Dict[str, str]]:
    """Fetch latest OSS CAD Suite release info from GitHub."""
    url = "https://api.github.com/repos/YosysHQ/oss-cad-suite-build/releases/latest"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
            tag = data.get("tag_name", "")

            # Find Linux x64 asset
            for asset in data.get("assets", []):
                name = asset.get("name", "")
                if "linux-x64" in name and name.endswith(".tgz"):
                    return {
                        "version": tag,
                        "url": asset.get("browser_download_url"),
                        "filename": name,
                    }
            return None
    except Exception as e:
        log(f"Failed to fetch latest release info: {e}", "WARN")
        return None


def download_oss_cad_suite(release_info: Dict[str, str], verbose: bool, dry_run: bool) -> bool:
    """Download and extract OSS CAD Suite."""
    section("Downloading OSS CAD Suite")

    version = release_info["version"]
    url = release_info["url"]
    filename = release_info["filename"]

    log(f"Version: {version}", "INFO")
    log(f"URL: {url}", "INFO")

    # Download to tmp
    download_path = TMP / filename

    if dry_run:
        log(f"[DRY-RUN] Would download to {download_path}", "WARN")
        return True

    if not TMP.exists():
        TMP.mkdir(parents=True, exist_ok=True)

    log(f"Downloading {filename}...", "INFO")
    try:
        urllib.request.urlretrieve(
            url,
            download_path,
            reporthook=lambda block, size, total: _download_progress(block, size, total) if verbose else None
        )
        log(f"✓ Downloaded to {download_path}", "OK")
    except Exception as e:
        log(f"Failed to download: {e}", "ERROR")
        return False

    # Extract
    log(f"Extracting to {OSS_CAD_SUITE}...", "INFO")
    try:
        # Remove old installation
        if OSS_CAD_SUITE.exists():
            log(f"Removing old installation at {OSS_CAD_SUITE}...", "WARN")
            shutil.rmtree(OSS_CAD_SUITE)

        OSS_CAD_SUITE.parent.mkdir(parents=True, exist_ok=True)

        # Extract tar.gz
        with tarfile.open(download_path, "r:gz") as tar:
            # The archive contains 'oss-cad-suite/' directory, extract to parent
            tar.extractall(path=OSS_CAD_SUITE.parent)

        log(f"✓ Extracted successfully", "OK")

        # Verify
        env_script = OSS_CAD_SUITE / "environment"
        if not env_script.exists():
            log(f"Extraction failed: {env_script} not found", "ERROR")
            return False

        # Cleanup download
        download_path.unlink()
        log(f"✓ OSS CAD Suite {version} installed", "OK")
        return True

    except Exception as e:
        log(f"Failed to extract: {e}", "ERROR")
        return False


def _download_progress(block: int, size: int, total: int):
    """Simple download progress callback."""
    if total > 0 and block * size < total:
        percent = min(100, int((block * size / total) * 100))
        print(f"  {percent}%", end="\r")


def verify_toolchain() -> bool:
    """Verify OSS CAD Suite is installed and accessible."""
    section("Toolchain Verification")

    if not OSS_CAD_SUITE.exists():
        log(f"OSS CAD Suite not found at {OSS_CAD_SUITE}", "ERROR")
        log("Install from: https://github.com/YosysHQ/oss-cad-suite-build/releases", "WARN")
        return False

    # Source environment and verify tools
    env_script = OSS_CAD_SUITE / "environment"
    if not env_script.exists():
        log(f"Environment script not found at {env_script}", "ERROR")
        return False

    # Check yosys
    result = run_cmd(["bash", "-c", f"source {env_script} && yosys --version"])
    if result.success:
        log(f"✓ Yosys: {result.stdout.strip()}", "OK")
    else:
        log("Yosys not found", "ERROR")
        return False

    # Check nextpnr
    result = run_cmd(["bash", "-c", f"source {env_script} && nextpnr-ecp5 --version"])
    if result.success:
        log(f"✓ nextpnr-ecp5: {result.stdout.strip()}", "OK")
    else:
        log("nextpnr-ecp5 not found", "ERROR")
        return False

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Build Targets
# ─────────────────────────────────────────────────────────────────────────────

def build_apollo_firmware(verbose: bool, dry_run: bool) -> bool:
    """
    Build Apollo firmware for Cynthion debug controller.

    Args:
        verbose: Show full command output
        dry_run: Simulate build without executing

    Returns:
        True if build successful, False otherwise
    """
    section("Apollo Firmware Build")

    fw_dir = REPOS / "awto-apollo" / "firmware"
    if not fw_dir.exists():
        logger.error(f"Apollo firmware directory not found: {fw_dir}")
        return False

    logger.debug(f"Firmware directory: {fw_dir}")

    # Get dependencies (TinyUSB submodule initialization)
    logger.info("Getting dependencies (TinyUSB, etc)...")
    result = run_cmd(
        ["make", "APOLLO_BOARD=cynthion", "get-deps"],
        cwd=fw_dir,
        verbose=verbose,
        dry_run=dry_run,
    )
    if not result.success:
        logger.error(f"Failed to get dependencies: {result.returncode}")
        if result.stdout:
            logger.debug(f"stdout: {result.stdout}")
        if result.stderr:
            logger.error(f"stderr: {result.stderr}")
        logger.error("This usually means TinyUSB submodule initialization failed. Try:")
        logger.error(f"  cd {fw_dir}")
        logger.error("  git submodule update --init --recursive lib/tinyusb/")
        return False

    # Clean previous build
    logger.info("Cleaning previous build artifacts...")
    clean_result = run_cmd(
        ["make", "APOLLO_BOARD=cynthion", "clean"],
        cwd=fw_dir,
        verbose=False,
        dry_run=dry_run,
    )

    # Build firmware
    logger.info("Building Apollo firmware...")
    result = run_cmd(
        ["make", "APOLLO_BOARD=cynthion"],
        cwd=fw_dir,
        verbose=verbose,
        dry_run=dry_run,
    )

    if result.success:
        logger.info("✓ Apollo firmware built successfully")
        output_file = fw_dir / "build" / "cynthion_d11" / "apollo_debug_soc.elf"
        if output_file.exists():
            logger.debug(f"Build artifact: {output_file}")
        return True
    else:
        logger.error(f"Apollo firmware build failed with exit code {result.returncode}")
        if result.stderr:
            logger.error(f"Build error output (last 500 chars): {result.stderr[-500:]}")
        return False


def build_moondancer_firmware(verbose: bool, dry_run: bool) -> bool:
    """Build moondancer RISC-V firmware."""
    section("moondancer Firmware Build (Rust/RISC-V)")

    fw_dir = REPOS / "awto-cynthion" / "firmware" / "moondancer"
    if not fw_dir.exists():
        log("moondancer firmware directory not found", "ERROR")
        return False

    log("Building release...", "INFO")
    result = run_cmd(
        ["cargo", "build", "--release"],
        cwd=fw_dir,
        verbose=verbose,
        dry_run=dry_run,
    )
    if result.success:
        log("✓ moondancer firmware built successfully", "OK")
        return True
    else:
        log("moondancer firmware build failed", "ERROR")
        if not verbose:
            print(f"  {result.stderr[-500:]}")
        return False


def build_gateware_analyzer(verbose: bool, dry_run: bool) -> bool:
    """Build analyzer gateware (Amaranth elaboration)."""
    section("Analyzer Gateware Build")

    gw_dir = REPOS / "awto-cynthion" / "cynthion" / "python"
    if not gw_dir.exists():
        log("Gateware directory not found", "ERROR")
        return False

    env_file = OSS_CAD_SUITE / "environment"
    env_setup = f"source {env_file}" if OSS_CAD_SUITE.exists() else ""

    cmd = [
        "bash",
        "-c",
        f"{env_setup} && LUNA_PLATFORM=cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2 "
        f"python3.14 -m cynthion.gateware.analyzer.top --dry-run"
    ]

    log("Elaborating analyzer gateware...", "INFO")
    result = run_cmd(cmd, cwd=gw_dir, verbose=verbose, dry_run=dry_run)

    if result.success:
        log("✓ Analyzer gateware elaborated successfully", "OK")
        return True
    else:
        log("Analyzer gateware elaboration failed", "ERROR")
        if not verbose:
            print(f"  {result.stderr[-500:]}")
        return False


def build_gateware_facedancer(verbose: bool, dry_run: bool) -> bool:
    """
    Build facedancer gateware via Amaranth elaboration.

    Args:
        verbose: Show full command output
        dry_run: Simulate build without executing

    Returns:
        True if elaboration successful, False otherwise
    """
    section("Facedancer Gateware Build")

    gw_dir = REPOS / "awto-cynthion" / "cynthion" / "python"
    if not gw_dir.exists():
        logger.error(f"Gateware directory not found: {gw_dir}")
        return False

    logger.debug(f"Gateware directory: {gw_dir}")

    env_file = OSS_CAD_SUITE / "environment"
    env_setup = f"source {env_file}" if OSS_CAD_SUITE.exists() else ""

    if not OSS_CAD_SUITE.exists():
        logger.error(f"OSS CAD Suite not found at {OSS_CAD_SUITE}")
        logger.error("Install it with: ./scripts/install.py toolchain-install")
        return False

    cmd = [
        "bash",
        "-c",
        f"{env_setup} && LUNA_PLATFORM=cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2 "
        f"python3.14 -m cynthion.gateware.facedancer.top --dry-run"
    ]

    logger.info("Elaborating facedancer gateware...")
    logger.debug(f"Command: {' '.join(cmd)}")
    result = run_cmd(cmd, cwd=gw_dir, verbose=verbose, dry_run=dry_run)

    if result.success:
        logger.info("✓ Facedancer gateware elaborated successfully")
        return True
    else:
        logger.error(f"Facedancer gateware elaboration failed with exit code {result.returncode}")
        # Check for known issues
        if "Field collection must be a dict, list, or Field, not None" in result.stderr:
            logger.error("Known issue: luna_soc SPIflash controller has incompatible Field definition")
            logger.error("Probable cause: Facedancer uses SPIflash but current luna_soc version is broken")
            logger.error("Workaround: Try analyzer-only build without SPIflash")
            logger.debug(f"Full error: {result.stderr}")
        else:
            if result.stderr:
                logger.error(f"Elaboration error (last 1000 chars): {result.stderr[-1000:]}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Parallel Build Execution (Python 3.14 no-GIL)
# ─────────────────────────────────────────────────────────────────────────────

def build_component_parallel(
    component_name: str,
    build_func,
    verbose: bool,
    dry_run: bool,
) -> Tuple[str, bool, Optional[str]]:
    """
    Wrapper for parallel build execution.

    Args:
        component_name: Name of component (for logging)
        build_func: Build function to call
        verbose: Verbose output flag
        dry_run: Dry-run flag

    Returns:
        Tuple of (component_name, success, error_msg)
    """
    try:
        success = build_func(verbose, dry_run)
        return (component_name, success, None)
    except Exception as e:
        return (component_name, False, str(e))


def run_builds_parallel(args, max_workers: int = 4) -> bool:
    """
    Run all builds in parallel using thread pool (Python 3.14 no-GIL).

    Args:
        args: Command-line arguments
        max_workers: Maximum number of parallel threads

    Returns:
        True if all builds succeeded, False otherwise
    """
    section("Parallel Build Execution")
    logger.info(f"Running {max_workers} parallel build threads...")

    builds = [
        ("Apollo Firmware", build_apollo_firmware),
        ("moondancer Firmware", build_moondancer_firmware),
        ("Analyzer Gateware", build_gateware_analyzer),
        ("Facedancer Gateware", build_gateware_facedancer),
    ]

    results = {}
    start_time = os.times().user

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all builds
        futures = {
            executor.submit(
                build_component_parallel,
                name,
                func,
                args.verbose,
                args.dry_run,
            ): name
            for name, func in builds
        }

        # Collect results as they complete
        for future in as_completed(futures):
            try:
                component, success, error = future.result()
                results[component] = (success, error)
                status = "✓" if success else "✗"
                logger.info(f"{status} {component}")
                if error and not success:
                    logger.debug(f"  Error: {error}")
            except Exception as e:
                component = futures[future]
                results[component] = (False, str(e))
                logger.error(f"✗ {component}: {e}")

    elapsed = os.times().user - start_time
    logger.info(f"Parallel builds completed in {elapsed:.1f}s")

    # Summary
    successful = sum(1 for success, _ in results.values() if success)
    total = len(results)
    logger.info(f"Results: {successful}/{total} builds successful")

    return all(success for success, _ in results.values())


def run_builds_sequential(args) -> bool:
    """
    Run builds sequentially (original behavior).

    Args:
        args: Command-line arguments

    Returns:
        True if all builds succeeded, False otherwise
    """
    section("Sequential Build Execution")

    builds_ok = True

    if not build_apollo_firmware(args.verbose, args.dry_run):
        builds_ok = False

    if not build_moondancer_firmware(args.verbose, args.dry_run):
        builds_ok = False

    if not build_gateware_analyzer(args.verbose, args.dry_run):
        builds_ok = False

    if not build_gateware_facedancer(args.verbose, args.dry_run):
        builds_ok = False

    return builds_ok


# ─────────────────────────────────────────────────────────────────────────────
# Main Installation Flows
# ─────────────────────────────────────────────────────────────────────────────

def cmd_setup(args) -> bool:
    """
    Full setup: clone repos, init submodules, install deps, build everything.

    Performs fail-fast system checks before attempting any builds to catch
    prerequisite issues early rather than deep in the build process.

    Args:
        args: Command-line arguments

    Returns:
        True if setup completed successfully, False if any critical step failed
    """
    section("Cynthion Workspace Full Setup")

    # Fail-fast: Check system prerequisites BEFORE doing anything else
    logger.info("Running fail-fast prerequisite check...")
    if not fail_fast_check(args):
        logger.error("✗ System prerequisites not met. Cannot proceed.")
        logger.error("Run './scripts/install.py prereqs' to see what's missing")
        return False

    print()

    # Repositories
    section("Repository Setup")
    ensure_repos_dir()

    failed_repos = []
    for name, manifest in REPOS_MANIFEST.items():
        if not manifest["required"]:
            log(f"Skipping optional {name}", "WARN")
            continue

        if not clone_or_pull_repo(name, manifest, args.verbose, args.dry_run):
            failed_repos.append(name)

        # Initialize submodules
        if not args.no_submodules:
            log(f"Initializing submodules in {name}...", "INFO")
            if init_submodules(manifest["path"], args.verbose, args.dry_run):
                log(f"✓ Submodules initialized", "OK")
            else:
                log(f"Warning: submodule init failed (may not be needed)", "WARN")

    if failed_repos:
        log(f"Failed to setup repos: {failed_repos}", "ERROR")
        return False

    if args.repo_only:
        log("Repo-only mode: skipping builds", "WARN")
        return True

    # Toolchain verification
    if not verify_toolchain():
        log("Toolchain verification failed", "ERROR")
        return False

    # Python environment
    if not setup_python_environment(args.verbose, args.dry_run):
        log("Python environment setup failed", "ERROR")
        return False

    if args.no_build:
        log("No-build mode: setup complete, skipping builds", "WARN")
        return True

    # Run builds (parallel or sequential)
    print()
    if args.parallel:
        if args.dry_run:
            logger.info("[DRY-RUN] Would run builds in parallel")
            builds_ok = run_builds_sequential(args)  # Still run sequential for dry-run
        else:
            builds_ok = run_builds_parallel(args, max_workers=args.jobs)
    else:
        builds_ok = run_builds_sequential(args)

    if not builds_ok:
        logger.error("✗ Some builds failed")
        return False

    section("Setup Complete")
    log("✓ All steps completed successfully", "OK")
    return True


def cmd_clean(args) -> bool:
    """Remove all build artifacts and temporary files."""
    section("Cleaning Build Artifacts")

    paths_to_clean = [
        TMP,
        REPOS / "awto-apollo" / "firmware" / "build",
        REPOS / "awto-cynthion" / "firmware" / "moondancer" / "target",
    ]

    for path in paths_to_clean:
        if path.exists():
            log(f"Removing {path}...", "INFO")
            if args.dry_run:
                log(f"[DRY-RUN] Would remove {path}", "WARN")
            else:
                shutil.rmtree(path, ignore_errors=True)
                log(f"✓ Removed", "OK")

    log("✓ Cleanup complete", "OK")
    return True


def cmd_rebuild(args) -> bool:
    """Clean everything and rebuild from scratch."""
    section("Full Rebuild")

    if not cmd_clean(args):
        return False

    return cmd_setup(args)


def cmd_versions(args) -> bool:
    """Show installed versions of all tools and components."""
    section("Version Information")

    versions_dict = {}

    # System tools
    print("\n--- System Tools ---")
    tools = {
        "Git": ["git", "--version"],
        "Python 3.14": ["python3.14", "--version"],
        "Rustc": ["rustc", "--version"],
        "Cargo": ["cargo", "--version"],
        "ARM GCC": ["arm-none-eabi-gcc", "--version"],
        "GCC": ["gcc", "--version"],
        "G++": ["g++", "--version"],
        "Make": ["make", "--version"],
        "CMake": ["cmake", "--version"],
        "Flex": ["flex", "--version"],
        "Bison": ["bison", "--version"],
    }

    for name, cmd in tools.items():
        result = run_cmd(cmd, verbose=False, dry_run=False)
        if result.success:
            version = result.stdout.split('\n')[0].strip()[:70]
            print(f"  {name:<20} {version}")
            versions_dict[name] = version
        else:
            print(f"  {name:<20} NOT FOUND")
            versions_dict[name] = "NOT FOUND"

    # FPGA Toolchain
    print("\n--- FPGA Toolchain (OSS CAD Suite) ---")
    if OSS_CAD_SUITE.exists():
        result = run_cmd(["bash", "-c", f"source {OSS_CAD_SUITE}/environment && yosys --version"])
        if result.success:
            version = result.stdout.split('\n')[0].strip()[:70]
            print(f"  Yosys{' ' * 16} {version}")
            versions_dict["Yosys"] = version
        else:
            print(f"  Yosys{' ' * 16} ERROR")

        result = run_cmd(["bash", "-c", f"source {OSS_CAD_SUITE}/environment && nextpnr-ecp5 --version"])
        if result.success:
            version = result.stdout.split('\n')[0].strip()[:70]
            print(f"  nextpnr-ecp5{' ' * 8} {version}")
            versions_dict["nextpnr-ecp5"] = version

        print(f"  Location{' ' * 12} {OSS_CAD_SUITE}")
        versions_dict["OSS CAD Suite Location"] = str(OSS_CAD_SUITE)
    else:
        print(f"  OSS CAD Suite{' ' * 7} NOT INSTALLED")
        versions_dict["OSS CAD Suite"] = "NOT INSTALLED"

    # Python packages
    print("\n--- Python Packages ---")
    packages = ["amaranth", "luna-usb", "luna-soc", "cynthion"]
    for pkg in packages:
        result = run_cmd(["python3.14", "-m", "pip", "show", pkg], verbose=False, dry_run=False)
        if result.success:
            # Extract version from pip output
            for line in result.stdout.split('\n'):
                if line.startswith('Version:'):
                    version = line.split(':', 1)[1].strip()
                    print(f"  {pkg:<20} {version}")
                    versions_dict[pkg] = version
                    break
        else:
            print(f"  {pkg:<20} NOT INSTALLED")
            versions_dict[pkg] = "NOT INSTALLED"

    # Repository commits
    print("\n--- Repository Commits ---")
    for name, manifest in REPOS_MANIFEST.items():
        path = manifest["path"]
        if path.exists():
            result = run_cmd(["git", "rev-parse", "--short", "HEAD"], cwd=path, verbose=False)
            if result.success:
                commit = result.stdout.strip()
                result_date = run_cmd(["git", "log", "-1", "--format=%ai"], cwd=path, verbose=False)
                date = result_date.stdout.strip()[:19] if result_date.success else "?"
                print(f"  {name:<20} {commit} ({date})")
                versions_dict[f"{name} (HEAD)"] = commit
            else:
                print(f"  {name:<20} ERROR")
        else:
            print(f"  {name:<20} NOT CLONED")

    # Save to JSON file
    if not args.dry_run:
        versions_file = TMP / "versions.json"
        try:
            with open(versions_file, 'w') as f:
                json.dump(versions_dict, f, indent=2)
            log(f"✓ Versions saved to {versions_file}", "OK")
        except Exception as e:
            log(f"Failed to save versions: {e}", "WARN")

    return True


def cmd_versions_check(args) -> bool:
    """Check local versions against latest remote versions."""
    section("Version Comparison: Local vs Latest")

    print("\n--- GitHub Releases (Latest Upstream) ---\n")

    comparisons = {
        "Yosys": {
            "repo": "YosysHQ/yosys",
            "asset_filter": None,
            "local_cmd": ["bash", "-c", f"source {OSS_CAD_SUITE}/environment && yosys --version 2>&1 | grep -oP '\\d+\\.\\d+' | head -1"],
        },
        "nextpnr": {
            "repo": "YosysHQ/nextpnr",
            "asset_filter": None,
            "local_cmd": ["bash", "-c", f"source {OSS_CAD_SUITE}/environment && nextpnr-ecp5 --version 2>&1 | head -1"],
        },
        "OSS CAD Suite": {
            "repo": "YosysHQ/oss-cad-suite-build",
            "asset_filter": None,
            "local_cmd": ["bash", "-c", f"[ -d {OSS_CAD_SUITE} ] && echo 'installed' || echo 'not installed'"],
        },
    }

    for tool_name, info in comparisons.items():
        repo = info["repo"]

        # Get latest remote
        try:
            url = f"https://api.github.com/repos/{repo}/releases/latest"
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read().decode())
                remote_version = data.get("tag_name", "unknown")
                remote_date = data.get("published_at", "?")[:10]

                print(f"{tool_name}:")
                print(f"  Latest:  {remote_version} ({remote_date})")

                # Get local version
                result = run_cmd(info["local_cmd"], verbose=False, dry_run=False)
                if result.success:
                    local_version = result.stdout.strip()[:50]
                    print(f"  Local:   {local_version}")

                    # Compare
                    if remote_version.lstrip('v') in local_version or local_version in remote_version:
                        log("  Status:  ✓ Up-to-date", "OK")
                    else:
                        log("  Status:  ⚠ Outdated (update available)", "WARN")
                else:
                    log("  Local:   (error getting version)", "ERROR")

                print()

        except Exception as e:
            log(f"{tool_name}: Failed to fetch - {e}", "WARN")
            print()

    # Check repo commits
    print("--- Repository Commits ---\n")
    for name, manifest in REPOS_MANIFEST.items():
        if not manifest["required"]:
            continue

        path = manifest["path"]
        if not path.exists():
            log(f"{name}: Not cloned", "WARN")
            continue

        # Local commit
        result_local = run_cmd(["git", "rev-parse", "HEAD"], cwd=path, verbose=False)
        local_commit = result_local.stdout.strip()[:7] if result_local.success else "?"

        # Remote commit
        result_fetch = run_cmd(["git", "fetch", "--quiet", "origin"], cwd=path, verbose=False, dry_run=False)
        result_remote = run_cmd(["git", "rev-parse", "origin/main"], cwd=path, verbose=False)
        remote_commit = result_remote.stdout.strip()[:7] if result_remote.success else "?"

        print(f"{name}:")
        print(f"  Local:   {local_commit}")
        print(f"  Remote:  {remote_commit}")

        if local_commit == remote_commit:
            log("  Status:  ✓ In sync", "OK")
        else:
            log("  Status:  ⚠ Behind remote (git pull to update)", "WARN")

        print()

    return True


def cmd_ci_install(args) -> bool:
    """Install act (GitHub Actions runner for local Docker)."""
    section("Installing act (GitHub Actions Runner)")

    # Check if already installed
    result = run_cmd(["which", "act"], verbose=False, dry_run=False)
    if result.success:
        result_version = run_cmd(["act", "--version"], verbose=False, dry_run=False)
        log(f"✓ act already installed: {result_version.stdout.strip()}", "OK")
        return True

    # Install act
    log("Downloading and installing act...", "INFO")
    cmd = [
        "bash",
        "-c",
        "curl https://raw.githubusercontent.com/nektos/act/master/install.sh | bash"
    ]

    result = run_cmd(cmd, verbose=args.verbose, dry_run=args.dry_run)

    if result.success or args.dry_run:
        log("✓ act installed successfully", "OK")
        log("Next: ./scripts/install.py ci-list /path/to/repo", "INFO")
        return True
    else:
        log("Failed to install act", "ERROR")
        log("Manual install: https://github.com/nektos/act", "WARN")
        return False


def cmd_ci_list(args) -> bool:
    """List GitHub Actions workflows in current repo."""
    section("GitHub Actions Workflows")

    # Check if we're in a git repo with .github/workflows
    gh_dir = Path.cwd() / ".github" / "workflows"
    if not gh_dir.exists():
        log(f"No .github/workflows directory found in {Path.cwd()}", "ERROR")
        log("Run this command in a repo with GitHub Actions (e.g., awto-apollo)", "WARN")
        return False

    log(f"Found workflows in {gh_dir}", "INFO")
    print()

    workflows = list(gh_dir.glob("*.yml")) + list(gh_dir.glob("*.yaml"))
    if not workflows:
        log("No workflows found", "WARN")
        return False

    for workflow_file in sorted(workflows):
        print(f"  • {workflow_file.name}")

        # Try to extract job names
        try:
            with open(workflow_file) as f:
                content = f.read()
                import re
                jobs = re.findall(r"^\s+(\w+):\s*$", content, re.MULTILINE)
                if jobs:
                    for job in jobs[:3]:  # Show first 3 jobs
                        print(f"      - {job}")
                    if len(jobs) > 3:
                        print(f"      ... and {len(jobs)-3} more")
        except Exception:
            pass

    print()
    log("Run with act:", "INFO")
    print(f"  cd {Path.cwd()}")
    print(f"  act -l                      # List all jobs")
    print(f"  act -j <job-name>           # Run specific job")
    print(f"  act -P ubuntu-latest=ubuntu:22.04  # Use custom Docker image")

    return True


def check_system_info() -> dict:
    """
    Scan system information: OS, package manager, versions.

    Returns:
        Dictionary with system info (os_type, os_version, pkg_manager, etc.)
    """
    info = {"os_type": None, "os_version": None, "pkg_manager": None}

    # Check OS type
    if Path("/etc/os-release").exists():
        result = run_cmd(["cat", "/etc/os-release"], verbose=False, dry_run=False)
        if result.success:
            for line in result.stdout.split('\n'):
                if line.startswith("ID="):
                    info["os_type"] = line.split('=')[1].strip('"')
                if line.startswith("VERSION_ID="):
                    info["os_version"] = line.split('=')[1].strip('"')

    # Determine package manager
    if Path("/usr/bin/dnf").exists():
        info["pkg_manager"] = "dnf"
    elif Path("/usr/bin/apt-get").exists():
        info["pkg_manager"] = "apt"
    elif Path("/usr/bin/apt").exists():
        info["pkg_manager"] = "apt"

    logger.debug(f"System info: {info}")
    return info


def fail_fast_check(args) -> bool:
    """
    Aggressive fail-fast prerequisite checking.

    Scans system configuration, checks critical tools, and fails early
    if prerequisites aren't met. This prevents deep build failures.

    Args:
        args: Command-line arguments

    Returns:
        True if system is ready for builds, False otherwise
    """
    section("System Scan & Prerequisite Check")

    # 1. System information scan
    logger.info("Scanning system information...")
    sys_info = check_system_info()

    if sys_info["os_type"]:
        logger.info(f"✓ OS: {sys_info['os_type']} {sys_info['os_version']}")
    else:
        logger.error("✗ Could not determine OS type")
        return False

    if sys_info["pkg_manager"]:
        logger.info(f"✓ Package manager: {sys_info['pkg_manager']}")
    else:
        logger.error("✗ No supported package manager found (need dnf or apt)")
        return False

    print()

    # 2. Critical build tools check
    logger.info("Checking critical build tools...")
    critical_tools = {
        "git": ["git", "--version"],
        "gcc": ["gcc", "--version"],
        "make": ["make", "--version"],
        "python3.14": ["python3.14", "--version"],
    }

    missing_critical = []
    for name, cmd in critical_tools.items():
        result = run_cmd(cmd, verbose=False, dry_run=False)
        if result.success:
            version = result.stdout.split('\n')[0].strip()[:50]
            logger.info(f"✓ {name:<15} {version}")
        else:
            logger.error(f"✗ {name:<15} NOT FOUND (CRITICAL)")
            missing_critical.append(name)

    if missing_critical:
        logger.error(f"\n✗ CRITICAL MISSING: {', '.join(missing_critical)}")
        logger.error("Cannot proceed without these tools. Install with:")
        if sys_info["pkg_manager"] == "dnf":
            logger.error(f"  sudo dnf install -y {' '.join(missing_critical)}-devel")
        else:
            logger.error(f"  sudo apt-get install -y {' '.join(missing_critical)}")
        return False

    print()

    # 3. FPGA toolchain check
    logger.info("Checking FPGA toolchain...")
    fpga_tools = {
        "arm-none-eabi-gcc": ["arm-none-eabi-gcc", "--version"],
        "rustc": ["rustc", "--version"],
    }

    missing_fpga = []
    for name, cmd in fpga_tools.items():
        result = run_cmd(cmd, verbose=False, dry_run=False)
        if result.success:
            version = result.stdout.split('\n')[0].strip()[:50]
            logger.info(f"✓ {name:<20} {version}")
        else:
            logger.warning(f"⚠ {name:<20} not found (needed for firmware)")
            missing_fpga.append(name)

    if missing_fpga:
        logger.warning(f"Missing FPGA tools: {', '.join(missing_fpga)}")
        logger.warning("Cannot build firmware, but can still build gateware")

    print()

    # 4. OSS CAD Suite check
    logger.info("Checking OSS CAD Suite (gateware toolchain)...")
    if OSS_CAD_SUITE.exists():
        result = run_cmd(
            ["bash", "-c", f"source {OSS_CAD_SUITE}/environment && yosys --version"],
            verbose=False,
            dry_run=False,
        )
        if result.success:
            version = result.stdout.split('\n')[0].strip()[:50]
            logger.info(f"✓ OSS CAD Suite {version}")
        else:
            logger.error("✗ OSS CAD Suite found but not functional")
            logger.error("Try reinstalling: ./scripts/install.py toolchain-install")
            return False
    else:
        logger.error(f"✗ OSS CAD Suite not installed at {OSS_CAD_SUITE}")
        logger.error("Install with: ./scripts/install.py toolchain-install")
        return False

    print()
    logger.info("✓ System prerequisites check PASSED")
    return True


def cmd_prereqs(args) -> bool:
    """Check system prerequisites."""
    return fail_fast_check(args)

    checks = {
        "git": ["git", "--version"],
        "python3.14": ["python3.14", "--version"],
        "rustc": ["rustc", "--version"],
        "arm-none-eabi-gcc": ["arm-none-eabi-gcc", "--version"],
        "gcc": ["gcc", "--version"],
        "make": ["make", "--version"],
        "cmake": ["cmake", "--version"],
        "flex": ["flex", "--version"],
        "bison": ["bison", "--version"],
    }

    print()
    found = 0
    missing = 0

    for name, cmd in checks.items():
        result = run_cmd(cmd, verbose=False, dry_run=False)
        if result.success:
            version = result.stdout.split('\n')[0].strip()
            log(f"✓ {name:<25} {version[:60]}", "OK")
            found += 1
        else:
            log(f"✗ {name:<25} NOT FOUND", "WARN")
            missing += 1

    print()

    # Check OSS CAD Suite
    if OSS_CAD_SUITE.exists():
        result = run_cmd(["bash", "-c", f"source {OSS_CAD_SUITE}/environment && yosys --version"])
        if result.success:
            log(f"✓ {'OSS CAD Suite':<25} {result.stdout.split(chr(10))[0][:60]}", "OK")
            found += 1
        else:
            log(f"✗ {'OSS CAD Suite':<25} Installation incomplete", "WARN")
            missing += 1
    else:
        log(f"✗ {'OSS CAD Suite':<25} NOT INSTALLED", "WARN")
        missing += 1

    print()
    log(f"Found: {found} | Missing: {missing}", "INFO")

    if missing > 0:
        print()
        log("Missing prerequisites. Install with:", "WARN")
        print()
        print("  Fedora/RHEL:")
        print("    sudo dnf install -y python3.14 python3.14-devel rustup \\")
        print("      arm-none-eabi-gcc-cs gcc gcc-c++ make cmake git \\")
        print("      boost-devel eigen-devel libreadline-devel zlib-devel \\")
        print("      bison flex clang curl jq dfu-util openocd tcl tcl-devel")
        print()
        print("  Debian/Ubuntu:")
        print("    sudo apt-get install -y python3.14 python3.14-dev rustc cargo \\")
        print("      arm-none-eabi-gcc gcc g++ make cmake git \\")
        print("      libboost-all-dev libeigen3-dev libreadline-dev zlib1g-dev \\")
        print("      bison flex clang curl jq dfu-util openocd tcl tcl-dev")
        print()
        return False

    return True


def cmd_status(args) -> bool:
    """Check status of repositories and builds."""
    section("Workspace Status")

    log(f"Root: {ROOT}", "INFO")
    log(f"Repos: {REPOS}", "INFO")
    log(f"OSS CAD Suite: {OSS_CAD_SUITE}", "INFO")

    print()

    for name, manifest in REPOS_MANIFEST.items():
        path = manifest["path"]
        exists = path.exists()
        status = "✓" if exists else "✗"
        req = "required" if manifest["required"] else "optional"

        log(f"{status} {name} [{req}]", "INFO" if exists else "WARN")
        if exists:
            # Try to get git status
            result = run_cmd(["git", "rev-parse", "--short", "HEAD"], cwd=path)
            if result.success:
                print(f"    HEAD: {result.stdout.strip()}")

    print()

    # Check builds
    log("Build artifacts:", "INFO")
    apollo_elf = REPOS / "awto-apollo" / "firmware" / "build" / "cynthion_d11" / "apollo_debug_soc.elf"
    moondancer_elf = REPOS / "awto-cynthion" / "firmware" / "moondancer" / "target" / "riscv32imac-unknown-none-elf" / "release" / "moondancer"

    log(f"{'✓' if apollo_elf.exists() else '✗'} Apollo firmware: {apollo_elf.name}", "OK" if apollo_elf.exists() else "WARN")
    log(f"{'✓' if moondancer_elf.exists() else '✗'} moondancer firmware: {moondancer_elf.name}", "OK" if moondancer_elf.exists() else "WARN")

    return True


def cmd_toolchain_install(args) -> bool:
    """Install or reinstall OSS CAD Suite toolchain."""
    section("OSS CAD Suite Installation")

    log("Checking for latest release...", "INFO")
    release_info = get_latest_oss_cad_suite_release()

    if not release_info:
        log("Failed to fetch latest release info", "ERROR")
        log("Visit: https://github.com/YosysHQ/oss-cad-suite-build/releases", "WARN")
        return False

    log(f"Latest version: {release_info['version']}", "OK")

    # Check current version
    if OSS_CAD_SUITE.exists():
        env_script = OSS_CAD_SUITE / "environment"
        if env_script.exists():
            result = run_cmd(["bash", "-c", f"source {env_script} && yosys --version"])
            if result.success:
                log(f"Current: {result.stdout.strip()}", "INFO")

    if not download_oss_cad_suite(release_info, args.verbose, args.dry_run):
        return False

    # Verify installation
    if not args.dry_run:
        if not verify_toolchain():
            log("Verification failed after installation", "ERROR")
            return False

    return True


def cmd_toolchain_status(args) -> bool:
    """Check OSS CAD Suite installation status."""
    section("Toolchain Status")

    if not OSS_CAD_SUITE.exists():
        log(f"OSS CAD Suite not installed at {OSS_CAD_SUITE}", "WARN")
        return False

    log(f"Location: {OSS_CAD_SUITE}", "INFO")

    env_script = OSS_CAD_SUITE / "environment"
    if not env_script.exists():
        log("Environment script not found", "ERROR")
        return False

    # Get versions
    result = run_cmd(["bash", "-c", f"source {env_script} && yosys --version"])
    if result.success:
        log(f"Yosys: {result.stdout.strip()}", "OK")
    else:
        log("Yosys: not available", "WARN")

    result = run_cmd(["bash", "-c", f"source {env_script} && nextpnr-ecp5 --version"])
    if result.success:
        log(f"nextpnr-ecp5: {result.stdout.strip()}", "OK")
    else:
        log("nextpnr-ecp5: not available", "WARN")

    return True


def cmd_clone_repos(args) -> bool:
    """Clone all repositories to specified path."""
    section("Clone Repositories to Custom Path")

    target_repos = args.repos_path or REPOS
    log(f"Target path: {target_repos}", "INFO")

    if target_repos.exists() and list(target_repos.iterdir()):
        log(f"Path already exists with content: {target_repos}", "WARN")
        if not args.dry_run:
            response = input("Remove existing content and recreate? (y/N): ")
            if response.lower() != "y":
                log("Aborted", "WARN")
                return False
            log(f"Removing {target_repos}...", "INFO")
            shutil.rmtree(target_repos)

    target_repos.mkdir(parents=True, exist_ok=True)
    log(f"Created {target_repos}", "INFO")

    # Clone all repos
    failed = []
    for name, manifest in REPOS_MANIFEST.items():
        url = manifest["url"]
        path = target_repos / name

        log(f"Cloning {name}...", "INFO")
        if args.dry_run:
            log(f"[DRY-RUN] Would clone {url} to {path}", "WARN")
        else:
            result = run_cmd(["git", "clone", url, str(path)], verbose=args.verbose)
            if result.success:
                log(f"✓ {name}", "OK")

                # Initialize submodules
                if not args.no_submodules:
                    result = run_cmd(
                        ["git", "submodule", "update", "--init", "--recursive"],
                        cwd=path,
                        verbose=False
                    )
                    if result.success:
                        log(f"  ✓ Submodules initialized", "OK")
            else:
                log(f"✗ Failed to clone {name}", "ERROR")
                failed.append(name)

    if failed:
        log(f"Failed to clone: {failed}", "ERROR")
        return False

    log(f"✓ All repos cloned to {target_repos}", "OK")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Workspace commands
    subparsers.add_parser("setup", help="Full setup from scratch")
    subparsers.add_parser("clean", help="Remove all build artifacts")
    subparsers.add_parser("rebuild", help="Clean and rebuild everything")
    subparsers.add_parser("status", help="Check workspace status")
    subparsers.add_parser("versions", help="Show installed versions of all tools")
    subparsers.add_parser("versions-check", help="Check local vs latest remote versions")
    subparsers.add_parser("prereqs", help="Check system prerequisites")
    subparsers.add_parser(
        "clone-repos",
        help="Clone all repos to specified path (use --repos-path)"
    )

    # Toolchain commands
    subparsers.add_parser(
        "toolchain-install",
        help="Download and install latest OSS CAD Suite"
    )
    subparsers.add_parser(
        "toolchain-status",
        help="Check OSS CAD Suite installation status"
    )

    # CI commands
    subparsers.add_parser(
        "ci-install",
        help="Install act (GitHub Actions runner for local Docker)"
    )
    subparsers.add_parser(
        "ci-list",
        help="List GitHub Actions workflows in a repo"
    )

    # Global options
    parser.add_argument(
        "--repos-path",
        type=Path,
        default=None,
        help=f"Custom repos path (default: {REPOS})",
    )
    parser.add_argument(
        "--repo-only",
        action="store_true",
        help="Only clone/update repos, skip builds",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Setup repos but skip all builds",
    )
    parser.add_argument(
        "--no-submodules",
        action="store_true",
        help="Skip submodule initialization",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show all command output",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done, don't execute",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run builds in parallel using thread pool (Python 3.14 no-GIL)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=4,
        metavar="N",
        help="Max parallel jobs when --parallel enabled (default: 4)",
    )

    args = parser.parse_args()

    # Initialize global logger
    global logger
    TMP.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(
        "install",
        log_dir=LOGS,
        level=logging.DEBUG if args.verbose else logging.INFO,
    )
    logger.debug(f"Workspace root: {ROOT}")
    logger.debug(f"Temporary directory: {TMP}")
    logger.debug(f"Logs directory: {LOGS}")

    # Override REPOS if custom path provided
    if args.repos_path:
        args.repos_path = args.repos_path.expanduser().resolve()
        logger.info(f"Using custom repos path: {args.repos_path}")

    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == "setup":
            success = cmd_setup(args)
        elif args.command == "clean":
            success = cmd_clean(args)
        elif args.command == "rebuild":
            success = cmd_rebuild(args)
        elif args.command == "status":
            success = cmd_status(args)
        elif args.command == "versions":
            success = cmd_versions(args)
        elif args.command == "versions-check":
            success = cmd_versions_check(args)
        elif args.command == "prereqs":
            success = cmd_prereqs(args)
        elif args.command == "clone-repos":
            success = cmd_clone_repos(args)
        elif args.command == "toolchain-install":
            success = cmd_toolchain_install(args)
        elif args.command == "toolchain-status":
            success = cmd_toolchain_status(args)
        elif args.command == "ci-install":
            success = cmd_ci_install(args)
        elif args.command == "ci-list":
            success = cmd_ci_list(args)
        else:
            log(f"Unknown command: {args.command}", "ERROR")
            return 1

        return 0 if success else 1

    except KeyboardInterrupt:
        log("\nAborted by user", "WARN")
        return 130
    except Exception as e:
        log(f"Unexpected error: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
