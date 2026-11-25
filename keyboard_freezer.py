#!/usr/bin/env python3
"""
keyboard-freezer: lock your keyboard for a few minutes so you can clean it
while your AI coding agent keeps working.

    python keyboard_freezer.py          # 5 minutes
    python keyboard_freezer.py 2        # 2 minutes
    python keyboard_freezer.py -s 30    # 30 seconds

Only the keyboard is locked; the mouse keeps working.
Emergency exit: close this terminal window with the mouse.
Zero dependencies, standard library only.
"""
from __future__ import annotations

import argparse
import ctypes
import math
import os
import sys
import threading
import time

__version__ = "1.0.0"

DEFAULT_MINUTES = 5.0
MAX_MINUTES = 60.0
WATCHDOG_GRACE_SECONDS = 15.0


class FreezeError(Exception):
    """A problem we can explain to the user in plain words."""


# --------------------------------------------------------------------------- output

def _setup_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def _fancy() -> bool:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in encoding


def say(text: str = "", end: str = "\n") -> None:
    """Print that can never crash the program (the unlock must not depend on the UI)."""
    try:
        sys.stdout.write(text + end)
        sys.stdout.flush()
    except Exception:
        pass


def icon(fancy: str, plain: str) -> str:
    return fancy if _fancy() else plain


def mmss(seconds: float) -> str:
    m, s = divmod(max(0, math.ceil(seconds)), 60)
    return f"{m:02d}:{s:02d}"


# --------------------------------------------------------------------------- Windows

