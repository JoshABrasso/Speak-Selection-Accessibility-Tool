"""
Speak Selected — a Windows equivalent of macOS's "Speak Selection."

Select text in any app, press Ctrl+Alt+Z, hear it spoken. Works by simulating a
copy (Ctrl+C), reading the clipboard, speaking it through a local Kokoro TTS
voice, then restoring whatever was in the clipboard before. Nothing leaves the
machine — no cloud TTS, no screen reader dependency.

Every control (pause/resume, stop, speed, voice) is a global keyboard shortcut
with a spoken confirmation — the floating panel is a secondary, glanceable
layer, never required to actually use the tool.
"""

import ctypes
import ctypes.wintypes
import faulthandler
import re
import socket
import sys
import threading
import time
import traceback

import numpy as np
import sounddevice as sd
import win32clipboard
import win32con
import win32api

from kokoro_onnx import Kokoro
from kokoro_onnx.chunker import pause_after as _kokoro_pause_after

import app_paths
import autostart
import settings as settings_module
import tts_sapi
from tray import TrayIcon
from ui import UIController

# A --windowed/no-console build has no real stdout/stderr (both are None),
# so reconfigure() would crash before main() even runs, and every later
# print() would too. Route output to a log file instead whenever there's no
# real console to write to — this is the only way a windowed build's
# failures are diagnosable at all, since there's no terminal to see them in.
LOG_PATH = app_paths.data_dir() / "speak_selected.log"

if sys.stdout is None or sys.stderr is None:
    _log_file = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = _log_file
    sys.stderr = _log_file
else:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _log_unhandled_exception(exc_type, exc_value, exc_tb) -> None:
    traceback.print_exception(exc_type, exc_value, exc_tb, file=sys.stderr)


sys.excepthook = _log_unhandled_exception
threading.excepthook = lambda args: _log_unhandled_exception(args.exc_type, args.exc_value, args.exc_traceback)

# sys.excepthook only ever sees genuine Python exceptions — a real native
# fault (a hard crash inside Tcl/Tk, onnxruntime, pywin32/COM, or any other
# C extension) bypasses it completely, taking the whole process down with
# zero trace: no Python exception, and no Windows Error Reporting record
# either, by deliberate privacy choice on this machine. faulthandler is a
# distinct, lower-level CPython mechanism that installs its own fault
# handler and writes a real stack trace to this file the instant such a
# crash happens — confirmed working via a real deliberate test crash
# before trusting it, not assumed: it's the one piece of evidence that
# doesn't depend on WER or on the crash being a well-behaved exception.
_crash_log_path = app_paths.data_dir() / "crash_trace.log"
_crash_log_file = open(_crash_log_path, "a", encoding="utf-8", buffering=1)
faulthandler.enable(file=_crash_log_file, all_threads=True)

MODEL_DIR = app_paths.app_dir() / "models"

# A fixed localhost port, used purely as a single-instance signal — not a
# network service. Launching the exe again (e.g. from the Start Menu or
# taskbar while it's already running in the tray) should bring the existing
# Settings window to front, not silently fail to register already-claimed
# hotkeys or start a confusing second copy of the app.
SINGLE_INSTANCE_PORT = 51762


_single_instance_socket: socket.socket | None = None


def claim_single_instance_or_signal_running() -> bool:
    """True if another instance is already running and was just told to
    show its window — the caller should exit immediately in that case.

    This has to be one atomic step, not "check if anything's listening,
    then bind our own listener later" — the earlier version did exactly
    that, with the bind happening only after model loading (several
    seconds later). Two launches close together could both see nothing
    listening yet and both conclude "I'm the first instance," producing
    two full running copies that then fought over the same hotkeys — this
    is confirmed to have actually happened, not a theoretical risk: caught
    two live python.exe processes started at the same timestamp, and
    Ctrl+Alt+Z silently belonged to whichever one won that race. Binding
    the port *is* the atomic claim — only one process can ever hold it."""
    global _single_instance_socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    except OSError:
        sock.close()
        try:
            client = socket.create_connection(("127.0.0.1", SINGLE_INSTANCE_PORT), timeout=0.5)
            client.sendall(b"show")
            client.close()
        except OSError:
            pass  # the other instance is mid-shutdown or wedged — nothing more to do here
        return True

    sock.listen(5)
    _single_instance_socket = sock  # kept alive for the process lifetime, not GC'd
    return False


