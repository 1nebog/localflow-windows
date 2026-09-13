"""Окно панели: Microsoft Edge в режиме приложения — без вкладок и адресной
строки, выглядит как обычное окно программы.

Edge есть в каждой Windows 10 и 11, так что ставить ничего не нужно. У окна
свой отдельный профиль в папке программы: вкладки, вход в аккаунт и
расширения браузера человека сюда не попадают. Нет Edge — панель откроется
в браузере по умолчанию.
"""

import ctypes
import logging
import os
import subprocess
import time
import winreg
from ctypes import wintypes
from pathlib import Path

from . import w32

log = logging.getLogger("localflow")

TITLE = "LocalFlow"
WIDTH, HEIGHT = 760, 860          # в точках без учёта масштаба экрана
STARTING_SEC = 8                  # столько ждём первое окно, не запуская второе

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
w32._proto(w32.user32.EnumWindows, wintypes.BOOL, WNDENUMPROC, wintypes.LPARAM)
w32._proto(w32.user32.IsIconic, wintypes.BOOL, wintypes.HWND)
w32._proto(w32.user32.GetWindowTextW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
w32._proto(w32.user32.GetWindowRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
SW_RESTORE = 9


def find_edge() -> str | None:
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe") as key:
                path = winreg.QueryValue(key, None)
                if path and Path(path).exists():
                    return path
        except OSError:
            pass
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            path = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if path.exists():
                return str(path)
    return None


def _top_windows(pid: int) -> list[int]:
    found = []

    def _each(hwnd, _):
        owner = wintypes.DWORD()
        w32.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and w32.user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(64)
            w32.user32.GetClassNameW(hwnd, cls, 64)
            if cls.value.startswith("Chrome_WidgetWin"):
                title = ctypes.create_unicode_buffer(256)
                w32.user32.GetWindowTextW(hwnd, title, 256)
                if title.value:
                    found.append(hwnd)
        return True

    w32.user32.EnumWindows(WNDENUMPROC(_each), 0)
    return found


class PanelWindow:
    def __init__(self, profile_dir: Path):
        self.profile_dir = Path(profile_dir)
        self._proc: subprocess.Popen | None = None
        self._started = 0.0

    def windows(self) -> list[int]:
        if self._proc is None or self._proc.poll() is not None:
            return []
        return _top_windows(self._proc.pid)

    def open(self, url: str) -> None:
        alive = self._proc is not None and self._proc.poll() is None
        if alive:
            wins = self.windows()
            if wins:
                self._focus(wins[0])
                return
            if time.monotonic() - self._started < STARTING_SEC:
                return            # окно ещё открывается — второе не нужно
        edge = find_edge()
        if edge is None:
            log.info("Панель: Edge не найден, открываю в браузере по умолчанию")
            os.startfile(url)
            return
        work, scale = w32.monitor_at_cursor()
        max_h = int((work.bottom - work.top) / scale) - 40
        args = [
            edge, f"--app={url}", f"--user-data-dir={self.profile_dir}",
            f"--window-size={WIDTH},{min(HEIGHT, max(max_h, 480))}",
            "--no-first-run", "--no-default-browser-check", "--disable-sync",
            "--disable-extensions", "--disable-background-mode",
        ]
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        # Если прежний Edge этой панели ещё жив без окон, он сам откроет
        # новое окно у себя, а наш запуск тут же завершится
        proc = subprocess.Popen(args, close_fds=True)
        if not alive:
            self._proc = proc
        self._started = time.monotonic()
        log.info("Панель: открываю окно")

    @staticmethod
    def _focus(hwnd: int) -> None:
        if w32.user32.IsIconic(hwnd):
            w32.user32.ShowWindow(hwnd, SW_RESTORE)
        w32.user32.SetForegroundWindow(hwnd)

    def close(self) -> None:
        for hwnd in self.windows():
            w32.user32.PostMessageW(hwnd, w32.WM_CLOSE, 0, 0)
