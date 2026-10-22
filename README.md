# Speak Selected

**Select any text, anywhere on your screen. Press a shortcut. Hear it read aloud.**
Every application. Not just the ones that happen to support it.

![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D4.svg)
![100% local](https://img.shields.io/badge/runs-100%25%20locally-brightgreen.svg)

---

## Why this exists

macOS has had this for years, built in, called **Speak Selection**. Select text in literally
any app — a browser, an email, a PDF, a chat window — press a shortcut, and your Mac reads it
back to you. It is one of the most quietly essential accessibility features Apple ships, and
if you have never needed it, you have probably never noticed it.

Windows has nothing like it. The closest thing — "Read Aloud" in Microsoft Word — only works
inside Word. Outside of it, there is nothing. Not in your browser, not in your email client,
not in a PDF reader, not anywhere else you actually spend your day reading.

This was built by someone who has **XLRS (X-linked retinoschisis)**, a genetic low-vision
condition, and who relied on Speak Selection every single day on a Mac — then lost it
completely when a Zoom bug forced a move to Windows. The gap wasn't a minor inconvenience.
It was the difference between being able to read something comfortably and not, between
finishing a page and getting a headache halfway through it.

So it got built properly instead — system-wide, exactly like the feature it was always meant
to replace, not an app-by-app compromise. After using the finished tool for real, the honest
summary is simple: **I can actually fully read stuff again, without the splitting headaches.**
That's the whole reason this exists, and the whole reason it's now public instead of sitting
in a personal folder — something that helps this much shouldn't be kept to one person.

## Who this is for

Anyone who finds reading on-screen text tiring, uncomfortable, or genuinely difficult —
low vision, dyslexia, eye strain, or simply preferring to listen rather than read. Anyone who
used and misses Speak Selection on a Mac. Anyone who just wants a reliable, private,
system-wide "read this to me" button on Windows that isn't locked to one application.

## What it does

- Select text in **any** application — a browser, a PDF, an email, a chat window, an IDE,
  a game's text box, literally anywhere you can select text — press **Ctrl+Alt+Z**, and it's
  read aloud.
- Full playback control, all from the keyboard or a small floating panel that appears while
  it's speaking: pause/resume, stop, skip to the previous or next sentence, repeat the whole
  selection from the start, speed up or slow down, and switch voices — mid-sentence, with
  the change taking effect immediately, not after the current sentence finishes.
- **Two voice engines, so it always works.** The enhanced voice — 54 voices across 9 languages
  (American & British English, Spanish, French, Hindi, Italian, Japanese, Brazilian Portuguese,
  Mandarin Chinese) — is the default whenever it's available. If it's ever missing or fails to
  load for any reason, Speak Selected automatically falls back to Windows' own built-in voice
  instead of refusing to speak at all. Settings lets you pick a specific voice either way, or
  force one engine explicitly if you'd rather.
- Lives quietly in the system tray with a status icon, starts automatically at login, and
  shows a short one-time welcome screen explaining the shortcuts the first time it runs.
- A native-styled, draggable floating panel that remembers where you put it, and opens
  Settings right next to wherever it is — not at some arbitrary spot across a second monitor.

## How it works

When you press the shortcut, Speak Selected simulates a copy command, reads what lands on the
clipboard, speaks it, then puts whatever was on the clipboard before back exactly as it was —
so it never disturbs your actual clipboard contents. The speech itself comes from
[Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), a small, high-quality open-weight
text-to-speech model that runs entirely on your own CPU, synthesizing one sentence at a time
and preparing the next one in the background while the current one plays, so longer passages
read continuously instead of pausing between every sentence.

Kokoro is the preferred voice, but never a hard requirement. Windows ships its own built-in
text-to-speech engine on every install, and Speak Selected uses that as an always-available
fallback — so a missing or corrupted model file degrades to a lower-quality voice instead of
the app refusing to start. For something meant to be depended on daily, that distinction
matters more than it might sound like it should.

This clipboard-based approach was a deliberate choice over trying to read an app's internal
accessibility tree directly. Windows has several different, incompatible accessibility APIs
(UI Automation, MSAA, IAccessible2) and no single one of them is implemented consistently
across every application — simulated copy is the one mechanism that works almost everywhere,
because almost everything that lets you select text also lets you copy it.

## Your privacy, not a slogan

- **Nothing is ever sent anywhere.** No cloud service, no account, no internet connection
  used at any point, by design — not for the speech itself, not for anything else.
- The text-to-speech voice model runs **entirely on your own machine**. The text you select
  never leaves your computer, in any form.
- No telemetry, no analytics, no "usage data," no crash reporting to a third party. If
  something goes wrong, it's logged to a plain text file on your own disk, for you to read —
  never transmitted.
- What you choose to read, and when, is nobody's business but yours. That's not a marketing
  line — it's the actual design.

## Install

1. Go to [Releases](../../releases) and download `SpeakSelected.msi`.
2. Run it. No administrator rights needed, nothing else to install first — it's fully
   self-contained.
3. Windows may show a blue "Windows protected your PC" screen the first time. This happens to
   independently-published software without an expensive commercial code-signing certificate
   — it is not a sign anything is wrong. Click **More info**, then **Run anyway**.
4. A short welcome screen explains the shortcuts. You're done — no setup, no accounts, no
   configuration required to start using it immediately.

## Using it

**Reading**
| Shortcut | Does |
|---|---|
| `Ctrl+Alt+Z` | Speak the selected text |
| `Ctrl+Alt+X` | Pause / resume |
| `Ctrl+Alt+C` | Stop |

**Navigating what's being read**
| Shortcut | Does |
|---|---|
| `Ctrl+Alt+Home` | Repeat the current selection from the start |
| `Ctrl+Alt+PageUp` | Previous sentence |
| `Ctrl+Alt+PageDown` | Next sentence |

**Voice and speed**
| Shortcut | Does |
|---|---|
| `Ctrl+Alt+N` | Cycle voice |
| `Ctrl+Alt+]` | Speed up |
| `Ctrl+Alt+[` | Slow down |
| `Ctrl+Alt+,` | Open Settings (pick a specific language and voice) |

Every one of these also has a matching button on the floating panel and the system tray
menu — nothing here requires memorizing keys to use the tool at all.

These are the defaults, not fixed — every shortcut can be changed in Settings →
**Keyboard Shortcuts**, since there's no way to guarantee Ctrl+Alt+anything won't already be
used by something else on your particular setup.

## Known limitations

Honest, not hidden:

- Clipboard-copy works almost everywhere, but not in applications that deliberately disable
  copying (some DRM-protected readers, for instance).
- All 54 enhanced voices work, but the non-English ones depend on `espeak-ng`'s phonemization
  quality for that language, which varies — English, Spanish, French, and Italian are the
  strongest; Japanese and Mandarin are the least refined.
- The Windows built-in fallback voice's quality and selection depend entirely on what's
  installed on your machine — it's there so the app always works, not to match the enhanced
  voice's quality.
- This is a first public release. If something breaks on your setup, please open an issue —
  it's actively maintained by someone who relies on it personally, every day.

## Building from source

Requires Python 3.12+, and the [WiX Toolset v5+](https://wixtoolset.org/) if you want to
build the installer (not required just to run it from source).

```
git clone <this repo>
cd <the folder you just cloned into>
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

Download the voice model (not included in this repo — about 353MB) from the
[kokoro-onnx releases page](https://github.com/thewh1teagle/kokoro-onnx/releases): get
`kokoro-v1.0.onnx` and `voices-v1.0.bin` and place both in `models\`.

Run it directly:

```
venv\Scripts\python speak_selected.py
```

Or build the standalone installer:

```
venv\Scripts\python -m PyInstaller --name SpeakSelected --onedir --windowed --icon app.ico ^
  --collect-all kokoro_onnx --collect-all onnxruntime --collect-all phonemizer ^
  --collect-all espeakng_loader --collect-all sounddevice --collect-all pystray ^
  --hidden-import win32timezone speak_selected.py

:: copy models\*.onnx, models\*.bin, and app.ico into dist\SpeakSelected\ first

wix build Installer\SpeakSelected.wxs -d PayloadDir="dist\SpeakSelected" -out Installer\SpeakSelected.msi
```

## Feedback and issues

Bug reports, questions, and suggestions are genuinely welcome — open an
[issue](../../issues). This is maintained by someone who uses it every day himself, not a
company with a support queue, so please be patient, but it is actively looked after.

## Credits

- [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) (Apache-2.0) — the enhanced voice model
- [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) (MIT) — the inference engine
- [espeak-ng](https://github.com/espeak-ng/espeak-ng) and
  [phonemizer](https://github.com/bootphon/phonemizer) (both GPL-3.0) — turn text into the
  phonemes Kokoro speaks; this is why this project is licensed GPL-3.0 rather than something
  more permissive
- Windows' own built-in **SAPI5** text-to-speech, the always-available fallback voice — ships
  with every Windows install, accessed via [pywin32](https://github.com/mhammond/pywin32)
  (PSF/Python license), which is also what makes clipboard access and global keyboard shortcuts
  work throughout the rest of the app
- Apple's **Speak Selection**, for being the feature this was always meant to replace

## License

GPL-3.0 — see [LICENSE](LICENSE). Free to use, study, modify, and share.
