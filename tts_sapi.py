"""Windows' own built-in SAPI5 text-to-speech — the always-available
fallback/default voice.

Ships on every Windows install already (no download, no setup), so this
is what the app falls back to whenever Kokoro (the enhanced voice) isn't
available — not yet downloaded, corrupted, or a from-source build that
simply doesn't bundle it. This engine can never be "missing": that's the
whole point of having it. Quality is noticeably more robotic than Kokoro,
but it always works, which matters more for an accessibility tool than
voice quality does.
"""

import numpy as np
import pythoncom
import win32com.client

# From Microsoft's own SpeechAudioFormatType enum reference (SAPI 5.3/5.4) —
# confirmed against the published enum definition, not guessed: picking the
# wrong numeric value here produces garbled or wrong-pitch audio, not a
# clean failure.
SAFT24kHz16BitMono = 26
SAMPLE_RATE = 24000

SVSFDefault = 0


def list_voices() -> list[tuple[str, str]]:
    """Every SAPI voice actually installed on this machine, as
    (id, display_name) pairs — varies by Windows install/language packs,
    unlike Kokoro's fixed bundled set, so this is read live, not assumed."""
    pythoncom.CoInitialize()
    try:
        sp_voice = win32com.client.Dispatch("SAPI.SpVoice")
        voices = [(token.Id, token.GetDescription()) for token in sp_voice.GetVoices()]
        sp_voice = None  # release before CoUninitialize — see synthesize()
        return voices
    finally:
        pythoncom.CoUninitialize()


def _speed_to_sapi_rate(speed: float) -> int:
    """Maps the app's 0.5x-2.5x speed multiplier onto SAPI's own -10..10
    integer rate scale (1.0x -> 0, no change). SAPI's scale isn't a true
    linear multiplier internally, so this is a reasonable approximation,
    not an exact match to Kokoro's speed behaviour — SAPI exposes nothing
    closer to a real multiplier."""
    return max(-10, min(10, round((speed - 1.0) * 10)))


def synthesize(text: str, voice_id: str | None, speed: float) -> tuple[np.ndarray, int]:
    """Renders to an in-memory stream rather than SAPI's own speaker
    output, so playback stays on the app's existing sounddevice pipeline —
    the same pause/resume/stop/speed controls work identically regardless
    of which engine actually produced the audio."""
    pythoncom.CoInitialize()
    try:
        sp_voice = win32com.client.Dispatch("SAPI.SpVoice")
        if voice_id:
            for token in sp_voice.GetVoices():
                if token.Id == voice_id:
                    sp_voice.Voice = token
                    break
        sp_voice.Rate = _speed_to_sapi_rate(speed)

        stream = win32com.client.Dispatch("SAPI.SpMemoryStream")
        stream.Format.Type = SAFT24kHz16BitMono
        sp_voice.AudioOutputStream = stream
        sp_voice.Speak(text, SVSFDefault)  # synchronous — blocks until done

        raw = bytes(stream.GetData())
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        # Explicitly release the COM objects before CoUninitialize() tears
        # down this thread's apartment — confirmed by testing that leaving
        # them to Python's normal garbage-collection timing (i.e. whenever
        # they happen to go out of scope relative to the finally block
        # below) can fire their Release() calls *after* the apartment is
        # already gone, producing a "Win32 exception... releasing IUnknown"
        # warning. Dropping the reference here collects them immediately.
        sp_voice = None
        stream = None
        return samples, SAMPLE_RATE
    finally:
        pythoncom.CoUninitialize()


def is_available() -> bool:
    """SAPI ships with every Windows install, but confirm rather than
    assume — a locked-down or stripped install is at least conceivable,
    and this is the fallback of last resort, so it needs to fail
    gracefully itself rather than ever raise somewhere unexpected.

    Must wrap with CoInitialize/CoUninitialize exactly like the other
    functions here — confirmed by testing that without it, Dispatch()
    raises in some calling contexts and this silently (and wrongly)
    reported SAPI as unavailable even though synthesis worked fine."""
    pythoncom.CoInitialize()
    try:
        win32com.client.Dispatch("SAPI.SpVoice")  # just confirming this doesn't raise
        return True
    except Exception:
        return False
    finally:
        pythoncom.CoUninitialize()
