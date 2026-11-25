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

Windows: low-level keyboard hook.  macOS: Quartz event tap (needs Accessibility permission).
Linux: evdev grab (sudo or the 'input' group; works on X11, Wayland and the console),
       or an X11 keyboard grab without sudo as a fallback.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import math
import os
import struct
import sys
import threading
import time

__version__ = "1.1.0"

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


# --------------------------------------------------------------------------- macOS

class MacBackend:
    """Quartz event tap that drops keyboard events. Needs the Accessibility permission."""

    name = "macOS Quartz event tap"

    CORE_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    APP_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"

    kCGSessionEventTap = 1
    kCGHeadInsertEventTap = 0
    kCGEventTapOptionDefault = 0
    kCGEventKeyDown = 10
    kCGEventKeyUp = 11
    kCGEventFlagsChanged = 12
    kCGEventTapDisabledByTimeout = 0xFFFFFFFE
    kCGEventTapDisabledByUserInput = 0xFFFFFFFF
    kCGKeyboardEventAutorepeat = 8
    kCGKeyboardEventKeycode = 9
    kCGEventSourceStateHIDSystemState = 1
    kVK_CapsLock = 57  # a toggle, not "held"

    def __init__(self) -> None:
        try:
            cg = ctypes.CDLL(self.CORE_GRAPHICS)
            cf = ctypes.CDLL(self.CORE_FOUNDATION)
        except OSError as e:
            raise FreezeError(f"Could not load the macOS frameworks: {e}")

        vp = ctypes.c_void_p
        self.CALLBACK = ctypes.CFUNCTYPE(vp, vp, ctypes.c_uint32, vp, vp)
        cg.CGEventTapCreate.argtypes = (ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint64,
                                        self.CALLBACK, vp)
        cg.CGEventTapCreate.restype = vp
        cg.CGEventTapEnable.argtypes = (vp, ctypes.c_bool)
        cg.CGEventTapEnable.restype = None
        cg.CGEventGetIntegerValueField.argtypes = (vp, ctypes.c_uint32)
        cg.CGEventGetIntegerValueField.restype = ctypes.c_int64
        cg.CGEventSourceKeyState.argtypes = (ctypes.c_int32, ctypes.c_uint16)
        cg.CGEventSourceKeyState.restype = ctypes.c_bool
        cf.CFMachPortCreateRunLoopSource.argtypes = (vp, vp, ctypes.c_long)
        cf.CFMachPortCreateRunLoopSource.restype = vp
        cf.CFMachPortInvalidate.argtypes = (vp,)
        cf.CFMachPortInvalidate.restype = None
        cf.CFRunLoopGetCurrent.argtypes = ()
        cf.CFRunLoopGetCurrent.restype = vp
        cf.CFRunLoopAddSource.argtypes = (vp, vp, vp)
        cf.CFRunLoopAddSource.restype = None
        cf.CFRunLoopRemoveSource.argtypes = (vp, vp, vp)
        cf.CFRunLoopRemoveSource.restype = None
        cf.CFRunLoopRunInMode.argtypes = (vp, ctypes.c_double, ctypes.c_bool)
        cf.CFRunLoopRunInMode.restype = ctypes.c_int32
        cf.CFRelease.argtypes = (vp,)
        cf.CFRelease.restype = None

        self.cg, self.cf = cg, cf
        self.default_mode = vp.in_dll(cf, "kCFRunLoopDefaultMode")
        self.common_modes = vp.in_dll(cf, "kCFRunLoopCommonModes")
        self.blocked = 0
        self._held_at_start: set[int] = set()
        self._tap = None
        self._source = None
        self._loop = None
        self._proc = self.CALLBACK(self._callback)

    def keys_held(self) -> set[int]:
        state = self.kCGEventSourceStateHIDSystemState
        return {k for k in range(128) if k != self.kVK_CapsLock and self.cg.CGEventSourceKeyState(state, k)}

    def _callback(self, proxy, event_type, event, refcon):
        if event_type in (self.kCGEventTapDisabledByTimeout, self.kCGEventTapDisabledByUserInput):
            if self._tap:
                self.cg.CGEventTapEnable(self._tap, True)  # macOS turned us off; turn back on
            return event
        if event_type == self.kCGEventKeyDown:
            if not self.cg.CGEventGetIntegerValueField(event, self.kCGKeyboardEventAutorepeat):
                self.blocked += 1
            return None  # drop it
        if event_type == self.kCGEventKeyUp:
            key = self.cg.CGEventGetIntegerValueField(event, self.kCGKeyboardEventKeycode)
            if key in self._held_at_start:
                self._held_at_start.discard(key)
                return event
            return None
        if event_type == self.kCGEventFlagsChanged:
            return None
        return event

    def _ask_for_permission(self) -> None:
        """Pops up macOS's own 'allow Accessibility' dialog. Best effort."""
        try:
            ax = ctypes.CDLL(self.APP_SERVICES)
            vp = ctypes.c_void_p
            keys = (vp * 1)(vp.in_dll(ax, "kAXTrustedCheckOptionPrompt").value)
            values = (vp * 1)(vp.in_dll(self.cf, "kCFBooleanTrue").value)
            self.cf.CFDictionaryCreate.argtypes = (vp, vp, vp, ctypes.c_long, vp, vp)
            self.cf.CFDictionaryCreate.restype = vp
            options = self.cf.CFDictionaryCreate(
                None, keys, values, 1,
                ctypes.addressof(ctypes.c_char.in_dll(self.cf, "kCFTypeDictionaryKeyCallBacks")),
                ctypes.addressof(ctypes.c_char.in_dll(self.cf, "kCFTypeDictionaryValueCallBacks")))
            ax.AXIsProcessTrustedWithOptions.argtypes = (vp,)
            ax.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
            ax.AXIsProcessTrustedWithOptions(options)
            if options:
                self.cf.CFRelease(options)
        except Exception:
            pass

    def lock(self) -> None:
        self._held_at_start = self.keys_held()
        mask = (1 << self.kCGEventKeyDown) | (1 << self.kCGEventKeyUp) | (1 << self.kCGEventFlagsChanged)
        self._tap = self.cg.CGEventTapCreate(self.kCGSessionEventTap, self.kCGHeadInsertEventTap,
                                             self.kCGEventTapOptionDefault, mask, self._proc, None)
        if not self._tap:
            self._ask_for_permission()
            raise FreezeError(
                "macOS needs your permission first.\n"
                "  Open System Settings > Privacy & Security > Accessibility, switch on the app you run\n"
                "  this from (Terminal, iTerm, VS Code...), then run keyboard-freezer again.")
        self._source = self.cf.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._loop = self.cf.CFRunLoopGetCurrent()
        self.cf.CFRunLoopAddSource(self._loop, self._source, self.common_modes)
        self.cg.CGEventTapEnable(self._tap, True)

    def pump(self, timeout: float) -> None:
        self.cf.CFRunLoopRunInMode(self.default_mode, max(0.0, timeout), False)

    def unlock(self) -> None:
        if self._tap:
            self.cg.CGEventTapEnable(self._tap, False)
            if self._source:
                self.cf.CFRunLoopRemoveSource(self._loop, self._source, self.common_modes)
                self.cf.CFRelease(self._source)
                self._source = None
            self.cf.CFMachPortInvalidate(self._tap)
            self.cf.CFRelease(self._tap)
            self._tap = None


