<p align="center">
  <img src="assets/meme.svg" alt="Three-panel comic. Them: That keyboard is DISGUSTING. Clean it. Now. You: I can't turn it off! Claude Code is 47 files into a refactor. keyboard-freezer: Why not both?" width="100%">
</p>

<h1 align="center">❄️ keyboard-freezer</h1>

<p align="center">
  <b>Freeze the keys, not the agent.</b><br>
  Lock your keyboard for 5 minutes so you can clean it, while your AI agent keeps coding.
</p>

<p align="center">
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-3776ab">
  <img alt="Zero dependencies" src="https://img.shields.io/badge/dependencies-zero-2ea44f">
  <img alt="Platforms: Windows, macOS, Linux" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-0078d4">
  <a href="https://github.com/aamirovich/keyboard-freezer/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/aamirovich/keyboard-freezer/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-lightgrey">
</p>

---

## The problem

It's 2026. Your AI agent (Claude Code, ChatGPT, Codex, Cursor, you name it) has been refactoring your codebase for 40 minutes. It's on file 47. The tests are almost green.

Then someone who loves you (your partner, your mom, your roommate with opinions) glances at your desk and says:

> "That keyboard is disgusting. Clean it. **Now.**"

And they're right. There's a whole ecosystem in there.

But:

- 🔌 **You can't shut the computer down.** The agent is mid-task. You'd lose 40 minutes of progress and the agent's train of thought.
- 🧽 **You can't wipe it while it's on.** Every swipe of the cloth types `jjjjjjjjj;;;;;;;;;` into the agent's prompt, hits Enter, and approves something you'll regret.
- 💻 **You can't unplug it.** It's a laptop. Or it's a desktop, but the cable is behind the desk, behind the cat.

## The fix

```bash
python keyboard_freezer.py
```

<p align="center">
  <img src="assets/demo.svg" alt="Terminal showing: KEYBOARD FROZEN for 05:00, a countdown, and a counter of swallowed keys" width="100%">
</p>

For 5 minutes, every key you press goes straight into the void. Your agent keeps working, the mouse keeps working, and nothing you scrub ends up in a prompt.

When time's up, the keyboard comes back on its own and tells you how many keys you smashed:

```text
  ✅ Keyboard is back.
  137 keys smashed! That keyboard needed it. Your AI agent saw none of them.
```

(Press nothing at all and it will ask whether you actually cleaned it. 🤨)

## Quick start

