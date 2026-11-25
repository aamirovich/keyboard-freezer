"""macOS check: load the frameworks, try to create the event tap, release it.

Without the Accessibility permission (the normal case on a CI runner) the tap is refused,
and the test checks that the user gets the friendly permission message instead of a crash.
Run: python tests/smoke_macos.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import keyboard_freezer as kf  # noqa: E402


def main() -> int:
    backend = kf.MacBackend()
    held = backend.keys_held()
    print(f"frameworks loaded, keys held right now: {sorted(held)}")
    try:
        backend.lock()
    except kf.FreezeError as e:
        print(f"tap refused, friendly message shown:\n  {e}")
        ok = "Accessibility" in str(e)
    else:
        backend.pump(0.5)
        backend.unlock()
        print("event tap created, pumped for 0.5 s and released")
        ok = True
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
