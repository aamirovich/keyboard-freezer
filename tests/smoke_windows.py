"""Windows end-to-end check: freeze for 3 s, inject harmless F24 presses, see if they got through.

Run: python tests/smoke_windows.py   (it really locks the keyboard for ~3 seconds)
"""
import ctypes
import os
import subprocess
import sys
import time

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "keyboard_freezer.py")
user32 = ctypes.WinDLL("user32")
VK_F24, KEYEVENTF_KEYUP = 0x87, 0x0002


def f24_gets_through() -> bool:
    user32.keybd_event(VK_F24, 0, 0, 0)
    time.sleep(0.1)
    down = bool(user32.GetAsyncKeyState(VK_F24) & 0x8000)
    user32.keybd_event(VK_F24, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.1)
    return down


def main() -> int:
    before = f24_gets_through()
    proc = subprocess.Popen([sys.executable, SCRIPT, "-s", "3"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        time.sleep(1.0)
        during = [f24_gets_through() for _ in range(3)]
        out, _ = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()  # never leave a hidden process holding the keyboard
    after = f24_gets_through()

    sys.stdout.buffer.write(out)
    print(f"\nexit code {proc.returncode} | before: {before} | during: {during} | after: {after}")
    if not before:
        print("SKIP: injected keys don't register on this desktop, so the test can't tell anything.")
        return 0
    ok = proc.returncode == 0 and not any(during) and after and b"You hit 3 keys" in out
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
