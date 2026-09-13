"""Поток окон: очередь сообщений Windows, таймеры, работа из других потоков.

Окна Windows принадлежат потоку, который их создал, и трогать их можно
только оттуда. Диктовка же живёт в своих потоках — поэтому всё, что она
хочет показать, приходит сюда через post(fn).

Пока ничего не происходит, поток спит в GetMessage и процессор не тратит.
"""

import ctypes
import logging
from collections import deque
from ctypes import wintypes

from . import w32

log = logging.getLogger("localflow")

WM_CALL = w32.WM_APP + 1


class UiLoop:
    def __init__(self):
        self._calls: deque = deque()
        self._handlers: dict[int, object] = {}
        self._timers: dict[int, tuple] = {}
        self._next_timer = 1
        self.hwnd = w32.create_window("LocalFlowMain", self._on_message)

    # --- Из любого потока ---

    def post(self, fn) -> None:
        self._calls.append(fn)
        w32.user32.PostMessageW(self.hwnd, WM_CALL, 0, 0)

    def quit(self) -> None:
        self.post(lambda: w32.user32.PostQuitMessage(0))

    # --- Только из потока окон ---

    def on(self, msg: int, fn) -> None:
        """fn(wparam, lparam) -> результат или None (обработка по умолчанию)."""
        self._handlers[msg] = fn

    def every(self, sec: float, fn) -> int:
        return self._set_timer(sec, fn, once=False)

    def after(self, sec: float, fn) -> int:
        return self._set_timer(sec, fn, once=True)

    def cancel(self, timer_id: int | None) -> None:
        if timer_id and self._timers.pop(timer_id, None) is not None:
            w32.user32.KillTimer(self.hwnd, timer_id)

    def _set_timer(self, sec, fn, once) -> int:
        tid = self._next_timer
        self._next_timer += 1
        self._timers[tid] = (fn, once)
        w32.user32.SetTimer(self.hwnd, tid, max(10, int(sec * 1000)), None)
        return tid

    def run(self) -> None:
        msg = wintypes.MSG()
        while w32.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            w32.user32.TranslateMessage(ctypes.byref(msg))
            w32.user32.DispatchMessageW(ctypes.byref(msg))

    def pump(self, sec: float = 0.0) -> None:
        """Обработать накопившееся (для проверок, без бесконечного цикла)."""
        import time
        end = time.monotonic() + sec
        msg = wintypes.MSG()
        while True:
            while w32.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                w32.user32.TranslateMessage(ctypes.byref(msg))
                w32.user32.DispatchMessageW(ctypes.byref(msg))
            if time.monotonic() >= end:
                return
            time.sleep(0.01)

    def destroy(self) -> None:
        for tid in list(self._timers):
            self.cancel(tid)
        w32.destroy_window(self.hwnd)

    def _on_message(self, msg, wparam, lparam):
        if msg == WM_CALL:
            while self._calls:
                fn = self._calls.popleft()
                try:
                    fn()
                except Exception:
                    log.exception("Задача в потоке окон упала")
            return 0
        if msg == w32.WM_TIMER and wparam in self._timers:
            fn, once = self._timers[wparam]
            if once:
                self.cancel(wparam)
            try:
                fn()
            except Exception:
                log.exception("Таймер в потоке окон упал")
            return 0
        handler = self._handlers.get(msg)
        if handler is not None:
            return handler(wparam, lparam)
        return None
