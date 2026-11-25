"""Unit tests that run anywhere, without locking anything. Run: python -m unittest discover -s tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import keyboard_freezer as kf  # noqa: E402

# Trimmed from a real /proc/bus/input/devices: a power button, a laptop keyboard and a mouse.
PROC_DEVICES = """\
I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
P: Phys=PNP0C0C/button/input0
H: Handlers=kbd event0
B: PROP=0
B: EV=3
B: KEY=10000000000000 0

I: Bus=0011 Vendor=0001 Product=0001 Version=ab41
N: Name="AT Translated Set 2 keyboard"
P: Phys=isa0060/serio0/input0
H: Handlers=sysrq kbd event3 leds
B: PROP=0
B: EV=120013
B: KEY=402000000 3803078f800d001 feffffdfffefffff fffffffffffffffe
B: MSC=10
B: LED=7

I: Bus=0003 Vendor=046d Product=c52b Version=0111
N: Name="Logitech USB Receiver Mouse"
H: Handlers=mouse0 event5
B: PROP=0
B: EV=17
B: KEY=ffff0000 0 0 0 0
B: REL=1943
"""


class ParseArgs(unittest.TestCase):
    def test_default_is_five_minutes(self):
        self.assertEqual(kf.parse_args([]), (300.0, "auto"))

    def test_minutes_and_seconds(self):
        self.assertEqual(kf.parse_args(["2"])[0], 120.0)
        self.assertEqual(kf.parse_args(["0.5"])[0], 30.0)
        self.assertEqual(kf.parse_args(["-s", "45"])[0], 45.0)

    def test_backend_choice(self):
        self.assertEqual(kf.parse_args(["--backend", "x11"])[1], "x11")

    def test_rejects_nonsense(self):
        for bad in (["inf"], ["nan"], ["0"], ["-1"], ["61"], ["-s", "0"], ["-s", "inf"], ["abc"]):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                kf.parse_args(bad)


class Formatting(unittest.TestCase):
    def test_mmss(self):
        self.assertEqual(kf.mmss(300), "05:00")
        self.assertEqual(kf.mmss(59.2), "01:00")
        self.assertEqual(kf.mmss(0.2), "00:01")
        self.assertEqual(kf.mmss(-3), "00:00")

    def test_outro(self):
        self.assertIn("Did you actually clean it", kf.outro(0))
        self.assertIn("You hit 3 keys", kf.outro(3))
        self.assertIn("137 keys smashed", kf.outro(137))


class LinuxKeyboardDetection(unittest.TestCase):
    def test_picks_only_the_real_keyboard(self):
        self.assertEqual(kf.parse_keyboards(PROC_DEVICES, 64), ["event3"])

    def test_32_bit_words(self):
        block = "H: Handlers=kbd event7\nB: KEY=ffffffff fffffffe\n"
        self.assertEqual(kf.parse_keyboards(block, 32), ["event7"])

    def test_empty(self):
        self.assertEqual(kf.parse_keyboards("", 64), [])


class Backends(unittest.TestCase):
    def test_auto_backend_builds_or_explains(self):
        """On every OS, picking a backend either works or fails with a friendly FreezeError."""
        try:
            backend = kf.get_backend()
        except kf.FreezeError as e:
            self.assertTrue(str(e))
            return
        self.assertIsInstance(backend.keys_held(), set)
        backend.unlock()  # never locked; must be harmless

    def test_foreign_backend_fails_politely(self):
        foreign = "macos" if sys.platform == "win32" else "windows"
        with self.assertRaises(kf.FreezeError):
            kf.get_backend(foreign)


if __name__ == "__main__":
    unittest.main()
