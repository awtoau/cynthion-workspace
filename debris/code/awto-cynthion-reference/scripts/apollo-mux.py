#!/usr/bin/env python3
"""
apollo-mux.py — Cynthion interactive multiplexer

Interactive REPL + live log viewer.  Routes commands to:
  riscv  → GCP verbs via cynthion Python library (works against 1d50:615b)
  apollo → ttyACM2 or helpful stub
  fpga   → stub

Usage:
  venv/bin/python scripts/apollo-mux.py [--no-spinner] [-v]

Connects to apollod socket if running; otherwise reads TTYs directly.
"""

import argparse
import json
import logging
import os
import select
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("apollo-mux")

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SOCKET = Path.home() / ".local" / "run" / "apollod.sock"

VID = 0x1d50
PID_APOLLO = 0x615c
PID_MOONDANCER = 0x615b

CANARY_EXPECTED = 0xDEAD_C0DE

SPINNER_FRAMES = r"\|/-"

# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------

_USE_COLOUR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    if not _USE_COLOUR:
        return text
    return f"\033[{code}m{text}\033[0m"


def green(t):  return _c("32", t)
def blue(t):   return _c("34", t)
def grey(t):   return _c("90", t)
def red(t):    return _c("1;31", t)
def yellow(t): return _c("33", t)
def bold(t):   return _c("1", t)


SRC_COLOUR = {
    "rv0": green,
    "fpg": blue,
    "apl": grey,
}


def colour_src(src: str, text: str) -> str:
    fn = SRC_COLOUR.get(src, lambda t: t)
    return fn(text)


# ---------------------------------------------------------------------------
# Device discovery (same approach as apollod.py, inline for standalone use)
# ---------------------------------------------------------------------------

def _find_cynthion_pid() -> Optional[int]:
    """Return PID of first Cynthion device found, or None."""
    sys_bus = Path("/sys/bus/usb/devices")
    if not sys_bus.exists():
        return None
    for devdir in sorted(sys_bus.iterdir()):
        vp = devdir / "idVendor"
        pp = devdir / "idProduct"
        if not vp.exists():
            continue
        try:
            vid = int(vp.read_text().strip(), 16)
            pid = int(pp.read_text().strip(), 16)
        except (ValueError, OSError):
            continue
        if vid == VID and pid in (PID_APOLLO, PID_MOONDANCER):
            return pid
    return None


def _find_tty_nodes() -> list:
    """Return sorted list of /dev/ttyACM* nodes that exist."""
    return sorted([
        f"/dev/ttyACM{i}" for i in range(4)
        if os.path.exists(f"/dev/ttyACM{i}")
    ])


# ---------------------------------------------------------------------------
# Live log reader: connect to apollod socket or read TTYs directly
# ---------------------------------------------------------------------------