# --------------------------------------------------------------------------- Linux: evdev

def _word_bits() -> int:
    machine = os.uname().machine if hasattr(os, "uname") else ""
    return 64 if machine.endswith("64") or machine in ("x86_64", "aarch64", "s390x") else 32


def parse_keyboards(proc_devices: str, word_bits: int = 64) -> list[str]:
    """Pick real keyboards (they have letter keys and a space bar) out of /proc/bus/input/devices."""
    KEY_A, KEY_Z, KEY_SPACE = 30, 44, 57
    found = []
    for block in proc_devices.strip().split("\n\n"):
        handlers, key_words = [], []
        for line in block.splitlines():
            if line.startswith("H: Handlers="):
                handlers = line.split("=", 1)[1].split()
            elif line.startswith("B: KEY="):
                key_words = line.split("=", 1)[1].split()
        events = [h for h in handlers if h.startswith("event")]
        if "kbd" not in handlers or not events or not key_words:
            continue
        words = [int(w, 16) for w in reversed(key_words)]  # least significant word first

        def has(bit: int) -> bool:
            i, b = divmod(bit, word_bits)
            return i < len(words) and bool(words[i] >> b & 1)

        if has(KEY_A) and has(KEY_Z) and has(KEY_SPACE):
            found.append(events[0])
    return found


