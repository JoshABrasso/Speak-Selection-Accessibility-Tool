"""Persisted settings for Speak Selected (voice, speed) — a plain JSON file,
editable by hand if ever needed, with sane defaults if it's missing or corrupt."""

import json

import app_paths

SETTINGS_PATH = app_paths.data_dir() / "settings.json"

# Every voice embedded in voices-v1.0.bin — confirmed from the installed
# package itself (Kokoro.get_voices()), not a hand-picked subset. The first
# letter of each id is Kokoro's own language/accent prefix.
PREFIX_LANGUAGE = {
    "a": ("American English", "en-us"),
    "b": ("British English", "en-gb"),
    "e": ("Spanish", "es"),
    "f": ("French", "fr-fr"),
    "h": ("Hindi", "hi"),
    "i": ("Italian", "it"),
    "j": ("Japanese", "ja"),
    "p": ("Brazilian Portuguese", "pt-br"),
    "z": ("Mandarin Chinese", "cmn"),
}

VOICES = [
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    "ef_dora", "em_alex", "em_santa",
    "ff_siwis",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
    "if_sara", "im_nicola",
    "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo",
    "pf_dora", "pm_alex", "pm_santa",
    "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi",
    "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
]


def _label_for(voice_id: str) -> str:
    language, _ = PREFIX_LANGUAGE.get(voice_id[0], ("Unknown", "en-us"))
    name = voice_id.split("_", 1)[1].capitalize()
    return f"{name} ({language})"


VOICE_LABELS = {v: _label_for(v) for v in VOICES}


def lang_for_voice(voice_id: str) -> str:
    """The espeak/phonemizer language code Kokoro needs for this voice's
    language — passing the wrong one mispronounces non-English voices."""
    _, lang = PREFIX_LANGUAGE.get(voice_id[0] if voice_id else "b", (None, "en-gb"))
    return lang


def language_for_voice(voice_id: str) -> str:
    name, _ = PREFIX_LANGUAGE.get(voice_id[0] if voice_id else "b", ("British English", None))
    return name


# Display order: English variants first (most likely to be used daily), then
# everything else in the order Kokoro itself lists the prefixes.
LANGUAGES = ["American English", "British English", "Spanish", "French", "Hindi",
             "Italian", "Japanese", "Brazilian Portuguese", "Mandarin Chinese"]


def voices_for_language(language: str) -> list[str]:
    return [v for v in VOICES if language_for_voice(v) == language]


def voice_display_name(voice_id: str) -> str:
    """Just the voice's own name, no language suffix — used once a Language
    has already been picked separately, so the tag would be redundant."""
    return voice_id.split("_", 1)[1].capitalize()


DEFAULTS = {
    "voice": "bf_isabella",
    "speed": 1.0,
    "panel_x": None,
    "panel_y": None,
    "confirmation_mode": "keyboard_only",  # "off" | "keyboard_only" | "always"
    "onboarding_shown": False,
    "panel_behavior": "persistent",  # "persistent" | "auto_fade"
    "tts_engine": "auto",  # "auto" | "kokoro" | "sapi" — see TTS_ENGINES below
    "sapi_voice": None,    # a Windows SAPI voice id; None = system default voice
}

# "auto" prefers Kokoro (the bundled enhanced voice) whenever its model
# files are actually present and loaded successfully, and falls back to
# Windows' own built-in SAPI5 voice otherwise — which ships on every
# Windows install, so the app can always speak *something*, never a hard
# failure just because a model file is missing or corrupted. "kokoro" and
# "sapi" force one explicitly regardless of what's available (speak_selected
# .py's active_engine() still falls back if "kokoro" is forced but genuinely
# unavailable — forcing a choice should never produce silence).
TTS_ENGINES = ["auto", "kokoro", "sapi"]
TTS_ENGINE_LABELS = {
    "auto": "Best available (recommended)",
    "kokoro": "Enhanced voice",
    "sapi": "Windows built-in voice",
}

CONFIRMATION_MODES = ["off", "keyboard_only", "always"]
CONFIRMATION_MODE_LABELS = {
    "off": "Off",
    "keyboard_only": "Keyboard shortcuts only",
    "always": "Always (including panel clicks)",
}

PANEL_BEHAVIORS = ["persistent", "auto_fade"]
PANEL_BEHAVIOR_LABELS = {
    "persistent": "Stay open like a normal app",
    "auto_fade": "Fade out automatically when idle",
}