class WindowsBackend:
    """Global low-level keyboard hook (WH_KEYBOARD_LL). No admin rights needed."""

    name = "Windows low-level keyboard hook"

    WH_KEYBOARD_LL = 13
    HC_ACTION = 0
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104
    PM_NOREMOVE = 0x0000
    PM_REMOVE = 0x0001
    QS_ALLINPUT = 0x04FF

    def __init__(self) -> None:
        from ctypes import wintypes

        self.wintypes = wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        LRESULT = ctypes.c_ssize_t
        self.HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        self.KBDLLHOOKSTRUCT = KBDLLHOOKSTRUCT

        user32.SetWindowsHookExW.argtypes = (ctypes.c_int, self.HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.PeekMessageW.argtypes = (
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT)
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.MsgWaitForMultipleObjects.argtypes = (
            wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, wintypes.DWORD)
        user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD
        user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        user32.GetAsyncKeyState.restype = ctypes.c_short
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        self.user32 = user32
        self.kernel32 = kernel32
        self.msg = wintypes.MSG()
        self.blocked = 0
        self._down: set[int] = set()
        self._held_at_start: set[int] = set()
        self._hook = None
        self._proc = self.HOOKPROC(self._callback)  # keep a reference or it gets garbage-collected

    def keys_held(self) -> set[int]:
        # 0x08 and up are keyboard keys; 0x01-0x06 are mouse buttons.
        return {vk for vk in range(0x08, 0x100) if self.user32.GetAsyncKeyState(vk) & 0x8000}

    def _callback(self, n_code, w_param, l_param):
        if n_code == self.HC_ACTION:
            vk = ctypes.cast(l_param, ctypes.POINTER(self.KBDLLHOOKSTRUCT)).contents.vkCode
            if w_param in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN):
                if vk not in self._down:  # count presses, not auto-repeat
                    self._down.add(vk)
                    self.blocked += 1
                return 1  # swallow the key
            self._down.discard(vk)
            if vk in self._held_at_start:
                # This key was already down when we locked. Let its release through,
                # otherwise Windows would think it is stuck.
                self._held_at_start.discard(vk)
            else:
                return 1
        return self.user32.CallNextHookEx(None, n_code, w_param, l_param)

    def lock(self) -> None:
        # Make sure this thread has a message queue before installing the hook.
        self.user32.PeekMessageW(ctypes.byref(self.msg), None, 0, 0, self.PM_NOREMOVE)
        self._held_at_start = self.keys_held()
        self._hook = self.user32.SetWindowsHookExW(
            self.WH_KEYBOARD_LL, self._proc, self.kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            raise FreezeError(f"Could not install the keyboard hook: {ctypes.WinError(ctypes.get_last_error())}")

    def pump(self, timeout: float) -> None:
        # The hook only runs while this thread is pumping messages.
        self.user32.MsgWaitForMultipleObjects(0, None, False, max(0, int(timeout * 1000)), self.QS_ALLINPUT)
        while self.user32.PeekMessageW(ctypes.byref(self.msg), None, 0, 0, self.PM_REMOVE):
            pass

    def unlock(self) -> None:
        if self._hook:
            self.user32.UnhookWindowsHookEx(self._hook)
            self._hook = None


# --------------------------------------------------------------------------- core

def get_backend():
    if sys.platform == "win32":
        return WindowsBackend()
    raise FreezeError(f"Sorry, {sys.platform} is not supported yet. Windows only for now.")


def wait_for_release(backend, timeout: float = 3.0) -> None:
    """Don't lock while a key (e.g. the Enter that started us) is still held down."""
    start = time.monotonic()
    warned = False
    while backend.keys_held():
        waited = time.monotonic() - start
        if waited > timeout:
            return
        if waited > 0.4 and not warned:
            say("Let go of the keyboard...")
            warned = True
        time.sleep(0.02)


def outro(blocked: int) -> str:
    if blocked == 0:
        return "Zero keys pressed. Did you actually clean it? " + icon("🤨", ":|")
    if blocked < 50:
        return f"You hit {blocked} keys while cleaning. Your AI agent saw none of them."
    return f"{blocked} keys smashed! That keyboard needed it. Your AI agent saw none of them."


def freeze(duration: float) -> int:
    backend = get_backend()
    wait_for_release(backend)

    # Last line of defence: if anything hangs, kill the process after the deadline.
    # A dead process always releases the keyboard.
    watchdog = threading.Timer(duration + WATCHDOG_GRACE_SECONDS, lambda: os._exit(3))
    watchdog.daemon = True

    deadline = time.monotonic() + duration
    backend.lock()
    watchdog.start()
    tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    try:
        say()
        say(f"  {icon('❄️ ', '*')} KEYBOARD FROZEN for {mmss(duration)}")
        say("  Scrub away. Your AI agent keeps coding, your crumbs go nowhere.")
        say("  Emergency exit: close this window with the mouse.")
        say()
        last_report = -1.0
        while (remaining := deadline - time.monotonic()) > 0:
            if tty:
                say(f"\r  {icon('⏳', '>')} {mmss(remaining)} left   "
                    f"{icon('🧽', '#')} {backend.blocked} keys swallowed   ", end="")
            elif last_report < 0 or last_report - remaining >= 60:
                say(f"  {mmss(remaining)} left")
                last_report = remaining
            backend.pump(min(0.25, remaining))
    finally:
        backend.unlock()
        watchdog.cancel()

    if tty:
        say()
    say()
    say(f"  {icon('✅', 'OK')} Keyboard is back.")
    say(f"  {outro(backend.blocked)}")
    return 0


def parse_duration(argv: list[str] | None = None) -> float:
    parser = argparse.ArgumentParser(
        prog="keyboard-freezer",
        description="Lock your keyboard for a few minutes so you can clean it "
                    "while your AI agent keeps coding.",
    )
    parser.add_argument("minutes", nargs="?", type=float, default=DEFAULT_MINUTES,
                        help=f"how long to freeze, in minutes (default: {DEFAULT_MINUTES:g})")
    parser.add_argument("-s", "--seconds", type=float, help="how long to freeze, in seconds")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    duration = args.seconds if args.seconds is not None else args.minutes * 60
    if not math.isfinite(duration) or duration <= 0:
        parser.error("duration must be a positive number")
    if duration > MAX_MINUTES * 60:
        parser.error(f"duration can be at most {MAX_MINUTES:g} minutes (nobody cleans that long)")
    return duration


def main(argv: list[str] | None = None) -> int:
    _setup_output()
    duration = parse_duration(argv)
    try:
        return freeze(duration)
    except FreezeError as e:
        say(f"keyboard-freezer: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