class LinuxEvdevBackend:
    """Exclusive grab (EVIOCGRAB) on every keyboard in /dev/input. Works on X11, Wayland and TTYs."""

    name = "Linux evdev grab"

    EVIOCGRAB = 0x40044590
    EV_KEY = 1
    EVENT = struct.Struct("llHHi")  # struct input_event

    @staticmethod
    def EVIOCGKEY(length: int) -> int:
        return (2 << 30) | (length << 16) | (ord("E") << 8) | 0x18

    def __init__(self) -> None:
        import fcntl
        import select

        self.fcntl, self.select = fcntl, select
        try:
            with open("/proc/bus/input/devices") as f:
                names = parse_keyboards(f.read(), _word_bits())
        except OSError as e:
            raise FreezeError(f"Could not list input devices: {e}")
        if not names:
            raise FreezeError("No keyboard found in /proc/bus/input/devices.")
        self.fds: list[int] = []
        denied = False
        for name in names:
            try:
                self.fds.append(os.open(f"/dev/input/{name}", os.O_RDONLY | os.O_NONBLOCK))
            except PermissionError:
                denied = True
            except OSError:
                pass
        if denied and not self.fds:
            raise FreezeError(
                "Linux needs access to /dev/input to lock the keyboard. Run it with sudo:\n"
                "    sudo python3 keyboard_freezer.py\n"
                "  (or add yourself to the 'input' group and log in again)")
        if not self.fds:
            raise FreezeError("Could not open any keyboard device.")
        self.blocked = 0
        self._grabbed: list[int] = []

    def keys_held(self) -> set[int]:
        held = set()
        for fd in self.fds:
            buf = bytearray(96)  # KEY_MAX / 8 + 1
            try:
                self.fcntl.ioctl(fd, self.EVIOCGKEY(len(buf)), buf, True)
            except OSError:
                continue
            for i, byte in enumerate(buf):
                held.update(i * 8 + b for b in range(8) if byte >> b & 1)
        return held

    def lock(self) -> None:
        for fd in self.fds:
            try:
                self.fcntl.ioctl(fd, self.EVIOCGRAB, 1)
                self._grabbed.append(fd)
            except OSError:
                pass  # e.g. another program already grabbed it
        if not self._grabbed:
            raise FreezeError("Could not grab any keyboard (is another program holding it?).")

    def pump(self, timeout: float) -> None:
        ready, _, _ = self.select.select(self._grabbed, [], [], max(0.0, timeout))
        for fd in ready:
            try:
                data = os.read(fd, self.EVENT.size * 64)
            except OSError:
                continue
            for off in range(0, len(data) - self.EVENT.size + 1, self.EVENT.size):
                _, _, ev_type, _, value = self.EVENT.unpack_from(data, off)
                if ev_type == self.EV_KEY and value == 1:  # 1 = press, 2 = auto-repeat
                    self.blocked += 1

    def unlock(self) -> None:
        for fd in self._grabbed:
            try:
                self.fcntl.ioctl(fd, self.EVIOCGRAB, 0)
            except OSError:
                pass
        self._grabbed = []
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds = []


# --------------------------------------------------------------------------- Linux: X11

class X11Backend:
    """XGrabKeyboard on the root window: every key comes to us instead of the apps. No sudo needed."""

    name = "X11 keyboard grab"

    GrabModeAsync = 1
    GrabSuccess = 0
    KeyPress = 2

    def __init__(self) -> None:
        import select

        self.select = select
        lib = ctypes.util.find_library("X11")
        if not lib:
            raise FreezeError("libX11 not found.")
        x = ctypes.CDLL(lib)
        vp = ctypes.c_void_p
        x.XOpenDisplay.argtypes = (ctypes.c_char_p,)
        x.XOpenDisplay.restype = vp
        x.XDefaultRootWindow.argtypes = (vp,)
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XGrabKeyboard.argtypes = (vp, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong)
        x.XGrabKeyboard.restype = ctypes.c_int
        x.XUngrabKeyboard.argtypes = (vp, ctypes.c_ulong)
        x.XPending.argtypes = (vp,)
        x.XPending.restype = ctypes.c_int
        x.XNextEvent.argtypes = (vp, vp)
        x.XQueryKeymap.argtypes = (vp, ctypes.c_char_p)
        x.XConnectionNumber.argtypes = (vp,)
        x.XConnectionNumber.restype = ctypes.c_int
        x.XFlush.argtypes = (vp,)
        x.XCloseDisplay.argtypes = (vp,)
        self.x = x
        self.dpy = x.XOpenDisplay(None)
        if not self.dpy:
            raise FreezeError("Could not connect to the X display.")
        try:
            x.XkbSetDetectableAutoRepeat.argtypes = (vp, ctypes.c_int, vp)
            x.XkbSetDetectableAutoRepeat(self.dpy, 1, None)  # don't count auto-repeat as presses
        except AttributeError:
            pass
        self.root = x.XDefaultRootWindow(self.dpy)
        self.event = ctypes.create_string_buffer(256)  # XEvent is 192 bytes on 64-bit
        self.blocked = 0
        self._grabbed = False

    def keys_held(self) -> set[int]:
        keymap = ctypes.create_string_buffer(32)
        self.x.XQueryKeymap(self.dpy, keymap)
        return {i * 8 + b for i, byte in enumerate(keymap.raw) for b in range(8) if byte >> b & 1}

    def lock(self) -> None:
        for _ in range(30):  # an open menu may hold the keyboard for a moment
            result = self.x.XGrabKeyboard(self.dpy, self.root, 0, self.GrabModeAsync, self.GrabModeAsync, 0)
            if result == self.GrabSuccess:
                self._grabbed = True
                return
            time.sleep(0.1)
        raise FreezeError("Could not grab the keyboard: another program is holding it.")

    def pump(self, timeout: float) -> None:
        if not self.x.XPending(self.dpy):
            self.select.select([self.x.XConnectionNumber(self.dpy)], [], [], max(0.0, timeout))
        while self.x.XPending(self.dpy):
            self.x.XNextEvent(self.dpy, self.event)
            if ctypes.c_int.from_buffer(self.event).value == self.KeyPress:
                self.blocked += 1

    def unlock(self) -> None:
        if self.dpy:
            if self._grabbed:
                self.x.XUngrabKeyboard(self.dpy, 0)
                self._grabbed = False
            self.x.XFlush(self.dpy)
            self.x.XCloseDisplay(self.dpy)
            self.dpy = None