SPEED_MIN = 0.5
SPEED_MAX = 2.5
SPEED_STEP = 0.1

# Win32 MOD_* flags for RegisterHotKey, and the action list for rebinding.
MOD_ALT, MOD_CONTROL, MOD_SHIFT = 0x1, 0x2, 0x4

ACTIONS = ["speak", "pause", "stop", "voice", "settings",
           "speed_up", "speed_down", "repeat", "back", "forward"]
ACTION_LABELS = {
    "speak": "Speak selected text",
    "pause": "Pause / resume",
    "stop": "Stop",
    "voice": "Cycle voice",
    "settings": "Open settings",
    "speed_up": "Speed up",
    "speed_down": "Speed down",
    "repeat": "Repeat from the start",
    "back": "Previous sentence",
    "forward": "Next sentence",
}
# Stock defaults — each already checked live against Cubase/Ableton during
# development (see the research doc), but a stranger's own app stack is
# unknowable in advance, which is exactly why these need to be rebindable.
DEFAULT_HOTKEYS = {
    "speak": (MOD_CONTROL | MOD_ALT, 0x5A),               # Z
    "pause": (MOD_CONTROL | MOD_ALT, 0x58),                # X
    "stop": (MOD_CONTROL | MOD_ALT, 0x43),                 # C
    "voice": (MOD_CONTROL | MOD_ALT, 0x4E),                # N
    "settings": (MOD_CONTROL | MOD_ALT, 0xBC),             # ,
    "speed_up": (MOD_CONTROL | MOD_ALT, 0xDD),             # ]
    "speed_down": (MOD_CONTROL | MOD_ALT, 0xDB),           # [
    "repeat": (MOD_CONTROL | MOD_ALT, 0x24),               # Home
    "back": (MOD_CONTROL | MOD_ALT, 0x21),                 # Page Up
    "forward": (MOD_CONTROL | MOD_ALT, 0x22),              # Page Down
}

_VK_NAMES = {
    0x5A: "Z", 0x58: "X", 0x43: "C", 0x4E: "N", 0xBC: ",", 0xDD: "]", 0xDB: "[",
    0x24: "Home", 0x21: "PageUp", 0x22: "PageDown", 0x08: "Backspace",
    0x09: "Tab", 0x0D: "Enter", 0x1B: "Esc", 0x20: "Space", 0x23: "End",
    0x2D: "Insert", 0x2E: "Delete", 0xBB: "=", 0xBD: "-", 0xBA: ";",
    0xDE: "'", 0xC0: "`", 0xBF: "/",
}
for _i in range(26):
    _VK_NAMES[0x41 + _i] = chr(ord("A") + _i)
for _i in range(10):
    _VK_NAMES[0x30 + _i] = chr(ord("0") + _i)
for _i in range(12):
    _VK_NAMES[0x70 + _i] = f"F{_i + 1}"


def hotkey_label(mods: int, vk: int) -> str:
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    parts.append(_VK_NAMES.get(vk, f"VK{vk:#x}"))
    return "+".join(parts)


DEFAULTS["hotkeys"] = {action: list(combo) for action, combo in DEFAULT_HOTKEYS.items()}


def load() -> dict:
    if not SETTINGS_PATH.exists():
        return dict(DEFAULTS)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in DEFAULTS})
    if merged["voice"] not in VOICES:
        merged["voice"] = DEFAULTS["voice"]
    if merged["tts_engine"] not in TTS_ENGINES:
        merged["tts_engine"] = DEFAULTS["tts_engine"]
    merged["speed"] = max(SPEED_MIN, min(SPEED_MAX, float(merged["speed"])))

    # Per-action merge, not a flat replace — one corrupt/missing entry in a
    # saved file shouldn't wipe out every other customised shortcut.
    saved_hotkeys = data.get("hotkeys") if isinstance(data.get("hotkeys"), dict) else {}
    hotkeys = {}
    for action, default_combo in DEFAULT_HOTKEYS.items():
        combo = saved_hotkeys.get(action)
        if (isinstance(combo, (list, tuple)) and len(combo) == 2
                and all(isinstance(x, int) for x in combo)):
            hotkeys[action] = list(combo)
        else:
            hotkeys[action] = list(default_combo)
    merged["hotkeys"] = hotkeys
    return merged


def save(settings: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