You need Python 3.9 or newer. No `pip install`, no dependencies: it's one file and only uses the standard library.

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/aamirovich/keyboard-freezer/main/keyboard_freezer.py -OutFile keyboard_freezer.py
python keyboard_freezer.py
```

If `.py` files open with Python on your machine, double-clicking `keyboard_freezer.py` works too; the window closes by itself when the keyboard is back.

**macOS:**

```bash
curl -O https://raw.githubusercontent.com/aamirovich/keyboard-freezer/main/keyboard_freezer.py
python3 keyboard_freezer.py
```

The first time, macOS asks you to allow **Accessibility** for your terminal app (System Settings → Privacy & Security → Accessibility). Switch it on, then run the command again. That's macOS protecting you from programs that mess with your keyboard, which is exactly what this one does, politely.

**Linux:**

```bash
curl -O https://raw.githubusercontent.com/aamirovich/keyboard-freezer/main/keyboard_freezer.py
sudo python3 keyboard_freezer.py
```

`sudo` lets it grab the keyboard at the device level, which works everywhere: X11, Wayland, even the text console. If you're in the `input` group you don't need `sudo`. On an X11 desktop it also works without `sudo`, using an X11 keyboard grab instead.

**As a command, on any OS:**

```bash
pip install git+https://github.com/aamirovich/keyboard-freezer
keyboard-freezer
```

## Usage

```bash
python keyboard_freezer.py          # 5 minutes (the default)
python keyboard_freezer.py 2        # 2 minutes
python keyboard_freezer.py 0.5      # 30 seconds
python keyboard_freezer.py -s 45    # 45 seconds
```

The maximum is 60 minutes. Nobody cleans a keyboard for longer than that. We checked.

## Emergency exit

Changed your mind? Need to type something urgently? You have options:

1. **Close the terminal window with the mouse.** The keyboard unlocks instantly, on every OS. This is the official panic button.
2. **Windows:** Ctrl+Alt+Del always works; Windows doesn't let any program block it. From there, open Task Manager and end `python`.
3. **The watchdog.** Even if something goes badly wrong, the program kills itself 15 seconds after the timer ends. A dead process can't hold your keyboard hostage: the operating system releases the hook or grab the moment the process dies.

## How it works

Everything lives in one file, [`keyboard_freezer.py`](keyboard_freezer.py), written with nothing but the standard library and `ctypes`:

| OS | How the keys get swallowed |
| --- | --- |
| Windows | A global low-level keyboard hook (`WH_KEYBOARD_LL`) that answers "handled" for every key. No admin rights. |
| macOS | A Quartz event tap that drops key events before any app sees them. Needs the Accessibility permission. |
| Linux | An exclusive `EVIOCGRAB` on every keyboard in `/dev/input`, so only the freezer receives keys. Without `sudo` on X11: `XGrabKeyboard` on the root window. |

A few details that matter more than they sound:

- **No stuck keys.** It waits for you to let go of Enter before locking. Without that, the system could think the key is still held down after the freeze (on Linux, it would even auto-repeat it).
- **The unlock never depends on the UI.** The deadline lives in the main loop, printing can't crash it, and the watchdog backs it all up.
- **It counts, it doesn't log.** Key codes are only used to tell a press from a release. Nothing is stored, written to disk, or sent anywhere.

## Platform support

Every push runs real freezes in [CI](https://github.com/aamirovich/keyboard-freezer/actions/workflows/ci.yml): it injects key presses while the keyboard is frozen and checks they were swallowed.

| Platform | Status | How it's verified |
| --- | --- | --- |
| Windows 10 / 11 | ✅ Works | On a real Windows 11 machine and in CI: injected keys blocked during the freeze, delivered again after. |
| macOS | ✅ Works (after you allow Accessibility) | In CI on GitHub's macOS runner: 3 posted key presses swallowed. Not yet tried by hand on a physical Mac. |
| Linux with `sudo` (X11, Wayland, console) | ✅ Works | In CI with a virtual keyboard: keys swallowed, a second reader saw nothing during the freeze, keys flowed again after. |
| Linux X11 without `sudo` | ✅ Works | In CI under Xvfb: 3 `xdotool` key presses swallowed. |
| Linux Wayland without `sudo` | ❌ Needs `sudo` or the `input` group | Wayland doesn't let normal apps grab the keyboard. The freezer tells you exactly that. |

Python 3.9+ (3.9 is checked on Windows; 3.10 and 3.13 on macOS; 3.13 on Linux).

Found a setup where it doesn't work? [Open an issue](https://github.com/aamirovich/keyboard-freezer/issues). Bonus points for a photo of the keyboard.

## FAQ

**Is this a keylogger?**
No. It never records which keys you press, only how many, so it can roast you at the end. It's one file with no dependencies; read it before you run it, especially before you run it with `sudo`.

**Why does Linux want `sudo`?**
Wayland, by design, doesn't let a normal app grab the whole keyboard. The only way that works on every Linux desktop is to grab the keyboard device itself, and that's root's job (or the `input` group's).

**Why isn't there an `.exe`?**
A tiny unsigned program that hooks your keyboard looks exactly like malware to every antivirus on earth. A Python file you can read is more trustworthy than a binary you can't.

**Will it stop my AI agent?**
No. It only eats keyboard input. Every program keeps running, including the agent, its terminal, and your music.

**My agent runs in an Administrator terminal on Windows. Does that matter?**
Probably, yes. Windows keeps normal programs from interfering with Administrator windows, and that most likely includes this hook. If your agent's terminal runs as Administrator, start the freezer from an Administrator terminal too.

**My laptop's brightness and volume keys still work during the freeze.**
Some laptops handle `Fn` key combos in firmware, before the operating system sees them. Nothing a normal program can do about that. Wipe gently.

**My cat walks on my keyboard. Can I use this?**
Absolutely. This is also a cat tool. 🐈

## License

[MIT](LICENSE). Clean responsibly.
