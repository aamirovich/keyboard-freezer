"""macOS check: load the frameworks and try to create the event tap.

- No Accessibility permission (the normal case on a fresh Mac): the tap is refused, and we check
  the user gets the friendly permission message instead of a crash.
- Permission granted (e.g. on GitHub's macOS runners): we run a real 4 s freeze, post 3 key
  presses and expect the freezer to report that it swallowed exactly those 3.
Run: python tests/smoke_macos.py
"""
import ctypes
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import keyboard_freezer as kf  # noqa: E402

kVK_ANSI_A, kVK_ANSI_B, kVK_ANSI_C = 0, 11, 8
kCGHIDEventTap = 0


def post_keys(codes) -> None:
    cg = ctypes.CDLL(kf.MacBackend.CORE_GRAPHICS)
    cf = ctypes.CDLL(kf.MacBackend.CORE_FOUNDATION)
    cg.CGEventCreateKeyboardEvent.argtypes = (ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool)
    cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    cg.CGEventPost.argtypes = (ctypes.c_uint32, ctypes.c_void_p)
    cf.CFRelease.argtypes = (ctypes.c_void_p,)
    for code in codes:
        for down in (True, False):
            event = cg.CGEventCreateKeyboardEvent(None, code, down)
            cg.CGEventPost(kCGHIDEventTap, event)
            cf.CFRelease(event)
            time.sleep(0.05)


def main() -> int:
    backend = kf.MacBackend()
    print(f"frameworks loaded, keys held right now: {sorted(backend.keys_held())}")
    try:
        backend.lock()
    except kf.FreezeError as e:
        print(f"tap refused, friendly message shown:\n  {e}")
        ok = "Accessibility" in str(e)
        print("PASS (permission path)" if ok else "FAIL")
        return 0 if ok else 1
    backend.unlock()
    print("event tap allowed here, running a real freeze")

    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "keyboard_freezer.py"), "-s", "4"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        time.sleep(1.5)
        post_keys((kVK_ANSI_A, kVK_ANSI_B, kVK_ANSI_C))
        out, _ = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
    text = out.decode("utf-8", "replace")
    print(text)
    ok = proc.returncode == 0 and "You hit 3 keys" in text
    print("PASS (keys swallowed)" if ok else f"FAIL (exit code {proc.returncode})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