def _start_single_instance_listener() -> None:
    def _serve() -> None:
        while True:
            try:
                conn, _ = _single_instance_socket.accept()
                conn.close()
                ui.open_settings()
            except OSError:
                # The listening socket itself is gone (process shutting
                # down) — nothing left to serve.
                return
            except Exception as exc:
                # One bad connection attempt used to take the whole loop
                # down silently — accept() stays armed for every connection
                # after it, not just the first.
                print(f"Single-instance listener: error handling a connection: {exc}")

    threading.Thread(target=_serve, daemon=True).start()


# Each action gets a fixed numeric id (RegisterHotKey's "which hotkey slot"
# parameter) that never changes — the actual key combo bound to it does,
# and now lives in settings (settings_module.DEFAULT_HOTKEYS), not here.
ACTION_IDS = {action: i + 1 for i, action in enumerate(settings_module.ACTIONS)}
ID_TO_ACTION = {v: k for k, v in ACTION_IDS.items()}

user32 = ctypes.windll.user32


def register_all_hotkeys() -> list[str]:
    """Registers every action's current shortcut. Returns the list of
    actions whose shortcut couldn't be claimed (already owned by another
    app) — callers decide what to do about that, this never raises, since
    one conflicting shortcut on a stranger's machine (unknowable in advance)
    shouldn't be able to stop the whole app from starting."""
    failed = []
    for action in settings_module.ACTIONS:
        mods, vk = current_settings["hotkeys"][action]
        if not user32.RegisterHotKey(None, ACTION_IDS[action], mods, vk):
            failed.append(action)
    return failed


# Confirmed via Microsoft's own RegisterHotKey documentation: a hotkey
# registered with hWnd=NULL is tied to the *calling thread's own* message
# queue, not the process as a whole. try_rebind_hotkey() is invoked from
# the Tk UI thread (Settings' "Change" button), but the actual WM_HOTKEY
# dispatch loop runs on the main thread — calling UnregisterHotKey/
# RegisterHotKey directly from the Tk thread would register the new combo
# to a message queue nothing ever reads, leaving it permanently dead while
# the old combo silently kept working, until the app was restarted. This
# custom message marshals the real Win32 calls onto the main thread
# (see _handle_rebind_hotkey_request in the message loop below), and the
# Tk thread blocks briefly on the response — safe, since it's the Tk
# thread blocking, not the message loop itself.
WM_REBIND_HOTKEY = 0x8000 + 1  # WM_APP + 1, a private application message
_rebind_lock = threading.Lock()
_rebind_request: dict = {}
_rebind_response_event = threading.Event()


def try_rebind_hotkey(action: str, mods: int, vk: int) -> bool:
    """Called from the Tk UI thread. Returns whether the new shortcut was
    actually applied, after the main thread has handled the real
    registration — see _handle_rebind_hotkey_request for the rollback-on-
    conflict behaviour."""
    with _rebind_lock:
        _rebind_response_event.clear()
        _rebind_request.clear()
        _rebind_request.update(action=action, mods=mods, vk=vk)
    if not user32.PostThreadMessageW(main_thread_id, WM_REBIND_HOTKEY, 0, 0):
        return False  # main thread's queue is gone (shutting down) — nothing to do
    # A 2s timeout so a stuck main thread can never hang the Settings UI
    # forever — this should always resolve almost instantly in practice.
    _rebind_response_event.wait(timeout=2.0)
    with _rebind_lock:
        return bool(_rebind_request.get("result"))


def _handle_rebind_hotkey_request() -> None:
    """Runs on the main thread in response to WM_REBIND_HOTKEY — the real
    UnregisterHotKey/RegisterHotKey calls now correctly happen on the same
    thread that owns every hotkey registration, and rolls back to the old
    combo if the new one turns out to already be taken by something else."""
    with _rebind_lock:
        action = _rebind_request["action"]
        mods = _rebind_request["mods"]
        vk = _rebind_request["vk"]
    action_id = ACTION_IDS[action]
    old_mods, old_vk = current_settings["hotkeys"][action]
    user32.UnregisterHotKey(None, action_id)
    applied = False
    if user32.RegisterHotKey(None, action_id, mods, vk):
        current_settings["hotkeys"][action] = [mods, vk]
        settings_module.save(current_settings)
        applied = True
    else:
        user32.RegisterHotKey(None, action_id, old_mods, old_vk)  # restore — never leave it unbound
    with _rebind_lock:
        _rebind_request["result"] = applied
    _rebind_response_event.set()


SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

current_settings = settings_module.load()
paused = threading.Event()
confirmation_speaking = threading.Event()
playback_active = threading.Event()

# Bumped on every new hotkey press that starts a new speech; the playback
# thread checks this and aborts as soon as it's superseded by a newer request.
current_generation = 0
generation_lock = threading.Lock()

# The current content and where we are in it — kept around (not thrown away
# after playing) so Repeat/Back/Forward can jump within the same selection
# without needing a fresh clipboard capture.
playback_lock = threading.Lock()
playback_state = {"sentences": [], "index": 0}

kokoro: Kokoro | None
ui: UIController
main_thread_id: int


def active_engine() -> str:
    """Resolves the user's engine preference to the engine actually used
    for the next synthesis call. "auto" (the default) prefers Kokoro
    whenever it loaded successfully, falling back to Windows' always-
    available built-in voice otherwise. A forced choice of "kokoro" still
    falls back if Kokoro genuinely isn't available — forcing a choice
    should never produce silence, just whatever's actually possible."""
    choice = current_settings.get("tts_engine", "auto")
    if choice == "sapi":
        return "sapi"
    return "kokoro" if kokoro is not None else "sapi"


def get_clipboard_text() -> str:
    # A mixed selection (text + image/icon/svg, common on web pages) puts
    # several clipboard formats in play at once — CF_HTML, CF_BITMAP/CF_DIB,
    # sometimes CF_UNICODETEXT too, sometimes not, depending on the browser
    # and exactly what was selected. Any failure to read plain text just
    # means "no usable text in this selection", not a crash.
    try:
        win32clipboard.OpenClipboard()
    except Exception:
        return ""
    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return ""
        return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    except Exception as exc:
        print(f"  (clipboard read note: {exc})")
        return ""
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard_text(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        if text:
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def wait_for_hotkey_release(vks: tuple[int, ...], timeout: float = 1.0) -> None:
    """Wait until the given keys are all physically released before we
    synthesize our own Ctrl+C — otherwise leftover held keys from the
    hotkey press itself corrupt the synthetic keystroke."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(win32api.GetAsyncKeyState(vk) & 0x8000 for vk in vks):
            return
        time.sleep(0.01)


def simulate_ctrl_c() -> None:
    VK_CONTROL = 0x11
    VK_C = 0x43  # the literal 'C' key, for the clipboard-copy keystroke itself —
                 # fixed, unrelated to whatever key "speak" is bound to
    win32api.keybd_event(VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(VK_C, 0, 0, 0)
    time.sleep(0.03)
    win32api.keybd_event(VK_C, 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)


def _keys_held_for_hotkey(mods: int, vk: int) -> tuple[int, ...]:
    """The individual VK codes making up a (mods, vk) combo, for polling
    whether they're all physically released."""
    VK_CONTROL, VK_MENU, VK_SHIFT = 0x11, 0x12, 0x10
    keys = [vk]
    if mods & settings_module.MOD_CONTROL:
        keys.append(VK_CONTROL)
    if mods & settings_module.MOD_ALT:
        keys.append(VK_MENU)
    if mods & settings_module.MOD_SHIFT:
        keys.append(VK_SHIFT)
    return tuple(keys)


def capture_selected_text(timeout: float = 0.3) -> str:
    previous = get_clipboard_text()

    # Wait for whatever "speak" is actually bound to right now (it's
    # rebindable, not always Ctrl+Alt+Z) to be physically released before
    # synthesizing our own Ctrl+C — otherwise leftover held keys from the
    # hotkey press itself corrupt the synthetic keystroke.
    wait_for_hotkey_release(_keys_held_for_hotkey(*current_settings["hotkeys"]["speak"]))

    # Clear the clipboard first so we can detect "nothing was copied"
    # (e.g. no selection) instead of re-reading stale old content.
    set_clipboard_text("")
    simulate_ctrl_c()

    deadline = time.time() + timeout
    captured = ""
    while time.time() < deadline:
        captured = get_clipboard_text()
        if captured:
            break
        time.sleep(0.01)

    set_clipboard_text(previous)
    return captured


CLAUSE_SPLIT_RE = re.compile(r"(?<=[,;:])\s+")


def _split_long_chunk(text: str, max_len: int) -> list[str]:
    """Break one over-long chunk into pieces of at most `max_len` chars,
    least-disruptive boundary first — same preference order as kokoro's own
    internal chunker: newline (keeps list items/lines intact), then clause
    punctuation (a comma/semicolon/colon split still sounds like a natural
    pause), then word boundaries so a piece never cuts a word in half."""
    if len(text) <= max_len:
        return [text]

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        pieces = []
        for line in lines:
            pieces.extend(_split_long_chunk(line, max_len))
        return pieces

    clauses = [c.strip() for c in CLAUSE_SPLIT_RE.split(text) if c.strip()]
    if len(clauses) > 1:
        pieces = []
        for clause in clauses:
            pieces.extend(_split_long_chunk(clause, max_len))
        return pieces

    words = text.split()
    pieces, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_len and current:
            pieces.append(current)
            current = word
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def split_sentences(text: str) -> list[str]:
    """Plain per-sentence split — this is what actually gets spoken, and it
    already sounds natural (each sentence keeps its own punctuation, its
    own pacing), so it's left alone. See _bound_first_chunk below for the
    one real problem this had: only the very first chunk's synthesis time
    is ever perceptible as a delay before speech starts at all — every
    sentence after it synthesizes in the background while something
    earlier is already playing, so it was never worth touching."""
    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if s.strip()]
    return sentences or [text.strip()]


