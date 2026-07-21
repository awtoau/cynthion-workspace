#!/usr/bin/env python3
"""
Fault detection self-test for moondancer firmware.

Tests the hardening changes from issues #14, #16, #18:
  - Canary is present and intact after boot
  - corrupt_canary: GCP call returns OK, device then hangs (canary fires on next interrupt)
  - trigger_panic:  device hangs immediately
  - trigger_stack_overflow: device hangs (stack smashes canary, detected on next interrupt)

Destructive tests kill the USB connection — the device must be power-cycled
(or soft-reset via `./scripts/reset-cynthion.sh`) between them.

Usage:
  venv/bin/python scripts/test-fault-detection.py [--destructive]

Without --destructive, only the safe canary probe runs.
With    --destructive, all fault injection tests run in sequence with prompts.
"""

import argparse
import logging
import sys
import time

log = logging.getLogger(__name__)

CANARY_EXPECTED = 0xDEAD_C0DE


def connect():
    import cynthion
    board = cynthion.Cynthion()
    return board


def run_safe_tests(api):
    print("\n--- safe: canary status ---")
    canary_value, stack_used, stack_total = api.get_canary_status()
    print(f"  canary word : 0x{canary_value:08x}  (expected 0x{CANARY_EXPECTED:08x})")
    print(f"  stack used  : {stack_used} bytes")
    print(f"  stack total : {stack_total} bytes")
    print(f"  stack free  : {stack_total - stack_used} bytes")

    if canary_value != CANARY_EXPECTED:
        print(f"  FAIL: canary is 0x{canary_value:08x}, expected 0x{CANARY_EXPECTED:08x}")
        return False

    print("  PASS: canary intact")
    return True


def run_destructive_test(label, call_fn):
    """Run a fault injection call and verify the USB connection dies."""
    print(f"\n--- destructive: {label} ---")
    print("  calling verb (device will hang after this)...")

    try:
        call_fn()
        # corrupt_canary returns normally before the panic fires
        print("  verb returned OK — waiting for panic on next interrupt...")
        time.sleep(0.5)
        # Now try another GCP call — should fail if canary fired
        print("  probing device (expect timeout/error)...")
    except Exception as e:
        print(f"  verb raised: {e}")

    # Try to talk to the device — expect it to be dead
    import cynthion
    try:
        board2 = cynthion.Cynthion()
        board2.apis.selftest.get_canary_status()
        print("  UNEXPECTED: device still responding — fault detection may not have fired")
        return False
    except Exception as e:
        print(f"  PASS: device not responding ({type(e).__name__}) — fault detected as expected")
        return True


def wait_for_reset(prompt):
    print(f"\n  {prompt}")
    print("  Run:  ./scripts/reset-cynthion.sh")
    input("  Press Enter once the device has re-enumerated... ")
    time.sleep(1.0)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--destructive", action="store_true",
                        help="Run fault injection tests (kills USB, requires reset between each)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    print("Moondancer fault detection self-test")
    print("=====================================")

    # Safe test — always runs
    try:
        board = connect()
    except Exception as e:
        print(f"Could not connect to Cynthion: {e}")
        print("Is moondancer running? Try: cynthion run facedancer")
        sys.exit(1)

    api = board.apis.selftest
    ok = run_safe_tests(api)
    if not ok:
        sys.exit(1)

    if not args.destructive:
        print("\nSafe tests passed. Run with --destructive to test fault injection.")
        print("WARNING: --destructive tests kill the USB connection and require a reset.")
        sys.exit(0)

    # Destructive tests — each kills the device
    results = {}

    print("\n" + "="*50)
    print("DESTRUCTIVE TESTS — device will hang after each")
    print("="*50)

    # Test 1: corrupt_canary
    # The GCP call returns OK. The panic fires on the next USB SOF interrupt (~1 ms).
    # This is the cleanest observable path: host receives the response, then device dies.
    board = connect()
    api = board.apis.selftest
    results["corrupt_canary"] = run_destructive_test(
        "corrupt_canary (GCP returns OK, panic fires ~1ms later)",
        lambda: api.corrupt_canary()
    )
    wait_for_reset("Reset device before next test.")

    # Test 2: trigger_panic
    # Direct panic — GCP call does not return, device hangs immediately.
    board = connect()
    api = board.apis.selftest
    results["trigger_panic"] = run_destructive_test(
        "trigger_panic (direct panic, no GCP response expected)",
        lambda: api.trigger_panic()
    )
    wait_for_reset("Reset device before next test.")

    # Test 3: trigger_stack_overflow
    # Unbounded recursion — smashes stack into canary region.
    # Canary fires on the next interrupt after the overflow.
    board = connect()
    api = board.apis.selftest
    results["trigger_stack_overflow"] = run_destructive_test(
        "trigger_stack_overflow (recursion, canary fires on next interrupt)",
        lambda: api.trigger_stack_overflow()
    )
    wait_for_reset("Reset device after final test.")

    # Summary
    print("\n=== Results ===")
    all_pass = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {status}  {name}")
        if not passed:
            all_pass = False

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
