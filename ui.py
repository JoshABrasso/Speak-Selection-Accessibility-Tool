"""
Floating status panel + settings window for Speak Selected.

Runs its own Tk root in a dedicated thread (Tk is not thread-safe to share, so
every widget touch happens only from that thread — other threads talk to it
through a plain queue). The panel is secondary to the keyboard/voice control
surface: it's there for a glance or for screen-sharing, never required.
"""

import ctypes
import queue
import threading
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

import app_paths
import autostart as autostart_module
import settings as settings_module
import theme as theme_module
import tts_sapi

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
DWMWA_USE_IMMERSIVE_DARK_MODE = 20


def _enable_app_dark_mode() -> None:
    """The native ("vista" ttk theme) Combobox/Scale controls render with a
    plain white field no matter what colour is configured on them —
    confirmed from a real screenshot: white-on-white, unreadable text,
    visible only when focus briefly highlights it blue. Windows 11's own
    native dark-mode apps fix this with a specific, if under-documented,
    pair of calls: opt the whole process into allowing dark controls
    (SetPreferredAppMode), then opt each individual control into dark
    rendering (see _apply_dark_mode_to_control below) — real native
    rendering AND correct dark-mode colours are not mutually exclusive on
    Windows 11, they just both need to be asked for explicitly."""
    try:
        uxtheme = ctypes.WinDLL("uxtheme", use_last_error=True)
        SET_PREFERRED_APP_MODE_ORDINAL = 135
        ALLOW_DARK = 1
        set_preferred_app_mode = uxtheme[SET_PREFERRED_APP_MODE_ORDINAL]
        set_preferred_app_mode(ALLOW_DARK)
    except Exception:
        pass  # older Windows without this — controls stay native-light, not fatal


def _apply_dark_mode_to_control(widget: tk.Misc) -> None:
    """Applied per-control (Combobox, Scale), not the top-level window —
    these are leaf controls, so winfo_id() is already their real hwnd,
    unlike a decorated Toplevel's inner/outer split (see _top_level_hwnd)."""
    try:
        ctypes.windll.uxtheme.SetWindowTheme(widget.winfo_id(), "DarkMode_Explorer", None)
    except Exception:
        pass


def _style_option_menu(menu_widget: tk.OptionMenu, t) -> None:
    """Confirmed from a real screenshot: the native ("vista") Combobox still
    renders a plain white field no matter what style colour or SetWindowTheme
    call is applied to it — UxTheme owns that control's drawing completely.
    A plain tk.OptionMenu is drawn by Tk itself, not the OS theme engine, so
    every colour below actually takes effect — trading a sliver of native
    chrome on four rows for dropdown text a dark-mode user can actually
    read, which matters more."""
    menu_widget.configure(
        bg=t.field_bg, fg=t.fg, activebackground=t.hover, activeforeground=t.fg,
        highlightthickness=1, highlightbackground=t.separator, highlightcolor=t.separator,
        bd=0, relief="flat", font=("Segoe UI", 10), anchor="w", width=22,
        cursor="hand2", padx=8, pady=4,
    )
    dropdown = menu_widget["menu"]
    dropdown.configure(bg=t.field_bg, fg=t.fg, activebackground=t.hover,
                        activeforeground=t.fg, bd=0, font=("Segoe UI", 10))


def _set_option_menu_values(menu_widget: tk.OptionMenu, variable: tk.StringVar,
                             values: list[str], on_select, t) -> None:
    """Rebuilds an OptionMenu's dropdown entries — used both at first build
    and whenever the choices change at runtime (e.g. Voice depends on which
    Language is selected)."""
    dropdown = menu_widget["menu"]
    dropdown.delete(0, "end")
    for value in values:
        dropdown.add_command(label=value, command=lambda v=value: (variable.set(v), on_select()))
    dropdown.configure(bg=t.field_bg, fg=t.fg, activebackground=t.hover,
                        activeforeground=t.fg, bd=0, font=("Segoe UI", 10))


SPI_GETWORKAREA = 0x0030


def _work_area() -> tuple[int, int, int, int]:
    """The screen area Windows actually leaves free for normal windows —
    excludes the taskbar automatically, regardless of which edge of the
    screen it's docked to (top/bottom/left/right), which a plain
    winfo_screenwidth()/height() clamp would not account for."""
    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    return rect.left, rect.top, rect.right, rect.bottom


GA_ROOT = 2


def _top_level_hwnd(widget: tk.Misc) -> int:
    """widget.winfo_id() returns Tk's *inner content* window handle for a
    normally-decorated Toplevel, not the outer OS frame window that actually
    owns the title bar — DWM attributes applied to the wrong one silently
    do nothing to the title bar, which is exactly what happened here
    (confirmed: the fix didn't take even after retrying at show-time, so the
    first assumption — a timing issue — was wrong; this is the real cause).
    GetAncestor(..., GA_ROOT) walks up to the true top-level window
    regardless of Tk's internal nesting."""
    child_hwnd = widget.winfo_id()
    return ctypes.windll.user32.GetAncestor(child_hwnd, GA_ROOT)


def _apply_titlebar_theme(widget: tk.Misc, dark: bool) -> None:
    """Windows 11's native title bar doesn't automatically match a window's
    own content — confirmed from a real screenshot: Settings had a white
    title bar sitting directly above its dark body, a visible clash. This
    is the documented DWM attribute for opting a window's title bar into
    dark mode; explicitly set either way (not just "dark or leave alone")
    so a light-themed window gets a genuinely light title bar too, not
    whatever Windows happened to default to."""
    try:
        hwnd = _top_level_hwnd(widget)
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        pass  # older Windows without this attribute — default title bar, not fatal


GWL_STYLE = -16
WS_MAXIMIZEBOX = 0x00010000
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0020  # NOSIZE | NOMOVE | NOZORDER | FRAMECHANGED