# Measured directly: a first sentence that's either very long (a run-on
# block with no punctuation for a long stretch — a bullet list, say) or
# very short (a couple of words) both made kokoro noticeably slow to
# produce that first bit of audio — the long case from sheer synthesis
# time, the short case from an artifact in how kokoro picks its voice style
# by utterance length. Bound only this one position; everything else in
# the sentence list is untouched.
MAX_FIRST_CHUNK_CHARS = 200
MIN_FIRST_CHUNK_CHARS = 50


def _bound_first_chunk(sentences: list[str]) -> list[str]:
    if not sentences:
        return sentences
    first, rest = sentences[0], sentences[1:]

    if len(first) > MAX_FIRST_CHUNK_CHARS:
        return _split_long_chunk(first, MAX_FIRST_CHUNK_CHARS) + rest

    if len(first) < MIN_FIRST_CHUNK_CHARS and rest:
        merged = f"{first} {rest[0]}"
        if len(merged) > MAX_FIRST_CHUNK_CHARS:
            return _split_long_chunk(merged, MAX_FIRST_CHUNK_CHARS) + rest[1:]
        return [merged] + rest[1:]

    return sentences


def _synthesize(text: str, lang: str | None = None) -> tuple:
    """`lang` overrides the voice's own language — used only for spoken
    confirmations ("Voice: Isabella"), which are always in English
    regardless of which voice's timbre is reading them out."""
    if active_engine() == "kokoro":
        return kokoro.create(
            text, voice=current_settings["voice"], speed=current_settings["speed"],
            lang=lang or settings_module.lang_for_voice(current_settings["voice"]),
        )
    return tts_sapi.synthesize(text, current_settings.get("sapi_voice"), current_settings["speed"])