class LogSource:
    """Feeds log events to a callback.  Uses apollod socket if available,
    otherwise reads /dev/ttyACM* directly."""

    def __init__(self, on_event, socket_path: str = str(DEFAULT_SOCKET)):
        self._on_event = on_event
        self._socket_path = socket_path
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="log-src")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        if os.path.exists(self._socket_path):
            self._run_socket()
        else:
            self._run_tty()

    def _run_socket(self):
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self._socket_path)
            sock.setblocking(False)
            log.info("Connected to apollod socket")
        except OSError as e:
            log.warning("Cannot connect to apollod socket: %s — falling back to TTY", e)
            self._run_tty()
            return

        buf = b""
        while not self._stop.is_set():
            try:
                rlist, _, _ = select.select([sock], [], [], 0.2)
            except (ValueError, OSError):
                break
            if rlist:
                try:
                    data = sock.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        ev = json.loads(line.decode("utf-8", errors="replace"))
                        self._on_event(ev)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
        try:
            sock.close()
        except OSError:
            pass

    def _run_tty(self):
        """Read /dev/ttyACMx directly."""
        tty_names = {0: "rv0", 1: "fpg", 2: "apl"}
        fds = {}
        bufs = {}

        nodes = _find_tty_nodes()
        if not nodes:
            log.warning("No ttyACM* nodes found — no live log")
            return

        for node in nodes:
            try:
                fd = os.open(node, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
                idx = int(node.replace("/dev/ttyACM", ""))
                src = tty_names.get(idx, f"tty{idx}")
                fds[fd] = src
                bufs[fd] = b""
                log.info("Direct TTY: %s -> %s", node, src)
            except OSError as e:
                log.warning("Cannot open %s: %s", node, e)

        if not fds:
            return

        while not self._stop.is_set():
            try:
                rlist, _, _ = select.select(list(fds.keys()), [], [], 0.2)
            except (ValueError, OSError):
                break
            for fd in rlist:
                try:
                    data = os.read(fd, 4096)
                except (BlockingIOError, OSError):
                    continue
                if not data:
                    continue
                t = time.monotonic()
                bufs[fd] += data
                while b"\n" in bufs[fd]:
                    line, bufs[fd] = bufs[fd].split(b"\n", 1)
                    msg = line.rstrip(b"\r").decode("utf-8", errors="replace").strip()
                    if not msg:
                        continue
                    self._on_event({
                        "t": t,
                        "src": fds[fd],
                        "kind": "log",
                        "msg": msg,
                    })

        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Spinner (per-source, cycling)
# ---------------------------------------------------------------------------

class Spinner:
    def __init__(self, sources: list, enabled: bool = True):
        self.sources = sources
        self.enabled = enabled
        self._frame = 0
        self._lock = threading.Lock()
        self._last_tick = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="spinner")

    def tick(self, src: str):
        with self._lock:
            self._last_tick[src] = time.monotonic()

    def start(self):
        if self.enabled:
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                now = time.monotonic()
                parts = []
                for src in self.sources:
                    last = self._last_tick.get(src, 0)
                    age = now - last
                    frame = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
                    if age < 2.0:
                        parts.append(colour_src(src, f"{frame}{src}"))
                    else:
                        parts.append(grey(f"-{src}"))
                self._frame += 1

            line = "  ".join(parts)
            print(f"\r{line}  ", end="", flush=True)

            # Wait using select on a self-pipe
            r_fd, w_fd = os.pipe()
            try:
                select.select([r_fd], [], [], 0.25)
            finally:
                os.close(r_fd)
                os.close(w_fd)


# ---------------------------------------------------------------------------
# riscv command implementations (GCP via cynthion library)
# ---------------------------------------------------------------------------

def _connect_board():
    import cynthion
    board = cynthion.Cynthion()
    if board is None:
        raise RuntimeError("No Cynthion device found")
    return board


def cmd_riscv_canary(_args):
    """Show canary and stack status."""
    try:
        board = _connect_board()
    except Exception as e:
        print(red(f"Cannot connect to device: {e}"))
        return

    try:
        canary_value, stack_used, stack_total = board.apis.selftest.get_canary_status()
    except Exception as e:
        print(red(f"get_canary_status failed: {e}"))
        return

    status = "INTACT" if canary_value == CANARY_EXPECTED else "CORRUPT"
    colour = green if status == "INTACT" else red
    print(colour(f"  canary : 0x{canary_value:08x}  [{status}]  (expected 0x{CANARY_EXPECTED:08x})"))
    stack_free = stack_total - stack_used
    bar_len = 20
    used_ratio = stack_used / stack_total if stack_total else 0
    filled = int(bar_len * used_ratio)
    bar = "#" * filled + "." * (bar_len - filled)
    bar_colour = red if used_ratio > 0.8 else (yellow if used_ratio > 0.5 else green)
    print(f"  stack  : [{bar_colour(bar)}]  {stack_used}/{stack_total} bytes used  ({stack_free} free)")