def _remove_maximize_button(widget: tk.Misc) -> None:
    """Minimize stays (these windows should behave like a normal app you
    can tuck away, not a disposable popup), but there's nothing to maximize
    into — a fixed-size settings/welcome dialog has no resizable layout to
    grow. `resizable(False, False)` only greys the button out; this removes
    it from the title bar entirely via the real window style bits."""
    try:
        user32 = ctypes.windll.user32
        user32.GetWindowLongPtrW.restype = ctypes.c_longlong
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongPtrW.restype = ctypes.c_longlong
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_longlong]
        hwnd = _top_level_hwnd(widget)
        style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style & ~WS_MAXIMIZEBOX)
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FLAGS)
    except Exception:
        pass  # cosmetic — a visible-but-disabled maximize button isn't fatal


def _circled_symbol_image(plus: bool, color: tuple, size: int = 28) -> Image.Image:
    """The standard circled-plus/circled-minus look — drawn directly rather
    than relying on a font glyph: neither Segoe Fluent Icons nor Segoe UI
    actually has a combined plus/minus-in-a-circle character, confirmed by
    testing it (renders as a missing-glyph box), so this draws the circle
    and the bar(s) itself instead of guessing a codepoint that doesn't exist.

    Drawn at 4x and downsampled, with a stroke weight matched to the Segoe
    Fluent Icons glyphs around it — the first version used a flat per-size
    line width and came out visibly heavier/bolder than the font-rendered
    icons next to it (confirmed from a real screenshot, not just reasoning
    about it), since raw PIL line-drawing at native size looks chunkier
    than a font's hinted antialiasing. Supersampling fixes both the weight
    mismatch and the jaggedness in one pass. `color` is the active theme's
    icon colour — white on dark, near-black on light — not hardcoded, since
    a white icon would be invisible against a light-theme background."""
    scale = 4
    render_size = size * scale
    img = Image.new("RGBA", (render_size, render_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = render_size * 0.10
    width = round(1.6 * scale)  # ~1.6px effective weight, matching the Fluent glyphs
    draw.ellipse((pad, pad, render_size - pad, render_size - pad), outline=color, width=width)
    cx = cy = render_size / 2
    arm = render_size * 0.21
    draw.line([(cx - arm, cy), (cx + arm, cy)], fill=color, width=width)
    if plus:
        draw.line([(cx, cy - arm), (cx, cy + arm)], fill=color, width=width)
    return img.resize((size, size), Image.LANCZOS)


def _apply_native_rounded_corners(widget: tk.Misc) -> None:
    """Windows 11 native rounded window corners, via DWM — makes an
    overrideredirect popup look like a real system surface instead of a
    plain rectangle. Uses the resolved top-level handle (see
    _top_level_hwnd) since a normally-decorated Toplevel's winfo_id() is
    its inner content window, not the outer frame DWM actually paints."""
    try:
        hwnd = _top_level_hwnd(widget)
        pref = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), ctypes.sizeof(pref)
        )
    except Exception:
        pass  # older Windows without this DWM attribute — plain corners, not fatal


_FADE_DELAY_MS = 4000  # how long the panel stays after speech ends, before fading
_FADE_STEPS = 12
_FADE_INTERVAL_MS = 30