def playback_worker(generation: int) -> None:
    """Plays `playback_state["sentences"]` starting from `playback_state["index"]`.

    While one sentence plays, the next is synthesized concurrently in the
    background — so there's no audible synthesis gap between sentences —
    but that lookahead is always validated against the live index before
    it's used. A speed/voice change restarts playback from the current
    index via `start_playback_from` (a new generation), which makes this
    worker's own stale lookahead get discarded rather than played; a
    Back/Forward/Repeat jump that doesn't change generation is caught by
    the index-match check below instead. Either way, a jump always takes
    effect on the very next thing actually played — never stale audio."""
    playback_active.set()
    lookahead: dict = {}  # {"index": int, "audio": (samples, sample_rate)}
    try:
        while True:
            with generation_lock:
                if generation != current_generation:
                    return
            with playback_lock:
                sentences = playback_state["sentences"]
                index = playback_state["index"]
            if index >= len(sentences):
                return

            if lookahead.get("index") == index:
                samples, sample_rate = lookahead["audio"]
            else:
                try:
                    samples, sample_rate = _synthesize(sentences[index])
                except Exception as exc:
                    print(f"Error during speech synthesis: {exc}")
                    return
                with generation_lock:
                    if generation != current_generation:
                        return
                with playback_lock:
                    if playback_state["index"] != index:
                        continue  # a jump landed while we were synthesizing

            while paused.is_set() or confirmation_speaking.is_set():
                with generation_lock:
                    if generation != current_generation:
                        return
                time.sleep(0.05)

            with generation_lock:
                if generation != current_generation:
                    return

            # Synthesize the next sentence in the background while this one
            # plays, so it's ready the instant playback here finishes.
            next_index = index + 1
            lookahead = {}
            prefetch_box: dict = {}
            prefetch_thread = None
            if next_index < len(sentences):
                def _prefetch(target_index=next_index):
                    try:
                        prefetch_box["audio"] = _synthesize(sentences[target_index])
                    except Exception as exc:
                        print(f"Error during speech synthesis: {exc}")
                prefetch_thread = threading.Thread(target=_prefetch, daemon=True)
                prefetch_thread.start()

            sd.play(np.asarray(samples), sample_rate)
            sd.wait()

            with generation_lock:
                if generation != current_generation:
                    return

            # A chunk _bound_first_chunk force-split off a too-long first
            # sentence (not a real sentence break) got no pause baked into
            # its own audio the way a genuine sentence end would from
            # kokoro's own batching — so it would otherwise play back-to
            # -back with zero gap, sounding rushed or run-together. Add
            # back only the clause-length pause kokoro itself would have
            # inserted there; a true sentence end is left as-is (existing,
            # already-fine behavior), and a raw word-boundary cut gets no
            # added pause either, since that's mid-thought and continuity
            # matters more than a pause there.
            if next_index < len(sentences):
                gap = _kokoro_pause_after(sentences[index], sentence=0.0, clause=0.1)
                if gap:
                    time.sleep(gap)

            if prefetch_thread is not None:
                prefetch_thread.join()
                if "audio" in prefetch_box:
                    lookahead = {"index": next_index, "audio": prefetch_box["audio"]}

            with playback_lock:
                if playback_state["index"] == index:
                    playback_state["index"] = index + 1
                elif lookahead.get("index") != playback_state["index"]:
                    lookahead = {}  # a jump landed — the prefetch doesn't match where we're going
    finally:
        playback_active.clear()
        with generation_lock:
            if generation == current_generation:
                ui.hide_osd()


def start_playback_from(index: int) -> None:
    global current_generation
    with generation_lock:
        current_generation += 1
        generation = current_generation
    with playback_lock:
        playback_state["index"] = index
    paused.clear()
    ui.set_paused(False)
    sd.stop()
    threading.Thread(target=playback_worker, args=(generation,), daemon=True).start()


def jump_to(index: int) -> bool:
    """Move playback to `index` within the current content. Redirects an
    already-running playback thread, or starts a fresh one if speech had
    already finished/stopped. Returns False if there's no content to jump within."""
    with playback_lock:
        total = len(playback_state["sentences"])
    if total == 0:
        return False
    index = max(0, min(index, total - 1))
    if playback_active.is_set():
        with playback_lock:
            playback_state["index"] = index
        sd.stop()
    else:
        start_playback_from(index)
    return True


def should_speak_confirmation(source: str) -> bool:
    mode = current_settings.get("confirmation_mode", "keyboard_only")
    if mode == "off":
        return False
    if mode == "always":
        return True
    return source == "keyboard"


def speak_confirmation(text: str, source: str = "keyboard") -> None:
    if not should_speak_confirmation(source):
        return

    def _run():
        # Claim the audio device immediately so this never overlaps with
        # whatever selection-reading is in progress — stop it, speak the
        # confirmation, then let play_worker pick back up where it left off.
        sd.stop()
        confirmation_speaking.set()
        try:
            samples, sample_rate = _synthesize(text, lang="en-gb")
            sd.play(np.asarray(samples), sample_rate)
            sd.wait()
        finally:
            confirmation_speaking.clear()

    threading.Thread(target=_run, daemon=True).start()