# --------------------------------------------------------------------------- core

BACKENDS = {"windows": WindowsBackend, "macos": MacBackend, "evdev": LinuxEvdevBackend, "x11": X11Backend}


def _is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"


def get_backend(choice: str = "auto"):
    if choice != "auto":
        try:
            return BACKENDS[choice]()
        except FreezeError:
            raise
        except Exception as e:
            raise FreezeError(f"The '{choice}' backend can't run on this system ({e}).")
    if sys.platform == "win32":
        return WindowsBackend()
    if sys.platform == "darwin":
        return MacBackend()
    if sys.platform.startswith("linux"):
        try:
            return LinuxEvdevBackend()
        except FreezeError as evdev_error:
            # Without sudo we can still lock an X11 session. On Wayland an X11 grab
            # would only cover old X apps, so there we really need evdev.
            if os.environ.get("DISPLAY") and not _is_wayland():
                try:
                    return X11Backend()
                except FreezeError:
                    pass
            raise evdev_error
    raise FreezeError(f"Sorry, {sys.platform} is not supported yet.")


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


def freeze(duration: float, backend_choice: str = "auto") -> int:
    backend = get_backend(backend_choice)
    wait_for_release(backend)

    # Last line of defence: if anything hangs, kill the process after the deadline.
    # A dead process always releases the keyboard.
    watchdog = threading.Timer(duration + WATCHDOG_GRACE_SECONDS, lambda: os._exit(3))
    watchdog.daemon = True

    deadline = time.monotonic() + duration
    tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    watchdog.start()
    try:
        backend.lock()
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


def parse_args(argv: list[str] | None = None) -> tuple[float, str]:
    parser = argparse.ArgumentParser(
        prog="keyboard-freezer",
        description="Lock your keyboard for a few minutes so you can clean it "
                    "while your AI agent keeps coding.",
    )
    parser.add_argument("minutes", nargs="?", type=float, default=DEFAULT_MINUTES,
                        help=f"how long to freeze, in minutes (default: {DEFAULT_MINUTES:g})")
    parser.add_argument("-s", "--seconds", type=float, help="how long to freeze, in seconds")
    parser.add_argument("--backend", choices=["auto", *BACKENDS], default="auto",
                        help="how to lock the keyboard (default: pick the right one for this system)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    duration = args.seconds if args.seconds is not None else args.minutes * 60
    if not math.isfinite(duration) or duration <= 0:
        parser.error("duration must be a positive number")
    if duration > MAX_MINUTES * 60:
        parser.error(f"duration can be at most {MAX_MINUTES:g} minutes (nobody cleans that long)")
    return duration, args.backend


def main(argv: list[str] | None = None) -> int:
    _setup_output()
    duration, backend_choice = parse_args(argv)
    try:
        return freeze(duration, backend_choice)
    except FreezeError as e:
        say(f"keyboard-freezer: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
