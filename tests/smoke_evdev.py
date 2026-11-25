"""Linux/evdev end-to-end check with a virtual keyboard. Needs root and /dev/uinput:

    sudo modprobe uinput
    sudo python3 tests/smoke_evdev.py

Creates a fake keyboard, opens it as a "spy" reader (like a desktop would), freezes for 4 s,
types 3 keys, and checks: the freezer swallowed them, the spy saw nothing, and keys flow again after.
"""
import fcntl
import os
import struct
import subprocess
import sys
import time

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "keyboard_freezer.py")
UI_SET_EVBIT, UI_SET_KEYBIT, UI_DEV_CREATE, UI_DEV_DESTROY = 0x40045564, 0x40045565, 0x5501, 0x5502
EV_SYN, EV_KEY = 0, 1
KEY_A, KEY_B, KEY_C = 30, 48, 46
EVENT = struct.Struct("llHHi")
NAME = b"keyboard-freezer test keyboard"


def make_keyboard() -> int:
    ui = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    fcntl.ioctl(ui, UI_SET_EVBIT, EV_KEY)
    for code in range(1, 128):
        fcntl.ioctl(ui, UI_SET_KEYBIT, code)
    os.write(ui, struct.pack("80sHHHHI256i", NAME, 0x03, 0x1234, 0x5678, 1, 0, *([0] * 256)))
    fcntl.ioctl(ui, UI_DEV_CREATE)
    return ui


def find_node() -> str:
    for _ in range(50):
        with open("/proc/bus/input/devices") as f:
            for block in f.read().split("\n\n"):
                if NAME.decode() in block:
                    for line in block.splitlines():
                        if line.startswith("H: Handlers="):
                            return next(h for h in line.split("=", 1)[1].split() if h.startswith("event"))
        time.sleep(0.1)
    raise RuntimeError("virtual keyboard did not show up")


def tap(ui: int, code: int) -> None:
    for value in (1, 0):
        os.write(ui, EVENT.pack(0, 0, EV_KEY, code, value))
        os.write(ui, EVENT.pack(0, 0, EV_SYN, 0, 0))


def presses_seen(fd: int) -> int:
    n = 0
    while True:
        try:
            data = os.read(fd, EVENT.size * 64)
        except BlockingIOError:
            return n
        for off in range(0, len(data), EVENT.size):
            _, _, ev_type, _, value = EVENT.unpack_from(data, off)
            n += ev_type == EV_KEY and value == 1


def main() -> int:
    if not os.path.exists("/dev/uinput"):
        print("::warning::/dev/uinput is not available here; evdev smoke test skipped")
        return 0
    ui = make_keyboard()
    try:
        node = find_node()
        time.sleep(0.5)
        spy = os.open(f"/dev/input/{node}", os.O_RDONLY | os.O_NONBLOCK)
        proc = subprocess.Popen([sys.executable, SCRIPT, "-s", "4", "--backend", "evdev"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            time.sleep(1.5)
            presses_seen(spy)  # drain
            for code in (KEY_A, KEY_B, KEY_C):
                tap(ui, code)
            time.sleep(0.5)
            during = presses_seen(spy)
            out, _ = proc.communicate(timeout=20)
        finally:
            if proc.poll() is None:
                proc.kill()
        time.sleep(0.3)
        presses_seen(spy)
        tap(ui, KEY_A)
        time.sleep(0.3)
        after = presses_seen(spy)
    finally:
        fcntl.ioctl(ui, UI_DEV_DESTROY)
        os.close(ui)

    text = out.decode("utf-8", "replace")
    print(text)
    print(f"device {node} | exit code {proc.returncode} | spy saw during freeze: {during} | after: {after}")
    ok = proc.returncode == 0 and "You hit 3 keys" in text and during == 0 and after == 1
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