def handle_speak() -> None:
    t0 = time.perf_counter()
    text = capture_selected_text()
    t1 = time.perf_counter()

    if not text.strip():
        print(f"No text selected. (capture: {t1 - t0:.2f}s)")
        return

    sentences = _bound_first_chunk(split_sentences(text))
    print(f"Speaking ({len(sentences)} sentence(s), capture: {t1 - t0:.2f}s): "
          f"{text[:80]}{'...' if len(text) > 80 else ''}")

    with playback_lock:
        playback_state["sentences"] = sentences

    voice_label = settings_module.VOICE_LABELS.get(current_settings["voice"], current_settings["voice"])
    ui.show_osd(f"\U0001F50A Speaking  ·  {voice_label}  ·  {current_settings['speed']:.1f}x")

    start_playback_from(0)


def handle_repeat() -> None:
    if not jump_to(0):
        _flash_osd("Nothing to repeat yet")
        return
    _flash_osd("\U0001F501 Repeat")


def handle_back() -> None:
    with playback_lock:
        index = playback_state["index"]
    if not jump_to(index - 1):
        _flash_osd("Nothing to go back to")
        return
    _flash_osd("⏮ Previous sentence")


def handle_forward() -> None:
    with playback_lock:
        index = playback_state["index"]
    if not jump_to(index + 1):
        _flash_osd("Nothing to skip to")
        return
    _flash_osd("⏭ Next sentence")


def _flash_osd(text: str) -> None:
    ui.show_osd(text)
    ui.hide_osd()


def handle_pause_resume() -> None:
    # Pause/stop/speed are self-evident from the audio itself (silence, or
    # the new pace) — only a voice change gets a spoken confirmation, since
    # hearing the voice say something is the only real way to confirm it.
    if paused.is_set():
        paused.clear()
        ui.set_paused(False)
        _flash_osd("▶ Resumed")
    else:
        paused.set()
        sd.stop()
        ui.set_paused(True)
        _flash_osd("⏸ Paused")


def handle_stop() -> None:
    global current_generation
    with generation_lock:
        current_generation += 1
    sd.stop()
    paused.clear()
    ui.set_paused(False)
    _flash_osd("⏹ Stopped")


def _restart_current_sentence_if_active() -> None:
    """A speed/voice change should be heard right away, not just once the
    sentence already in flight happens to finish — restart that same
    sentence (same index, not advancing) under the new settings."""
    if playback_active.is_set():
        with playback_lock:
            index = playback_state["index"]
        start_playback_from(index)


def handle_voice_cycle(source: str = "keyboard") -> None:
    # Cycles whichever engine is actually active — Kokoro's fixed bundled
    # voices, or whatever SAPI voices are actually installed on this
    # machine, since the two have entirely different voice lists.
    if active_engine() == "kokoro":
        voices = settings_module.VOICES
        idx = voices.index(current_settings["voice"]) if current_settings["voice"] in voices else 0
        current_settings["voice"] = voices[(idx + 1) % len(voices)]
        settings_module.save(current_settings)
        label = settings_module.VOICE_LABELS[current_settings["voice"]]
    else:
        voices = tts_sapi.list_voices()
        if not voices:
            _flash_osd("No Windows voices found")
            return
        ids = [v[0] for v in voices]
        current_id = current_settings.get("sapi_voice")
        idx = ids.index(current_id) if current_id in ids else -1
        new_id, label = voices[(idx + 1) % len(voices)]
        current_settings["sapi_voice"] = new_id
        settings_module.save(current_settings)
    _flash_osd(f"Voice: {label}")
    _restart_current_sentence_if_active()
    speak_confirmation(f"Voice: {label}", source)


def handle_speed(delta: float) -> None:
    new_speed = round(current_settings["speed"] + delta, 1)
    new_speed = max(settings_module.SPEED_MIN, min(settings_module.SPEED_MAX, new_speed))
    current_settings["speed"] = new_speed
    settings_module.save(current_settings)
    _flash_osd(f"Speed: {new_speed:.1f}x")
    _restart_current_sentence_if_active()


def handle_settings_ui_voice_change(voice_id: str) -> None:
    current_settings["voice"] = voice_id
    settings_module.save(current_settings)
    label = settings_module.VOICE_LABELS[voice_id]
    _restart_current_sentence_if_active()
    speak_confirmation(f"Voice: {label}", source="ui")


def handle_settings_ui_sapi_voice_change(voice_id: str, label: str) -> None:
    current_settings["sapi_voice"] = voice_id
    settings_module.save(current_settings)
    _restart_current_sentence_if_active()
    speak_confirmation(f"Voice: {label}", source="ui")