def cmd_riscv_stack(_args):
    """Show stack usage only."""
    try:
        board = _connect_board()
    except Exception as e:
        print(red(f"Cannot connect to device: {e}"))
        return

    try:
        _canary_value, stack_used, stack_total = board.apis.selftest.get_canary_status()
    except Exception as e:
        print(red(f"get_canary_status failed: {e}"))
        return

    stack_free = stack_total - stack_used
    pct = 100 * stack_used / stack_total if stack_total else 0
    print(f"  stack used  : {stack_used} bytes ({pct:.1f}%)")
    print(f"  stack free  : {stack_free} bytes")
    print(f"  stack total : {stack_total} bytes")


def _destructive_warn(action: str) -> bool:
    """Print a warning and ask for confirmation. Returns True if confirmed."""
    print(red(f"  WARNING: {action} is destructive."))
    print(red("  The device will hang and require a reset."))
    print(red("  Run:  ./scripts/reset-cynthion.sh  to recover."))
    try:
        ans = input("  Type 'yes' to proceed: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans == "yes"


def cmd_riscv_panic(_args):
    """Trigger firmware panic (destructive — device will hang)."""
    if not _destructive_warn("trigger_panic"):
        print("  Aborted.")
        return
    try:
        board = _connect_board()
        board.apis.selftest.trigger_panic()
        print(yellow("  trigger_panic sent — device should now be unresponsive"))
    except Exception as e:
        print(yellow(f"  verb raised (expected if device hung): {e}"))


def cmd_riscv_overflow(_args):
    """Trigger stack overflow (destructive — device will hang)."""
    if not _destructive_warn("trigger_stack_overflow"):
        print("  Aborted.")
        return
    try:
        board = _connect_board()
        board.apis.selftest.trigger_stack_overflow()
        print(yellow("  trigger_stack_overflow sent — device should now be unresponsive"))
    except Exception as e:
        print(yellow(f"  verb raised (expected if device hung): {e}"))


def cmd_riscv_corrupt(_args):
    """Corrupt stack canary (destructive — device will hang on next interrupt)."""
    if not _destructive_warn("corrupt_canary"):
        print("  Aborted.")
        return
    try:
        board = _connect_board()
        board.apis.selftest.corrupt_canary()
        print(yellow("  corrupt_canary sent — canary is now corrupt"))
        print(yellow("  Device will hang on next USB SOF interrupt (~1 ms)"))
    except Exception as e:
        print(yellow(f"  verb raised (expected if device hung): {e}"))


# ---------------------------------------------------------------------------
# apollo / fpga stubs
# ---------------------------------------------------------------------------

_APOLLO_NOT_AVAILABLE = (
    "  Not available: Apollo firmware update required.\n"
    "  Apollo CDC-ACM TTYs are only present in Apollo mode (1d50:615c).\n"
    "  Currently this command is a stub pending Apollo firmware support."
)


def cmd_apollo_status(_args):
    print(grey(_APOLLO_NOT_AVAILABLE))


def cmd_apollo_heartbeat(_args):
    print(grey(_APOLLO_NOT_AVAILABLE))


def cmd_apollo_reset(_args):
    print(grey(_APOLLO_NOT_AVAILABLE))


def cmd_apollo_jtag_monitor(_args):
    print(grey(_APOLLO_NOT_AVAILABLE))


def cmd_fpga_stub(_args):
    print(grey("  FPGA commands are not yet implemented (stub)."))


# ---------------------------------------------------------------------------
# Command dispatch table
# ---------------------------------------------------------------------------

COMMANDS = {
    # riscv — live GCP commands
    ("riscv", "canary"):   (cmd_riscv_canary, "Show canary integrity and stack usage"),
    ("riscv", "stack"):    (cmd_riscv_stack,  "Show stack used/free"),
    ("riscv", "panic"):    (cmd_riscv_panic,  "Trigger firmware panic [destructive]"),
    ("riscv", "overflow"): (cmd_riscv_overflow, "Trigger stack overflow [destructive]"),
    ("riscv", "corrupt"):  (cmd_riscv_corrupt,  "Corrupt stack canary [destructive]"),

    # apollo — stubs
    ("apollo", "status"):       (cmd_apollo_status,       "Device status [stub]"),
    ("apollo", "heartbeat"):    (cmd_apollo_heartbeat,    "Set heartbeat interval [stub]"),
    ("apollo", "reset"):        (cmd_apollo_reset,        "Reset device [stub]"),
    ("apollo", "jtag-monitor"): (cmd_apollo_jtag_monitor, "JTAG monitor on/off [stub]"),

    # fpga — stubs
    ("fpga", "status"):  (cmd_fpga_stub, "FPGA status [stub]"),
    ("fpga", "program"): (cmd_fpga_stub, "Program FPGA [stub]"),
}


def cmd_help(_args):
    print(bold("  Available commands:"))
    for (ns, verb), (fn, desc) in sorted(COMMANDS.items()):
        print(f"    {bold(ns):<24} {verb:<20}  {desc}")
    print()
    print("  Type 'quit' or 'exit' or Ctrl-D to leave.")


def dispatch(line: str) -> bool:
    """Dispatch a command line.  Returns False to quit."""
    line = line.strip()
    if not line:
        return True
    if line in ("quit", "exit", "q"):
        return False
    if line == "help":
        cmd_help(None)
        return True

    parts = line.split()
    if len(parts) < 2:
        # Try single-word convenience: "canary" → "riscv canary"
        if parts[0] in ("canary", "stack", "panic", "overflow", "corrupt"):
            parts = ["riscv"] + parts
        else:
            print(grey(f"  Unknown command: {parts[0]!r}  (try 'help')"))
            return True

    ns, verb = parts[0], parts[1]
    rest = parts[2:]
    key = (ns, verb)

    if key not in COMMANDS:
        print(grey(f"  Unknown command: {ns} {verb}  (try 'help')"))
        return True

    fn, _desc = COMMANDS[key]
    try:
        fn(rest)
    except KeyboardInterrupt:
        print()
    except Exception as e:
        print(red(f"  Error: {e}"))

    return True


# ---------------------------------------------------------------------------
# Event display
# ---------------------------------------------------------------------------

def format_event(event: dict) -> str:
    t = event.get("t", 0)
    src = event.get("src", "?")
    kind = event.get("kind", "log")
    msg = event.get("msg", "")
    ts = f"{t:12.3f}"

    if kind in ("log",):
        outlier = red("  [OUTLIER]") if event.get("outlier") else ""
        corr = f"  [grp:{event['corr_group']}]" if "corr_group" in event else ""
        src_str = colour_src(src, f"[{src:4s}]")
        return f"{grey(ts)}  {src_str}{outlier}{corr}  {msg}"
    elif kind == "dedup":
        count = event.get("count", "?")
        med = event.get("median_ms", "?")
        std = event.get("stdev_ms", "?")
        src_str = colour_src(src, f"[{src:4s}]")
        return (
            f"{grey(ts)}  {src_str}  "
            f"{yellow(f'(x{count} @{med}ms±{std}ms)')}  {msg}"
        )
    elif kind == "status":
        return f"{grey(ts)}  {grey('[apollod]')}  {grey(msg)}"
    else:
        return f"{grey(ts)}  [{src}]  {msg}"


# ---------------------------------------------------------------------------
# REPL using prompt_toolkit (with readline fallback)
# ---------------------------------------------------------------------------

def _make_completer():
    """Return a prompt_toolkit completer for the command table."""
    try:
        from prompt_toolkit.completion import Completer, Completion
    except ImportError:
        return None

    class MuxCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor.lstrip()
            parts = text.split()
            n = len(parts)

            if n == 0:
                namespaces = sorted(set(k[0] for k in COMMANDS))
                for ns in namespaces:
                    yield Completion(ns)
            elif n == 1 and not text.endswith(" "):
                prefix = parts[0]
                namespaces = sorted(set(k[0] for k in COMMANDS))
                for ns in namespaces:
                    if ns.startswith(prefix):
                        yield Completion(ns, start_position=-len(prefix))
            elif n == 1 and text.endswith(" "):
                ns = parts[0]
                for (k_ns, verb) in COMMANDS:
                    if k_ns == ns:
                        yield Completion(verb)
            elif n == 2 and not text.endswith(" "):
                ns, prefix = parts[0], parts[1]
                for (k_ns, verb) in COMMANDS:
                    if k_ns == ns and verb.startswith(prefix):
                        yield Completion(verb, start_position=-len(prefix))

    return MuxCompleter()


def run_prompt_toolkit(log_source: LogSource, spinner: Optional[Spinner]):
    """REPL using prompt_toolkit with live log in background."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout

    completer = _make_completer()
    session = PromptSession(
        "cynthion > ",
        completer=completer,
    )

    def on_event(ev):
        line = format_event(ev)
        # prompt_toolkit's patch_stdout handles the reprinting
        print(line)
        if spinner:
            spinner.tick(ev.get("src", ""))

    log_source._on_event = on_event
    log_source.start()
    if spinner:
        spinner.start()

    with patch_stdout():
        while True:
            try:
                line = session.prompt()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not dispatch(line):
                break

    log_source.stop()
    if spinner:
        spinner.stop()


def run_readline(log_source: LogSource, spinner: Optional[Spinner]):
    """REPL using readline (or plain input)."""
    try:
        import readline
        # Basic tab completion with readline
        verbs = [f"{ns} {v}" for (ns, v) in COMMANDS.keys()]
        verbs += ["help", "quit", "exit"]

        def completer(text, state):
            options = [v for v in verbs if v.startswith(text)]
            return options[state] if state < len(options) else None

        readline.set_completer(completer)
        readline.parse_and_bind("tab: complete")
    except ImportError:
        pass

    events_lock = threading.Lock()
    events_pending: list = []

    def on_event(ev):
        with events_lock:
            events_pending.append(ev)
        if spinner:
            spinner.tick(ev.get("src", ""))

    log_source._on_event = on_event
    log_source.start()
    if spinner:
        spinner.start()

    print(bold("cynthion mux — type 'help' for commands, Ctrl-D to quit"))

    while True:
        # Flush any pending log events before showing prompt
        with events_lock:
            pending = events_pending[:]
            events_pending.clear()
        for ev in pending:
            print(format_event(ev))

        try:
            line = input("cynthion > ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not dispatch(line):
            break

    log_source.stop()
    if spinner:
        spinner.stop()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--socket", metavar="PATH", default=str(DEFAULT_SOCKET),
                        help=f"apollod socket path (default: {DEFAULT_SOCKET})")
    parser.add_argument("--no-spinner", action="store_true",
                        help="Suppress per-source spinner")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Print device status
    pid = _find_cynthion_pid()
    if pid is None:
        print(yellow("  No Cynthion device detected — commands may fail"))
    elif pid == PID_MOONDANCER:
        print(green("  Cynthion in moondancer mode (1d50:615b) — riscv commands available"))
    elif pid == PID_APOLLO:
        print(green("  Cynthion in Apollo mode (1d50:615c) — all commands available"))

    sock_path = args.socket
    log_source = LogSource(on_event=lambda _: None, socket_path=sock_path)

    sources = ["rv0", "fpg", "apl"]
    spinner = Spinner(sources, enabled=not args.no_spinner)

    # Try prompt_toolkit first
    try:
        import prompt_toolkit  # noqa: F401
        run_prompt_toolkit(log_source, spinner if not args.no_spinner else None)
    except ImportError:
        run_readline(log_source, spinner if not args.no_spinner else None)


if __name__ == "__main__":
    main()
