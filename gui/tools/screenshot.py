#!/usr/bin/env python3
"""Run the built Cynthion Monitor bundle on a virtual X display and photograph it.

The app is a desktop Flutter app: `flutter run` needs a real display and a human.
This gives an agent (or CI) a way to see what the app actually draws without
taking over the user's session, and without any hardware attached.

    gui/tools/screenshot.py --out tmp/gui-boot.png

Every wait here polls for a condition and reports what it was waiting for when it
gives up.  Nothing sleeps for a fixed period and then assumes success.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "gui"
BUNDLE = APP / "build/linux/x64/debug/bundle/cynthion_monitor"
LOG_DIR = REPO / "tmp/logs"

# Poll interval and budget.  The window is created by the Flutter engine a few
# hundred ms after exec on a warm page cache; a cold one has been seen to take
# several seconds.  30 s is generous enough that expiry means "it did not start",
# not "it was slow", which is the only reading that makes the failure useful.
POLL_S = 0.1
WINDOW_BUDGET_S = 30.0
# The first frame lands one or two vsyncs after the window is mapped.  We detect
# it by content (a non-uniform image) rather than by waiting a period.
FRAME_BUDGET_S = 15.0


def log(msg: str) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    line = f"{stamp} {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "gui-screenshot.log").open("a") as fh:
        fh.write(line + "\n")


def require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        sys.exit(f"missing tool: {tool}")
    return path


def free_display() -> str:
    for n in range(99, 130):
        if not Path(f"/tmp/.X11-unix/X{n}").exists():
            return f":{n}"
    sys.exit("no free X display between :99 and :129")


def wait_for(predicate, budget_s: float, what: str):
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(POLL_S)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="tmp/gui-boot.png",
                    help="PNG path, relative to the repo root")
    ap.add_argument("--size", default="1400x900")
    ap.add_argument("--hold", action="store_true",
                    help="leave the app and Xvfb running (prints the DISPLAY)")
    ap.add_argument("--home", default="tmp/gui-home",
                    help="throwaway HOME for the app's preferences; empty "
                         "string to use the real one")
    ap.add_argument("--software", action="store_true",
                    help="Skia's software rasteriser instead of GL-on-llvmpipe. "
                         "Left off by default: it produced no frame at all here, "
                         "while llvmpipe draws correctly once the window and the "
                         "engine surface agree on a size")
    args = ap.parse_args()

    for tool in ("Xvfb", "xdotool", "import"):
        require(tool)
    if not BUNDLE.exists():
        sys.exit(f"not built: {BUNDLE}\n  flutter build linux --debug")

    out = (REPO / args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    display = free_display()
    log(f"Xvfb {display} {args.size}, bundle {BUNDLE.relative_to(REPO)}")

    xvfb = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", f"{args.size}x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    env = dict(os.environ, DISPLAY=display, GDK_BACKEND="x11")
    env.pop("WAYLAND_DISPLAY", None)

    if args.home:
        # An isolated HOME keeps the user's real window geometry and node
        # positions out of the picture, and lets us pin the window to the size
        # the engine starts at.  A window that differs from the engine's initial
        # surface makes llvmpipe miss the resize handshake -- "Timed out waiting
        # for OpenGL frame of size WxH (have 1280x720)" -- and every capture
        # after that is torn.
        home = Path(args.home).resolve()
        prefs = home / ".local/share/au.awto.cynthion_monitor"
        prefs.mkdir(parents=True, exist_ok=True)
        w, h = args.size.split("x")
        (prefs / "shared_preferences.json").write_text(
            '{"flutter.window_geometry_v1": "0,0,%s.0,%s.0"}' % (w, h))
        env["HOME"] = str(home)
        env["XDG_DATA_HOME"] = str(home / ".local/share")
        env["XDG_CACHE_HOME"] = str(home / ".cache")

    if not wait_for(lambda: Path(f"/tmp/.X11-unix/X{display[1:]}").exists(),
                    10.0, "X socket"):
        xvfb.kill()
        sys.exit(f"Xvfb never created {display}")

    app_log = LOG_DIR / "gui-app.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [str(BUNDLE)]
    if args.software:
        cmd.append("--enable-software-rendering")
    with app_log.open("w") as fh:
        app = subprocess.Popen(cmd, cwd=APP, env=env,
                               stdout=fh, stderr=subprocess.STDOUT)

    def window_ids():
        if app.poll() is not None:
            return None
        r = subprocess.run(["xdotool", "search", "--name", "Cynthion"],
                           env=env, capture_output=True, text=True)
        return r.stdout.split() or None

    ids = wait_for(window_ids, WINDOW_BUDGET_S, "app window")
    if ids is None:
        rc = app.poll()
        log(f"no window; app exit code {rc}. app log:")
        log(app_log.read_text()[-4000:])
        app.kill(); xvfb.kill()
        return 1
    log(f"window(s) {' '.join(ids)}")

    def rendered():
        subprocess.run(["import", "-display", display, "-window", "root",
                        str(out)], env=env, capture_output=True)
        if not out.exists():
            return False
        r = subprocess.run(["identify", "-format", "%[standard-deviation]",
                            str(out)], capture_output=True, text=True)
        try:
            return float(r.stdout.strip() or 0) > 1.0
        except ValueError:
            return False

    if wait_for(rendered, FRAME_BUDGET_S, "first frame"):
        log(f"captured {out}")
    else:
        log(f"window mapped but the screen stayed blank; wrote {out} anyway")

    tail = app_log.read_text().strip()
    if tail:
        log("app stdout/stderr:")
        for line in tail.splitlines()[-40:]:
            log(f"  | {line}")

    if args.hold:
        log(f"holding: DISPLAY={display} app pid {app.pid} xvfb pid {xvfb.pid}")
        return 0

    app.send_signal(signal.SIGTERM)
    try:
        app.wait(timeout=5)  # SIGTERM to a Flutter app: exits on the next loop turn
    except subprocess.TimeoutExpired:
        app.kill()
    xvfb.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