def handle_tts_engine_change(engine: str) -> None:
    current_settings["tts_engine"] = engine
    settings_module.save(current_settings)
    _restart_current_sentence_if_active()
    label = settings_module.TTS_ENGINE_LABELS.get(engine, engine)
    speak_confirmation(f"Voice engine: {label}", source="ui")


def handle_confirmation_mode_change(mode: str) -> None:
    current_settings["confirmation_mode"] = mode
    settings_module.save(current_settings)


def handle_panel_behavior_change(behavior: str) -> None:
    current_settings["panel_behavior"] = behavior
    settings_module.save(current_settings)


def handle_settings_ui_speed_change(speed: float, restart: bool = False) -> None:
    current_settings["speed"] = speed
    settings_module.save(current_settings)
    if restart:
        _restart_current_sentence_if_active()


def handle_panel_position_change(x: int, y: int) -> None:
    current_settings["panel_x"] = x
    current_settings["panel_y"] = y
    settings_module.save(current_settings)


def get_current_state() -> dict:
    state = dict(current_settings)
    # Which engine "auto" actually resolves to right now, and whether
    # Kokoro is even an option — the Settings UI needs both to show the
    # right controls and to explain what "Best available" currently means.
    state["kokoro_available"] = kokoro is not None
    state["active_engine"] = active_engine()
    return state


ACTION_HANDLERS = {
    "speak": handle_speak,
    "pause": handle_pause_resume,
    "stop": handle_stop,
    "voice": handle_voice_cycle,
    "settings": lambda: ui.open_settings(),
    "speed_up": lambda: handle_speed(settings_module.SPEED_STEP),
    "speed_down": lambda: handle_speed(-settings_module.SPEED_STEP),
    "repeat": handle_repeat,
    "back": handle_back,
    "forward": handle_forward,
}