class UIController:
    def __init__(self, on_voice_change, on_speed_change, get_state,
                 on_pause_resume, on_stop, on_voice_cycle, on_speed_step,
                 on_position_change, on_confirmation_mode_change,
                 on_repeat, on_back, on_forward, on_panel_behavior_change,
                 on_rebind_hotkey, on_exit, on_tts_engine_change, on_sapi_voice_change):
        self._cmd_queue: "queue.Queue" = queue.Queue()
        self._on_voice_change = on_voice_change
        self._on_speed_change = on_speed_change
        self._get_state = get_state
        self._on_pause_resume = on_pause_resume
        self._on_stop = on_stop
        self._on_voice_cycle = on_voice_cycle
        self._on_speed_step = on_speed_step
        self._on_position_change = on_position_change
        self._on_confirmation_mode_change = on_confirmation_mode_change
        self._on_repeat = on_repeat
        self._on_back = on_back
        self._on_forward = on_forward
        self._on_panel_behavior_change = on_panel_behavior_change
        self._on_rebind_hotkey = on_rebind_hotkey
        self._on_exit = on_exit
        self._on_tts_engine_change = on_tts_engine_change
        self._on_sapi_voice_change = on_sapi_voice_change
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # --- public, thread-safe API, callable from any thread ---

    def show_osd(self, text: str) -> None:
        self._cmd_queue.put(("show_osd", text))

    def hide_osd(self) -> None:
        self._cmd_queue.put(("hide_osd", None))

    def open_settings(self) -> None:
        self._cmd_queue.put(("open_settings", None))

    def show_onboarding(self) -> None:
        self._cmd_queue.put(("show_onboarding", None))

    def set_paused(self, is_paused: bool) -> None:
        self._cmd_queue.put(("set_paused", is_paused))

    # --- UI thread internals ---

    def _set_icon(self, window: tk.Misc) -> None:
        """Tkinter Toplevels don't reliably inherit the root's icon on
        Windows — confirmed from a real screenshot: Settings was showing
        Tk's own default feather icon in its title bar, not ours, despite
        the root having iconbitmap set. Call this on every Toplevel
        explicitly rather than assuming inheritance works."""
        try:
            window.iconbitmap(str(app_paths.app_dir() / "app.ico"))
        except Exception:
            pass  # missing/unreadable icon file is cosmetic, never fatal

    def _run(self) -> None:
        self.theme = theme_module.get_theme()
        self.theme_is_dark = self.theme is theme_module.DARK
        if self.theme_is_dark:
            _enable_app_dark_mode()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Speak Selected")
        self._set_icon(self.root)

        self._build_osd()
        self._build_settings()
        self._build_onboarding()

        self.root.after(30, self._poll_queue)
        self.root.mainloop()

    def _build_osd(self) -> None:
        # Styled to match Windows' own native toolbars (e.g. the Magnifier
        # toolbar) in whichever theme Windows is actually set to — grouped
        # buttons separated by thin divider lines, a title strip with a
        # minimize control — not a custom-looking widget, and not assumed dark.
        t = self.theme
        BG, BG_HEADER, FG, SEPARATOR = t.bg, t.bg_header, t.fg, t.separator

        self.osd = tk.Toplevel(self.root)
        self._set_icon(self.osd)
        self.osd.overrideredirect(True)
        self.osd.attributes("-topmost", True)
        self.osd.configure(bg=SEPARATOR)  # 1px border colour, via padding below
        self.osd.withdraw()

        shell = tk.Frame(self.osd, bg=BG)
        shell.pack(padx=1, pady=1)  # the SEPARATOR-coloured root shows through as a 1px border

        self._drag_handle = tk.Frame(shell, bg=BG_HEADER, cursor="fleur")
        self._drag_handle.pack(fill="x")
        tk.Label(self._drag_handle, text="Speak Selected", fg=t.fg_muted, bg=BG_HEADER,
                 font=("Segoe UI", 9), padx=12, pady=5).pack(side="left")
        # A real quit, distinct from minimize — a public-release app needs
        # an obvious, always-available way to actually exit, not just the
        # tray menu (easy to miss) or Task Manager. Native close-button red
        # hover is correct here, since this genuinely does quit.
        tk.Button(self._drag_handle, text="✕", command=self._on_exit, fg=t.fg_muted, bg=BG_HEADER,
                  activebackground="#e81123", activeforeground="#ffffff", bd=0,
                  font=("Segoe UI", 9), padx=10, pady=5, cursor="arrow").pack(side="right")
        # Minimize — withdraws the panel but the app keeps running from the
        # tray. This used to be the only control here, drawn as "✕" with
        # the close-button red hover, which actively told the user this
        # would quit the app — it didn't. Now there's a real quit above,
        # and this one has a neutral hover matching what it actually does.
        tk.Button(self._drag_handle, text="—", command=self._hide_now, fg=t.fg_muted, bg=BG_HEADER,
                  activebackground=t.hover, activeforeground=FG, bd=0,
                  font=("Segoe UI", 9), padx=10, pady=5, cursor="arrow").pack(side="right")

        self._drag_handle.bind("<ButtonPress-1>", self._on_drag_start)
        self._drag_handle.bind("<B1-Motion>", self._on_drag_motion)
        self._drag_handle.bind("<ButtonRelease-1>", self._on_drag_end)

        # Icon-first, no button-box chrome: a bordered box around a glyph
        # shrinks the icon and adds visual clutter (per Josh's note, and
        # visible in the Mac reference) — so these are plain, borderless,
        # same-background widgets that only a hover/focus highlight marks
        # as interactive. Still real tk.Button widgets underneath, so they
        # stay Tab-focusable and show the native focus ring.
        #
        # "Segoe Fluent Icons" is the actual font Windows' own native UI
        # (Magnifier included) draws its icons from — plain emoji fonts
        # render these glyphs as mismatched, boxed, full-colour pictures,
        # which is exactly the wrong look. Codepoints confirmed from
        # Microsoft's own Segoe Fluent Icons reference, not guessed.
        FLUENT = "Segoe Fluent Icons"
        self._ICON_PAUSE, self._ICON_PLAY = "", ""
        ICON_STOP, ICON_MIC, ICON_SETTINGS = "", "", ""
        ICON_REPEAT, ICON_BACK, ICON_FORWARD = "", "", ""

        ICON_FONT = (FLUENT, 16)
        BTN_STYLE = dict(fg=FG, bg=BG, activebackground=t.hover,
                          activeforeground=FG, bd=0, highlightthickness=0,
                          relief="flat", font=ICON_FONT, padx=10, pady=6, cursor="hand2")
        # Speed controls: the standard circled-plus/circled-minus look,
        # drawn directly (see _circled_symbol_image) since no system icon
        # font actually has this as one combined glyph.
        ICON_BTN_STYLE = dict(fg=FG, bg=BG, activebackground=t.hover,
                               bd=0, highlightthickness=0, relief="flat",
                               padx=8, pady=6, cursor="hand2")
        self._icon_minus = ImageTk.PhotoImage(_circled_symbol_image(plus=False, color=t.icon_rgba))
        self._icon_plus = ImageTk.PhotoImage(_circled_symbol_image(plus=True, color=t.icon_rgba))

        self.osd_label = tk.Label(shell, text="", fg=t.fg_secondary, bg=BG,
                                   font=("Segoe UI", 10), anchor="w", padx=14)
        self.osd_label.pack(fill="x", pady=(8, 2))

        # One long, thin horizontal strip — controls front and centre,
        # matching the Magnifier/Mac reference layout: slower — pause —
        # stop — faster — divider — voice — settings.
        btn_row = tk.Frame(shell, bg=BG, padx=6)
        btn_row.pack(pady=(2, 10))

        def separator():
            tk.Frame(btn_row, bg=SEPARATOR, width=1).pack(side="left", fill="y", padx=10, pady=2)

        tk.Button(btn_row, text=ICON_REPEAT, command=self._on_repeat, **BTN_STYLE).pack(side="left", padx=(0, 6))
        tk.Button(btn_row, image=self._icon_minus, command=lambda: self._on_speed_step(-1), **ICON_BTN_STYLE).pack(side="left")
        tk.Button(btn_row, text=ICON_BACK, command=self._on_back, **BTN_STYLE).pack(side="left")
        self.pause_btn = tk.Button(btn_row, text=self._ICON_PAUSE, command=self._on_pause_resume, **BTN_STYLE)
        self.pause_btn.pack(side="left", padx=(6, 6))
        tk.Button(btn_row, text=ICON_STOP, command=self._on_stop, **BTN_STYLE).pack(side="left", padx=(0, 6))
        tk.Button(btn_row, text=ICON_FORWARD, command=self._on_forward, **BTN_STYLE).pack(side="left")
        tk.Button(btn_row, image=self._icon_plus, command=lambda: self._on_speed_step(1), **ICON_BTN_STYLE).pack(side="left")
        separator()
        tk.Button(btn_row, text=ICON_MIC, command=self._on_voice_cycle, **BTN_STYLE).pack(side="left", padx=(0, 6))
        tk.Button(btn_row, text=ICON_SETTINGS, command=self._open_settings, **BTN_STYLE).pack(side="left")

        self._fade_job = None
        self._fade_alpha = 1.0
        self._mouse_over = False
        self._pending_hide = False
        self._drag_data = None
        self.osd.bind("<Enter>", self._on_mouse_enter)
        self.osd.bind("<Leave>", self._on_mouse_leave)

        self.osd.update_idletasks()
        _apply_native_rounded_corners(self.osd)
        self._position_osd()  # establishes a real position even before it's first shown

    def _hide_now(self) -> None:
        if self._fade_job is not None:
            self.root.after_cancel(self._fade_job)
            self._fade_job = None
        self.osd.withdraw()

    def _on_mouse_enter(self, _event=None) -> None:
        self._mouse_over = True
        if self._fade_job is not None:
            self.root.after_cancel(self._fade_job)
            self._fade_job = None
        self.osd.attributes("-alpha", 1.0)

    def _on_mouse_leave(self, _event=None) -> None:
        self._mouse_over = False
        # Only resume a fade that was already due (speech genuinely finished
        # while the mouse happened to be hovering) — leaving the panel must
        # never start a fade on its own, or it'll vanish mid-speech the
        # moment the mouse drifts away (e.g. back to the text being read).
        if self._pending_hide:
            self._pending_hide = False
            self._schedule_fade()

    def _on_drag_start(self, event) -> None:
        self._drag_data = (event.x_root - self.osd.winfo_x(), event.y_root - self.osd.winfo_y())

    def _on_drag_motion(self, event) -> None:
        if self._drag_data is None:
            return
        offset_x, offset_y = self._drag_data
        self.osd.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _on_drag_end(self, _event) -> None:
        self._drag_data = None
        self._on_position_change(self.osd.winfo_x(), self.osd.winfo_y())

    def _position_osd(self) -> None:
        self.osd.update_idletasks()
        state = self._get_state()
        saved_x, saved_y = state.get("panel_x"), state.get("panel_y")
        if saved_x is not None and saved_y is not None:
            self.osd.geometry(f"+{saved_x}+{saved_y}")
            return
        # Bottom-right of the real work area, not raw screen dimensions —
        # the same taskbar-aware calculation used for Settings/Onboarding
        # (see _work_area). The previous hardcoded 80px bottom margin
        # assumed a bottom-docked taskbar specifically; this is correct
        # regardless of which edge it's actually docked to.
        w = self.osd.winfo_width()
        h = self.osd.winfo_height()
        left, top, right, bottom = _work_area()
        x = right - w - 24
        y = bottom - h - 24
        self.osd.geometry(f"+{x}+{y}")

    def _show_osd(self, text: str) -> None:
        if self._fade_job is not None:
            self.root.after_cancel(self._fade_job)
            self._fade_job = None
        self._pending_hide = False
        self.osd_label.configure(text=text)
        self.osd.attributes("-alpha", 1.0)
        self._fade_alpha = 1.0
        self._position_osd()
        self.osd.deiconify()

    def _hide_osd(self) -> None:
        # Called only by the engine, only once speech has genuinely finished
        # (or been stopped) — this is the sole real trigger for the fade.
        if self._fade_job is not None:
            self.root.after_cancel(self._fade_job)
            self._fade_job = None
        # "Persistent" (the default) means exactly that — the panel behaves
        # like a normal app window and stays up until the user closes or
        # minimizes it themselves. Auto-fade is opt-in, not assumed: a panel
        # that vanishes on its own can work against someone who needs more
        # time to notice or read it, not just be a convenience for everyone.
        if self._get_state().get("panel_behavior", "persistent") != "auto_fade":
            return
        if self._mouse_over:
            self._pending_hide = True
            return
        self._schedule_fade()

    def _schedule_fade(self) -> None:
        if self._fade_job is not None:
            self.root.after_cancel(self._fade_job)
        self._fade_job = self.root.after(_FADE_DELAY_MS, self._begin_fade)

    def _begin_fade(self) -> None:
        self._fade_alpha = 1.0
        self._fade_step()

    def _fade_step(self) -> None:
        self._fade_alpha -= 1.0 / _FADE_STEPS
        if self._fade_alpha <= 0:
            self.osd.withdraw()
            self._fade_job = None
            return
        self.osd.attributes("-alpha", self._fade_alpha)
        self._fade_job = self.root.after(_FADE_INTERVAL_MS, self._fade_step)

    def _build_settings(self) -> None:
        self.settings_win = tk.Toplevel(self.root)
        self._set_icon(self.settings_win)
        t = self.theme
        BG, FG, MUTED = t.bg, t.fg, t.fg_muted

        self.settings_win.title("Speak Selected — Settings")
        self.settings_win.attributes("-topmost", True)
        self.settings_win.protocol("WM_DELETE_WINDOW", self._close_settings)
        self.settings_win.withdraw()
        self.settings_win.resizable(False, False)
        self.settings_win.configure(bg=BG)

        style = ttk.Style(self.settings_win)
        # "default" (Tk's Win95-bevel theme) and "clam" (Tk's own flat
        # cross-platform theme) were both tried — neither is actually
        # Windows-native rendering, they're Tk drawing its own look. "vista"
        # is the one ttk theme that delegates to the real OS control
        # renderer (UxTheme/comctl32), so it's genuinely native chrome
        # instead of an approximation of it — at the cost of not fully
        # honouring custom dark colours the way the hand-drawn themes did,
        # since the OS draws these controls itself.
        style.theme_use("vista")
        style.configure("Themed.Horizontal.TScale", background=BG)

        # Left-tab-column + right-content-pane — the standard native
        # settings-dialog layout, not a bespoke one. Per Josh: "disability
        # needs consistency and familiarity" — this is an accessibility
        # requirement here, not a visual preference.
        NAV_WIDTH, CONTENT_WIDTH, CONTENT_HEIGHT = 136, 420, 380

        outer = tk.Frame(self.settings_win, bg=BG)
        outer.pack(fill="both", expand=True)

        tk.Label(outer, text="Settings", fg=FG, bg=BG, font=("Segoe UI", 14, "bold")) \
            .pack(anchor="w", padx=20, pady=(16, 10))
        tk.Frame(outer, bg=t.separator, height=1).pack(fill="x")

        body = tk.Frame(outer, bg=BG)
        body.pack(fill="both", expand=True)

        nav = tk.Frame(body, bg=t.bg_header, width=NAV_WIDTH, height=CONTENT_HEIGHT)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)

        content = tk.Frame(body, bg=BG, width=CONTENT_WIDTH, height=CONTENT_HEIGHT)
        content.pack(side="left", fill="both", expand=True)
        content.pack_propagate(False)

        general_frame = tk.Frame(content, bg=BG)
        voice_frame = tk.Frame(content, bg=BG)
        keyboard_frame = tk.Frame(content, bg=BG)
        self._tab_frames: dict[str, tk.Frame] = {
            "General": general_frame, "Voice": voice_frame, "Keyboard": keyboard_frame,
        }
        self._tab_buttons: dict[str, tk.Button] = {}

        def select_tab(name: str) -> None:
            for frame in self._tab_frames.values():
                frame.pack_forget()
            self._tab_frames[name].pack(fill="both", expand=True, padx=20, pady=18)
            for tab_name, btn in self._tab_buttons.items():
                active = tab_name == name
                btn.configure(bg=t.hover if active else t.bg_header, fg=FG if active else MUTED)

        for name in ("General", "Voice", "Keyboard"):
            btn = tk.Button(nav, text=name, fg=MUTED, bg=t.bg_header, activebackground=t.hover,
                             activeforeground=FG, bd=0, anchor="w", relief="flat",
                             font=("Segoe UI", 10), padx=16, pady=10, cursor="hand2",
                             command=lambda n=name: select_tab(n))
            btn.pack(fill="x")
            self._tab_buttons[name] = btn

        def row_label(parent: tk.Frame, text: str, row: int) -> tk.Label:
            label = tk.Label(parent, text=text, fg=FG, bg=BG, font=("Segoe UI", 10))
            label.grid(row=row, column=0, sticky="w", pady=6, padx=(0, 16))
            return label

        # --- General tab: spoken confirmations, panel behavior ---
        row_label(general_frame, "Spoken confirmations", 0)
        self.confirmation_var = tk.StringVar()
        confirmation_labels = [settings_module.CONFIRMATION_MODE_LABELS[m] for m in settings_module.CONFIRMATION_MODES]
        confirmation_menu = tk.OptionMenu(general_frame, self.confirmation_var, "")
        _set_option_menu_values(confirmation_menu, self.confirmation_var, confirmation_labels,
                                 self._on_confirmation_mode_selected, t)
        _style_option_menu(confirmation_menu, t)
        confirmation_menu.grid(row=0, column=1, sticky="w")

        row_label(general_frame, "Panel", 1)
        self.panel_behavior_var = tk.StringVar()
        panel_behavior_labels = [settings_module.PANEL_BEHAVIOR_LABELS[m] for m in settings_module.PANEL_BEHAVIORS]
        panel_behavior_menu = tk.OptionMenu(general_frame, self.panel_behavior_var, "")
        _set_option_menu_values(panel_behavior_menu, self.panel_behavior_var, panel_behavior_labels,
                                 self._on_panel_behavior_selected, t)
        _style_option_menu(panel_behavior_menu, t)
        panel_behavior_menu.grid(row=1, column=1, sticky="w")

        row_label(general_frame, "Run at startup", 2)
        # Reads the real registry value, not a separate remembered flag in
        # settings.json — Windows' own Task Manager > Startup Apps page can
        # also toggle this key, so settings.json tracking its own copy
        # could silently drift from what's actually true. The registry is
        # the single source of truth; re-read fresh on every Settings open.
        self.run_at_startup_var = tk.BooleanVar(value=autostart_module.is_enabled())
        run_at_startup_check = tk.Checkbutton(
            general_frame, variable=self.run_at_startup_var, fg=FG, bg=BG,
            activebackground=BG, activeforeground=FG, selectcolor=t.field_bg,
            bd=0, highlightthickness=0, cursor="hand2",
            command=self._on_run_at_startup_toggled,
        )
        run_at_startup_check.grid(row=2, column=1, sticky="w")

        # --- Voice tab: engine, (language,) voice, speed ---
        # "Best available" is the default — prefers the enhanced voice
        # (Kokoro) whenever its model is actually present, falling back to
        # Windows' own built-in voice otherwise, so the app can never go
        # silent just because a model file is missing or corrupted.
        row_label(voice_frame, "Voice engine", 0)
        self.tts_engine_var = tk.StringVar()
        engine_menu = tk.OptionMenu(voice_frame, self.tts_engine_var, "")
        _style_option_menu(engine_menu, t)
        engine_menu.grid(row=0, column=1, sticky="w")
        self._engine_menu = engine_menu
        self._engine_status_label = tk.Label(voice_frame, text="", fg=t.fg_muted, bg=BG,
                                              font=("Segoe UI", 9), anchor="w")
        self._engine_status_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))

        self._language_label = row_label(voice_frame, "Language", 2)
        self.language_var = tk.StringVar()
        language_menu = tk.OptionMenu(voice_frame, self.language_var, "")
        _set_option_menu_values(language_menu, self.language_var, settings_module.LANGUAGES,
                                 self._on_language_selected, t)
        _style_option_menu(language_menu, t)
        language_menu.grid(row=2, column=1, sticky="w")
        self._language_menu = language_menu

        row_label(voice_frame, "Voice", 3)
        self.voice_var = tk.StringVar()
        self._voice_menu = tk.OptionMenu(voice_frame, self.voice_var, "")
        _style_option_menu(self._voice_menu, t)
        self._voice_menu.grid(row=3, column=1, sticky="w")

        row_label(voice_frame, "Speed", 4)
        self.speed_var = tk.DoubleVar()
        speed_scale = ttk.Scale(
            voice_frame, from_=settings_module.SPEED_MIN, to=settings_module.SPEED_MAX,
            variable=self.speed_var, orient="horizontal", length=200,
            style="Themed.Horizontal.TScale", command=self._on_speed_dragged,
        )
        speed_scale.grid(row=4, column=1, sticky="w")
        # Restart the in-progress sentence only once the drag finishes, not
        # on every intermediate tick — avoids re-synthesizing repeatedly
        # while the slider is still being moved.
        speed_scale.bind("<ButtonRelease-1>", lambda _e: self._on_speed_change(round(self.speed_var.get(), 1), True))
        # The native ("vista") Scale renders correctly in dark mode already
        # (confirmed) — only the Combobox ignored colours, which is why it
        # alone was replaced with tk.OptionMenu above; the slider keeps
        # native rendering.
        if self.theme_is_dark:
            _apply_dark_mode_to_control(speed_scale)

        # --- Keyboard tab: one row per rebindable action ---
        self._shortcuts_status = tk.Label(keyboard_frame, text="Click Change, then press a new key combo.",
                                           fg=MUTED, bg=BG, font=("Segoe UI", 9), wraplength=CONTENT_WIDTH - 40,
                                           justify="left")
        self._shortcuts_status.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        self._shortcut_labels: dict[str, tk.Label] = {}
        self._shortcut_buttons: dict[str, tk.Button] = {}
        self._capturing_action: str | None = None

        for i, action in enumerate(settings_module.ACTIONS):
            row = 1 + i
            tk.Label(keyboard_frame, text=settings_module.ACTION_LABELS[action], fg=FG, bg=BG,
                     font=("Segoe UI", 10), anchor="w", width=20) \
                .grid(row=row, column=0, sticky="w", pady=3)
            combo_label = tk.Label(keyboard_frame, text="", fg=t.fg_secondary, bg=BG,
                                    font=("Segoe UI", 10, "bold"), width=12, anchor="w")
            combo_label.grid(row=row, column=1, sticky="w", pady=3)
            self._shortcut_labels[action] = combo_label
            change_btn = tk.Button(keyboard_frame, text="Change", fg=FG, bg=t.hover, activebackground=t.hover,
                                    bd=0, font=("Segoe UI", 9), padx=10, pady=3, cursor="hand2",
                                    command=lambda a=action: self._begin_capture(a))
            change_btn.grid(row=row, column=2, sticky="w", padx=(8, 0), pady=3)
            self._shortcut_buttons[action] = change_btn

        self.settings_win.bind("<KeyPress>", self._on_capture_keypress)
        select_tab("General")

        self.settings_win.update_idletasks()
        _apply_native_rounded_corners(self.settings_win)
        _apply_titlebar_theme(self.settings_win, self.theme_is_dark)
        _remove_maximize_button(self.settings_win)

    def _build_onboarding(self) -> None:
        t = self.theme
        BG = t.bg
        self.onboarding_win = tk.Toplevel(self.root)
        self._set_icon(self.onboarding_win)
        self.onboarding_win.title("Welcome to Speak Selected")
        self.onboarding_win.attributes("-topmost", True)
        self.onboarding_win.protocol("WM_DELETE_WINDOW", self.onboarding_win.withdraw)
        self.onboarding_win.withdraw()
        self.onboarding_win.resizable(False, False)
        self.onboarding_win.configure(bg=BG)

        frame = tk.Frame(self.onboarding_win, bg=BG, padx=28, pady=24)
        frame.pack()

        tk.Label(frame, text="Speak Selected", fg=t.fg,
                 bg=BG, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(frame, text="Select text anywhere, hear it read aloud — no app-by-app limits.",
                 fg=t.fg_secondary, bg=BG, font=("Segoe UI", 11), wraplength=420, justify="left") \
            .pack(anchor="w", pady=(4, 18))

        # Built from the live, current hotkeys — not hardcoded defaults.
        # This screen is reachable any time via the tray's "Help /
        # Shortcuts..." menu, not just on first run, so it must stay
        # correct for anyone who has rebound a shortcut in Settings.
        table = tk.Frame(frame, bg=BG)
        table.pack(anchor="w", fill="x")
        self._onboarding_shortcut_labels: list[tuple[tuple[str, ...], tk.Label]] = []
        for i, (actions, desc) in enumerate(self._ONBOARDING_ROWS):
            key_label = tk.Label(table, text=self._onboarding_hotkey_text(actions), fg=t.fg, bg=BG,
                                  font=("Segoe UI", 10, "bold"), anchor="w", width=26)
            key_label.grid(row=i, column=0, sticky="w", pady=2)
            tk.Label(table, text=desc, fg=t.fg_secondary, bg=BG, font=("Segoe UI", 10),
                     anchor="w").grid(row=i, column=1, sticky="w", pady=2)
            self._onboarding_shortcut_labels.append((actions, key_label))

        tk.Label(frame, text="A panel appears while it's speaking with the same controls as "
                              "buttons — drag it anywhere, it remembers where you put it. "
                              "Look for the speaker icon in the system tray for Settings any time.",
                 fg=t.fg_muted, bg=BG, font=("Segoe UI", 9), wraplength=420, justify="left") \
            .pack(anchor="w", pady=(16, 18))

        tk.Button(frame, text="Got it", command=self.onboarding_win.withdraw,
                  fg=t.cta_fg, bg=t.cta_bg, activebackground=t.hover,
                  bd=0, font=("Segoe UI", 10, "bold"), padx=18, pady=8,
                  cursor="hand2").pack(anchor="e")

        self.onboarding_win.update_idletasks()
        _apply_native_rounded_corners(self.onboarding_win)
        _apply_titlebar_theme(self.onboarding_win, self.theme_is_dark)
        _remove_maximize_button(self.onboarding_win)

    def _refresh_shortcut_labels(self) -> None:
        hotkeys = self._get_state().get("hotkeys", {})
        for action, label in self._shortcut_labels.items():
            mods, vk = hotkeys.get(action, settings_module.DEFAULT_HOTKEYS[action])
            label.configure(text=settings_module.hotkey_label(mods, vk))

    def _begin_capture(self, action: str) -> None:
        self._cancel_capture()
        self._capturing_action = action
        self._shortcut_buttons[action].configure(text="Press keys... (Esc cancels)")
        self._shortcuts_status.configure(text="Waiting for a key combo that includes Ctrl or Alt...")
        self.settings_win.focus_force()

    def _cancel_capture(self) -> None:
        if self._capturing_action is not None:
            self._shortcut_buttons[self._capturing_action].configure(text="Change")
        self._capturing_action = None
        self._shortcuts_status.configure(text="Click Change, then press a new key combo.")

    # Keysyms for the modifier keys themselves — pressing just "Ctrl" alone
    # must not finalize a combo, only the following real key should.
    _MODIFIER_KEYSYMS = {
        "Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R",
    }

    # Onboarding screen rows — grouped the same way regardless of what the
    # actual key combos are, since speed up/down and previous/next sentence
    # read naturally as one line each.
    _ONBOARDING_ROWS = [
        (("speak",), "Speak the selected text"),
        (("pause",), "Pause / resume"),
        (("stop",), "Stop"),
        (("voice",), "Cycle voice"),
        (("speed_up", "speed_down"), "Speed up / down"),
        (("repeat",), "Repeat from the start"),
        (("back", "forward"), "Previous / next sentence"),
        (("settings",), "Open settings"),
    ]

    def _onboarding_hotkey_text(self, actions: tuple) -> str:
        hotkeys = self._get_state().get("hotkeys", {})
        labels = []
        for action in actions:
            mods, vk = hotkeys.get(action, settings_module.DEFAULT_HOTKEYS[action])
            labels.append(settings_module.hotkey_label(mods, vk))
        return " / ".join(labels)

    def _refresh_onboarding_shortcuts(self) -> None:
        for actions, label in self._onboarding_shortcut_labels:
            label.configure(text=self._onboarding_hotkey_text(actions))

    def _on_capture_keypress(self, event) -> None:
        action = self._capturing_action
        if action is None:
            return
        if event.keysym == "Escape":
            self._cancel_capture()
            return
        if event.keysym in self._MODIFIER_KEYSYMS:
            return  # wait for the actual key, not just the modifier going down

        VK_CONTROL, VK_MENU, VK_SHIFT = 0x11, 0x12, 0x10
        user32 = ctypes.windll.user32
        ctrl = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        alt = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        shift = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)

        if not (ctrl or alt):
            self._shortcuts_status.configure(text="Needs Ctrl or Alt held down too — try again.")
            return

        mods = ((settings_module.MOD_CONTROL if ctrl else 0)
                | (settings_module.MOD_ALT if alt else 0)
                | (settings_module.MOD_SHIFT if shift else 0))
        vk = event.keycode

        applied = self._on_rebind_hotkey(action, mods, vk)
        if applied:
            self._refresh_shortcut_labels()
            self._cancel_capture()
            self._shortcuts_status.configure(
                text=f"{settings_module.ACTION_LABELS[action]} set to {settings_module.hotkey_label(mods, vk)}.")
        else:
            self._shortcuts_status.configure(
                text=f"{settings_module.hotkey_label(mods, vk)} is already used by another app — try a different combo.")

    def _position_near_panel(self, win: tk.Toplevel) -> None:
        """Open a secondary window near wherever the panel actually is, not
        at Tk's arbitrary default spot — on a multi-monitor, zoomed-in setup,
        a window opening somewhere else on screen is effectively invisible.
        Also clamped to the real work area (not just raw screen dimensions),
        since the naive +20/+20 offset could previously push the window
        partly off-screen whenever the panel itself sat near an edge —
        exactly the case when the taskbar is docked top/bottom and shrinks
        the usable area the panel was dragged into."""
        win.update_idletasks()
        osd_x, osd_y = self.osd.winfo_x(), self.osd.winfo_y()
        win_w = win.winfo_width() if win.winfo_width() > 1 else win.winfo_reqwidth()
        win_h = win.winfo_height() if win.winfo_height() > 1 else win.winfo_reqheight()
        left, top, right, bottom = _work_area()
        x = max(left, min(osd_x + 20, right - win_w))
        y = max(top, min(osd_y + 20, bottom - win_h))
        win.geometry(f"+{x}+{y}")

    def _show_onboarding(self) -> None:
        self._refresh_onboarding_shortcuts()
        self._position_near_panel(self.onboarding_win)
        self.onboarding_win.deiconify()
        self.onboarding_win.lift()
        self.onboarding_win.focus_force()
        # Re-assert after showing, not just at build time — a withdrawn
        # window that's never been mapped to the screen doesn't reliably
        # keep a DWM attribute or window-style bit applied before it was
        # first shown. The maximize button removal had exactly this bug:
        # it was only ever called once at build time while the window was
        # still withdrawn, and Josh's own screenshot confirmed the button
        # was still visible — this re-assert is the actual fix, not a
        # belt-and-braces extra.
        _apply_titlebar_theme(self.onboarding_win, self.theme_is_dark)
        _remove_maximize_button(self.onboarding_win)

    def _on_language_selected(self, _event=None) -> None:
        """Language changed — repopulate the Voice list for it and pick its
        first voice (the previously selected voice is a different language
        now, so there's nothing sensible to keep selected)."""
        language = self.language_var.get()
        voices = settings_module.voices_for_language(language)
        _set_option_menu_values(self._voice_menu, self.voice_var,
                                 [settings_module.voice_display_name(v) for v in voices],
                                 self._on_voice_selected, self.theme)
        if voices:
            self.voice_var.set(settings_module.voice_display_name(voices[0]))
            self._on_voice_change(voices[0])

    def _on_voice_selected(self) -> None:
        language = self.language_var.get()
        name = self.voice_var.get()
        for voice_id in settings_module.voices_for_language(language):
            if settings_module.voice_display_name(voice_id) == name:
                self._on_voice_change(voice_id)
                return

    def _on_sapi_voice_selected(self) -> None:
        name = self.voice_var.get()
        voice_id = self._sapi_voice_ids.get(name)
        if voice_id is not None:
            self._on_sapi_voice_change(voice_id, name)

    def _on_tts_engine_selected(self) -> None:
        label = self.tts_engine_var.get()
        kokoro_available = self._get_state().get("kokoro_available", False)
        for engine, engine_label in settings_module.TTS_ENGINE_LABELS.items():
            if engine_label == label and (engine != "kokoro" or kokoro_available):
                self._on_tts_engine_change(engine)
                self._refresh_voice_section()
                return

    def _refresh_voice_section(self) -> None:
        """Rebuilds the engine picker and switches the Language/Voice rows
        between Kokoro's language-grouped voices and Windows' own flat,
        machine-dependent SAPI voice list — these have genuinely different
        shapes, not just different values, so this is a real structural
        swap, not a simple field refresh."""
        state = self._get_state()
        kokoro_available = state.get("kokoro_available", False)
        active_engine = state.get("active_engine", "kokoro")
        preference = state.get("tts_engine", "auto")
        t = self.theme

        engines = [e for e in settings_module.TTS_ENGINES if e != "kokoro" or kokoro_available]
        engine_labels = [settings_module.TTS_ENGINE_LABELS[e] for e in engines]
        _set_option_menu_values(self._engine_menu, self.tts_engine_var, engine_labels,
                                 self._on_tts_engine_selected, t)
        self.tts_engine_var.set(settings_module.TTS_ENGINE_LABELS.get(preference, preference))

        active_label = settings_module.TTS_ENGINE_LABELS.get(active_engine, active_engine)
        status = f"Currently using: {active_label}"
        if not kokoro_available:
            status += " — the enhanced voice isn't installed."
        self._engine_status_label.configure(text=status)

        if active_engine == "kokoro":
            self._language_label.grid()
            self._language_menu.grid()
            voice_id = state["voice"]
            language = settings_module.language_for_voice(voice_id)
            self.language_var.set(language)
            voices = settings_module.voices_for_language(language)
            _set_option_menu_values(self._voice_menu, self.voice_var,
                                     [settings_module.voice_display_name(v) for v in voices],
                                     self._on_voice_selected, t)
            self.voice_var.set(settings_module.voice_display_name(voice_id))
        else:
            self._language_label.grid_remove()
            self._language_menu.grid_remove()
            sapi_voices = tts_sapi.list_voices()
            self._sapi_voice_ids = {name: voice_id for voice_id, name in sapi_voices}
            names = [name for _voice_id, name in sapi_voices]
            _set_option_menu_values(self._voice_menu, self.voice_var, names,
                                     self._on_sapi_voice_selected, t)
            current_id = state.get("sapi_voice")
            current_name = next((name for voice_id, name in sapi_voices if voice_id == current_id), None)
            if current_name is None and sapi_voices:
                current_name = sapi_voices[0][1]
            if current_name is not None:
                self.voice_var.set(current_name)

    def _on_speed_dragged(self, value: str) -> None:
        self._on_speed_change(round(float(value), 1), False)

    def _on_confirmation_mode_selected(self, _event=None) -> None:
        label = self.confirmation_var.get()
        for mode, mode_label in settings_module.CONFIRMATION_MODE_LABELS.items():
            if mode_label == label:
                self._on_confirmation_mode_change(mode)
                return

    def _on_panel_behavior_selected(self, _event=None) -> None:
        label = self.panel_behavior_var.get()
        for behavior, behavior_label in settings_module.PANEL_BEHAVIOR_LABELS.items():
            if behavior_label == label:
                self._on_panel_behavior_change(behavior)
                return

    def _on_run_at_startup_toggled(self) -> None:
        wanted = self.run_at_startup_var.get()
        ok = autostart_module.set_enabled(wanted)
        if not ok:
            # Revert the checkbox to the real state rather than leaving it
            # showing something that didn't actually happen.
            self.run_at_startup_var.set(not wanted)

    def _refresh_settings_fields(self) -> None:
        state = self._get_state()
        self._refresh_voice_section()
        self.speed_var.set(state["speed"])
        mode = state.get("confirmation_mode", "keyboard_only")
        self.confirmation_var.set(settings_module.CONFIRMATION_MODE_LABELS.get(mode, mode))
        behavior = state.get("panel_behavior", "persistent")
        self.panel_behavior_var.set(settings_module.PANEL_BEHAVIOR_LABELS.get(behavior, behavior))
        self.run_at_startup_var.set(autostart_module.is_enabled())
        self._refresh_shortcut_labels()

    def _open_settings(self) -> None:
        self._refresh_settings_fields()
        self._position_near_panel(self.settings_win)
        self.settings_win.deiconify()
        self.settings_win.lift()
        self.settings_win.focus_force()
        _apply_titlebar_theme(self.settings_win, self.theme_is_dark)
        _remove_maximize_button(self.settings_win)

    def _close_settings(self) -> None:
        self._cancel_capture()
        self.settings_win.withdraw()

    def _poll_queue(self) -> None:
        try:
            while True:
                cmd, arg = self._cmd_queue.get_nowait()
                if cmd == "show_osd":
                    self._show_osd(arg)
                elif cmd == "hide_osd":
                    self._hide_osd()
                elif cmd == "open_settings":
                    self._open_settings()
                elif cmd == "show_onboarding":
                    self._show_onboarding()
                elif cmd == "set_paused":
                    self.pause_btn.configure(text=self._ICON_PLAY if arg else self._ICON_PAUSE)
        except queue.Empty:
            pass
        self.root.after(30, self._poll_queue)
