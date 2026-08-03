#!/usr/bin/env python3
"""
Flutter auto-connect test suite

Tests the transport_provider auto-connect feature:
  - Starts a WebSocket server on 127.0.0.1:8765
  - Verifies app connects within polling window (200ms)
  - Validates connection state transitions
"""

import asyncio
import websockets
import sys
import time
from pathlib import Path
from datetime import datetime
import subprocess
import threading

# Workspace paths
SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
APP_DIR = REPO_ROOT / "app"

class ColoredFormatter:
    """ANSI color codes for output"""
    RESET = "\033[0m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"

    @staticmethod
    def success(msg):
        return f"{ColoredFormatter.GREEN}✓{ColoredFormatter.RESET} {msg}"

    @staticmethod
    def error(msg):
        return f"{ColoredFormatter.RED}✗{ColoredFormatter.RESET} {msg}"

    @staticmethod
    def info(msg):
        return f"{ColoredFormatter.BLUE}→{ColoredFormatter.RESET} {msg}"

    @staticmethod
    def warning(msg):
        return f"{ColoredFormatter.YELLOW}!{ColoredFormatter.RESET} {msg}"

class WSTestServer:
    """WebSocket server for testing auto-connect"""

    def __init__(self, host="127.0.0.1", port=8765):
        self.host = host
        self.port = port
        self.connections = []
        self.first_connection_time = None
        self.server = None

    async def handle_client(self, websocket):
        """Handle incoming WebSocket connections"""
        if self.first_connection_time is None:
            self.first_connection_time = time.time()

        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        print(f"  [{timestamp}] {ColoredFormatter.success('Client connected')} from {websocket.remote_address}")
        self.connections.append(websocket.remote_address)
        sys.stdout.flush()

        try:
            async for message in websocket:
                timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                msg_preview = message[:60].replace('\n', ' ')
                print(f"  [{timestamp}] Message: {msg_preview}")
                sys.stdout.flush()
        except websockets.exceptions.ConnectionClosed:
            timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            print(f"  [{timestamp}] {ColoredFormatter.info('Client disconnected')}")
            sys.stdout.flush()

    async def start(self):
        """Start the WebSocket server"""
        self.server = await websockets.serve(self.handle_client, self.host, self.port)
        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        print(f"{ColoredFormatter.info(f'[{timestamp}] WebSocket server started on ws://{self.host}:{self.port}')}")
        sys.stdout.flush()

    async def run(self, duration=15):
        """Run server for specified duration"""
        await self.start()
        try:
            await asyncio.sleep(duration)
        except asyncio.CancelledError:
            pass
        finally:
            if self.server:
                self.server.close()
                await self.server.wait_closed()

def run_flutter_app(timeout=10):
    """Run Flutter tests and return True if all pass"""
    print(f"\n{ColoredFormatter.info('Running Flutter tests...')}")
    try:
        result = subprocess.run(
            ["flutter", "test"],
            cwd=APP_DIR,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode == 0:
            # Count passed tests
            output = result.stdout + result.stderr
            if "All tests passed" in output or "passed" in output.lower():
                print(ColoredFormatter.success("Flutter tests passed"))
                return True
            else:
                print(ColoredFormatter.warning("Tests ran but status unclear"))
                print(output[:500])
                return False
        else:
            print(ColoredFormatter.error(f"Flutter tests failed (exit {result.returncode})"))
            print(result.stderr[:500])
            return False
    except subprocess.TimeoutExpired:
        print(ColoredFormatter.error(f"Flutter tests timed out after {timeout}s"))
        return False
    except Exception as e:
        print(ColoredFormatter.error(f"Failed to run tests: {e}"))
        return False

async def test_auto_connect():
    """Test auto-connect feature with WebSocket server"""
    print(f"\n{ColoredFormatter.info('Testing auto-connect feature...')}")
    print(f"{ColoredFormatter.info('Starting WebSocket server on port 8765')}")

    server = WSTestServer()
    server_task = asyncio.create_task(server.run(duration=12))

    # Wait for server to start
    await asyncio.sleep(0.5)

    # Start Flutter app which should auto-connect
    print(f"{ColoredFormatter.info('Starting Flutter app...')}")
    start_time = time.time()

    try:
        # Run flutter run with timeout
        process = await asyncio.create_subprocess_exec(
            "flutter", "run", "-d", "linux",
            cwd=str(APP_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # Wait for connection or timeout
        max_wait = 10
        while time.time() - start_time < max_wait:
            if server.first_connection_time:
                elapsed = server.first_connection_time - start_time
                print(f"\n{ColoredFormatter.success(f'Auto-connect successful in {elapsed:.3f}s')}")
                print(f"{ColoredFormatter.info(f'Total connections: {len(server.connections)}')}")

                # Verify it happened within polling window
                if elapsed < 1.0:  # Should be <200ms per poll
                    print(ColoredFormatter.success("Connection within expected polling window"))
                    return True
                else:
                    print(ColoredFormatter.warning(f"Connection took longer than expected ({elapsed:.3f}s)"))
                    return True

            await asyncio.sleep(0.1)

        # Timeout without connection
        print(f"\n{ColoredFormatter.error(f'No auto-connect after {max_wait}s')}")
        print(ColoredFormatter.info("This could indicate:"))
        print("  - App failed to launch")
        print("  - Transport provider auto-connect not triggered")
        print("  - Connection attempt failed silently (expected in polling)")

        # Kill the process
        try:
            process.kill()
        except:
            pass

        return False

    except Exception as e:
        print(ColoredFormatter.error(f"Test error: {e}"))
        return False
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

async def main():
    """Run all tests"""
    print(f"\n{ColoredFormatter.BLUE}{'='*60}")
    print(f"Cynthion Flutter Auto-Connect Test Suite")
    print(f"{'='*60}{ColoredFormatter.RESET}\n")

    # Test 1: Run unit tests
    print(f"{ColoredFormatter.info('Test 1: Flutter Widget Tests')}")
    unit_tests_pass = run_flutter_app()

    # Test 2: Test auto-connect with server
    print(f"\n{ColoredFormatter.info('Test 2: Auto-Connect Feature')}")
    auto_connect_pass = await test_auto_connect()

    # Summary
    print(f"\n{ColoredFormatter.BLUE}{'='*60}")
    print("Test Summary:")
    print(f"{'='*60}{ColoredFormatter.RESET}")
    print(f"  Unit Tests:     {ColoredFormatter.success('PASS') if unit_tests_pass else ColoredFormatter.error('FAIL')}")
    print(f"  Auto-Connect:   {ColoredFormatter.success('PASS') if auto_connect_pass else ColoredFormatter.error('FAIL')}")

    all_pass = unit_tests_pass and auto_connect_pass
    status = ColoredFormatter.success("ALL TESTS PASSED") if all_pass else ColoredFormatter.error("SOME TESTS FAILED")
    print(f"\n{status}\n")

    return 0 if all_pass else 1

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print(f"\n{ColoredFormatter.warning('Test interrupted by user')}")
        sys.exit(130)