def main() -> None:
    global kokoro, ui, main_thread_id

    # Without this, Windows has no distinct identity for the process and
    # falls back to grouping/labelling it as whatever the Python interpreter
    # itself is — confirmed from a real screenshot showing a generic Python
    # icon and the literal text "tk" (Tkinter's own hardcoded default root
    # window title) in the taskbar. This must be set before any window is
    # created, and is the documented, standard fix for exactly this.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SpeakSelected.App")
    except Exception:
        pass  # cosmetic only — never block startup over a taskbar identity call

    # The thread that runs the Win32 message loop below — needed because
    # exit requests come from other threads (the tray icon's own thread,
    # the Tk UI thread), and PostQuitMessage only posts WM_QUIT to the
    # *calling* thread's own queue, not this one. Calling it from those
    # other threads would silently do nothing: the message loop would keep
    # blocking forever, and the process would linger as an invisible
    # orphan after every visible window and the tray icon were gone —
    # exactly the kind of silent failure this app can't ship with.
    main_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

    if claim_single_instance_or_signal_running():
        print("Already running — brought the existing Settings window to front instead.")
        return

    # Kokoro (the enhanced voice) is preferred but never required — Windows'
    # own built-in SAPI5 voice ships on every Windows install and is the
    # real fallback here, so a missing or corrupted model file degrades to
    # a lower-quality voice instead of refusing to start at all. For an
    # accessibility tool someone depends on daily, "still works, just not
    # the best voice" is categorically better than "doesn't work."
    model_path = MODEL_DIR / "kokoro-v1.0.onnx"
    voices_path = MODEL_DIR / "voices-v1.0.bin"
    if model_path.exists() and voices_path.exists():
        print("Loading the enhanced voice model...")
        try:
            kokoro = Kokoro(str(model_path), str(voices_path))
        except Exception as exc:
            print(f"Enhanced voice model failed to load ({exc}); using the Windows "
                  f"built-in voice instead.")
            traceback.print_exc()
            kokoro = None
    else:
        print(f"Enhanced voice model not found in {MODEL_DIR}; using the Windows "
              f"built-in voice instead.")
        kokoro = None

    if kokoro is None and not tts_sapi.is_available():
        raise RuntimeError(
            "Neither the enhanced voice nor Windows' own built-in text-to-speech "
            "could be loaded. Try reinstalling Speak Selected, or check that Windows "
            "Speech is enabled under Settings → Time & Language → Speech."
        )

    WM_QUIT = 0x0012
    exit_app = lambda: user32.PostThreadMessageW(main_thread_id, WM_QUIT, 0, 0)

    ui = UIController(
        on_voice_change=handle_settings_ui_voice_change,
        on_speed_change=handle_settings_ui_speed_change,
        get_state=get_current_state,
        on_pause_resume=handle_pause_resume,
        on_stop=handle_stop,
        on_voice_cycle=lambda: handle_voice_cycle(source="ui"),
        on_speed_step=lambda direction: handle_speed(direction * settings_module.SPEED_STEP),
        on_position_change=handle_panel_position_change,
        on_confirmation_mode_change=handle_confirmation_mode_change,
        on_repeat=handle_repeat,
        on_back=handle_back,
        on_forward=handle_forward,
        on_panel_behavior_change=handle_panel_behavior_change,
        on_rebind_hotkey=try_rebind_hotkey,
        on_exit=exit_app,
        on_tts_engine_change=handle_tts_engine_change,
        on_sapi_voice_change=handle_settings_ui_sapi_voice_change,
    )

    _start_single_instance_listener()

    # A conflicting shortcut shouldn't take down the whole app — that would
    # make it impossible to ever reach Settings to fix it. Register
    # whatever does succeed, note whatever doesn't, and surface that
    # afterwards instead of failing the entire startup over one shortcut.
    failed_actions = register_all_hotkeys()

    TrayIcon(
        on_open_settings=ui.open_settings,
        on_show_help=ui.show_onboarding,
        on_exit=exit_app,
    )

    if not current_settings.get("onboarding_shown"):
        ui.show_onboarding()
        current_settings["onboarding_shown"] = True
        settings_module.save(current_settings)
        # Default to on for a fresh install, same as before — but set by the
        # app itself, once, rather than by the installer (see the WXS for
        # why: a user's later "turn this off" in Settings needs to actually
        # stick, including across a version upgrade).
        autostart.set_enabled(True)
    elif failed_actions:
        names = ", ".join(settings_module.ACTION_LABELS[a] for a in failed_actions)
        ui.show_osd(f"⚠ Some shortcuts are already used by another app: {names}. "
                    f"Open Settings to change them.")

    print("Ready — shortcuts are set in Settings and can be changed any time.")

    try:
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageA(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == win32con.WM_HOTKEY:
                action = ID_TO_ACTION.get(msg.wParam)
                handler = ACTION_HANDLERS.get(action)
                if handler is None:
                    continue
                try:
                    handler()
                except Exception as exc:
                    # A silently-swallowed exception here is exactly what
                    # made the app look like it had "stopped invoking"
                    # during development (a stale constant reference, caught
                    # here, visible only in a log file nobody was looking
                    # at) — logging alone isn't enough for a release build;
                    # the user needs to actually see that their key press
                    # did something, even when that something was a bug.
                    print(f"Error handling hotkey press ({action}): {exc}")
                    traceback.print_exc()
                    ui.show_osd(f"⚠ {settings_module.ACTION_LABELS.get(action, action)} hit an "
                                f"error and didn't complete. Check the log if this keeps happening.")
            elif msg.message == WM_REBIND_HOTKEY:
                try:
                    _handle_rebind_hotkey_request()
                except Exception as exc:
                    print(f"Error handling hotkey rebind request: {exc}")
                    traceback.print_exc()
                    # The Tk thread is blocked waiting on this — it must
                    # always be released, even when the rebind itself blew
                    # up, or Settings would hang for up to the 2s timeout
                    # instead of failing cleanly.
                    with _rebind_lock:
                        _rebind_request["result"] = False
                    _rebind_response_event.set()
    finally:
        for action_id in ACTION_IDS.values():
            user32.UnregisterHotKey(None, action_id)


def _show_startup_error(message: str) -> None:
    """A real, visible Windows dialog — not just a log entry. This is a
    --windowed build with no console: if startup fails silently, someone
    who isn't us has no way to know the app didn't start at all, let alone
    why. MessageBoxW needs nothing else initialized, so it works even if
    the model failed to load before Tk ever started."""
    MB_OK = 0x0
    MB_ICONERROR = 0x10
    ctypes.windll.user32.MessageBoxW(None, message, "Speak Selected — couldn't start", MB_OK | MB_ICONERROR)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Fatal startup error: {exc}")
        traceback.print_exc()
        _show_startup_error(
            f"{exc}\n\nDetails were saved to:\n{LOG_PATH}"
        )
