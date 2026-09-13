"""Мелочи Windows: звуки, активное окно, язык системы."""

import ctypes
import io
import logging
import math
import struct
import wave
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("localflow")

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- Звуки --------------------------------------------------------------------
# На Mac — системные звуки Pop / Bottle / Funk. У Windows похожих нет, а
# системные «динь» звучат как ошибка, поэтому короткие тоны рисуем сами.

_SOUNDS = {
    # (частота начала, частота конца, длительность, громкость)
    "start": [(880, 1320, 0.07, 0.35)],
    "stop": [(1320, 880, 0.08, 0.30)],
    "cancel": [(440, 330, 0.09, 0.30), (330, 247, 0.12, 0.30)],
}


def _tone_wav(parts, rate=22050) -> bytes:
    frames = bytearray()
    for f0, f1, dur, vol in parts:
        n = int(rate * dur)
        phase = 0.0
        for i in range(n):
            f = f0 + (f1 - f0) * i / n
            phase += 2 * math.pi * f / rate
            env = min(1.0, i / (0.005 * rate), (n - i) / (0.03 * rate))
            frames += struct.pack("<h", int(32767 * vol * env * math.sin(phase)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


class Sounds:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.enabled = True
        self._paths: dict[str, str] = {}
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            for name, parts in _SOUNDS.items():
                path = self.folder / f"{name}.wav"
                data = _tone_wav(parts)
                if not path.exists() or path.read_bytes() != data:
                    path.write_bytes(data)
                self._paths[name] = str(path)
        except OSError as exc:
            log.warning("Звуки не подготовились: %s", exc)

    def play(self, name: str) -> None:
        if not self.enabled or name not in self._paths:
            return
        try:
            import winsound
            winsound.PlaySound(self._paths[name], winsound.SND_FILENAME
                               | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception as exc:
            log.debug("Звук не проигрался: %s", exc)


# --- Активное окно ----------------------------------------------------------
# Имена приложений приводим к маковым: по ним общая логика понимает, что
# это мессенджер, редактор кода или браузер.

_EXE_NAMES = {
    "telegram": "Telegram", "whatsapp": "WhatsApp", "discord": "Discord",
    "slack": "Slack", "viber": "Viber", "signal": "Signal", "skype": "Skype",
    "teams": "Microsoft Teams", "ms-teams": "Microsoft Teams", "zoom": "Zoom",
    "chrome": "Google Chrome", "msedge": "Microsoft Edge", "firefox": "Firefox",
    "brave": "Brave Browser", "opera": "Opera", "vivaldi": "Vivaldi",
    "arc": "Arc", "yandex": "Yandex",
    "code": "Code", "cursor": "Cursor", "windsurf": "Windsurf", "zed": "Zed",
    "sublime_text": "Sublime Text", "idea64": "IntelliJ IDEA",
    "pycharm64": "PyCharm", "webstorm64": "WebStorm", "goland64": "GoLand",
    "windowsterminal": "Terminal", "cmd": "Terminal", "powershell": "Terminal",
    "pwsh": "Terminal", "wezterm-gui": "Terminal", "alacritty": "Alacritty",
    "winword": "Microsoft Word", "outlook": "Microsoft Outlook",
    "olk": "Microsoft Outlook", "excel": "Microsoft Excel",
    "notion": "Notion", "obsidian": "Obsidian", "claude": "Claude",
    "chatgpt": "ChatGPT", "notepad": "Notepad",
}

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
_user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)


def window_title(hwnd=None) -> str:
    hwnd = hwnd or _user32.GetForegroundWindow()
    if not hwnd:
        return ""
    n = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _exe_stem(hwnd) -> str:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return Path(buf.value).stem
    finally:
        _kernel32.CloseHandle(h)


def app_display_name(exe_stem: str, title: str = "") -> str:
    low = exe_stem.lower()
    if low == "applicationframehost" and title:
        # приложения из Microsoft Store живут в общем процессе — имя в заголовке
        return title.rsplit(" - ", 1)[-1].strip()
    return _EXE_NAMES.get(low, exe_stem)


def frontmost_app_name() -> str:
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return ""
        return app_display_name(_exe_stem(hwnd), window_title(hwnd))
    except Exception as exc:
        log.debug("Активное приложение не определилось: %s", exc)
        return ""


def frontmost_window_title() -> str:
    try:
        return window_title()
    except Exception:
        return ""


# --- Язык системы -------------------------------------------------------------

_LANG_BY_PRIMARY = {0x19: "ru", 0x22: "uk", 0x07: "de", 0x09: "en"}


def system_ui_lang() -> str:
    """Язык интерфейса Windows → ru / uk / de / en (всё прочее — en)."""
    try:
        langid = _kernel32.GetUserDefaultUILanguage()
        return _LANG_BY_PRIMARY.get(langid & 0x3FF, "en")
    except Exception:
        return "en"
