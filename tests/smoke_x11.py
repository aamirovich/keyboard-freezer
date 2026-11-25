"""Linux/X11 end-to-end check. Needs a display and xdotool:  xvfb-run -a python tests/smoke_x11.py

Freezes for 4 s with the X11 grab, types 3 keys with xdotool and expects the freezer
to have swallowed exactly those 3 keys.
"""
import os
import subprocess
import sys
import time

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "keyboard_freezer.py")


def main() -> int:
    proc = subprocess.Popen([sys.executable, SCRIPT, "-s", "4", "--backend", "x11"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        time.sleep(1.5)
        subprocess.run(["xdotool", "key", "a", "b", "c"], check=True)
        out, _ = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
    text = out.decode("utf-8", "replace")
    print(text)
    ok = proc.returncode == 0 and "You hit 3 keys" in text
    print("PASS" if ok else f"FAIL (exit code {proc.returncode})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
