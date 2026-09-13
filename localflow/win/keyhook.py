"""Перехват клавиатуры во всей системе (низкоуровневый хук Windows).

Хук живёт в своём потоке с очередью сообщений. На каждое нажатие Windows
ждёт ответа «пропустить / съесть», и если обработчик задумается дольше
системного лимита (около секунды), Windows молча снимает хук — клавиша
диктовки перестала бы работать. Поэтому в самом обработчике только решение
от KeyboardLogic, а события уходят в очередь и обрабатываются другим
потоком.
"""

import ctypes
import logging
import queue
import threading
from ctypes import wintypes

from ..keys import VK_LCONTROL, KeyboardLogic

log = logging.getLogger("localflow")

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x10
# Правый Alt на раскладках с AltGr присылает перед собой поддельный левый
# Ctrl с этим скан-кодом. Его не считаем: иначе RAlt как клавиша диктовки
# не срабатывал бы никогда, а LCtrl «зажимался» бы сам
ALTGR_FAKE_SCAN = 0x21D

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_user32.CallNextHookEx.restype = LRESULT
_user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
_user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
_user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
_user32.GetAsyncKeyState.restype = ctypes.c_short
_kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def is_key_down(vk: int) -> bool:
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


class KeyHook:
    """on_event(name) вызывается в отдельном потоке, по порядку нажатий."""

    def __init__(self, logic: KeyboardLogic, on_event, accept_injected: bool = False):
        self.logic = logic
        self._on_event = on_event
        # Нажатия, сымитированные программами (в том числе нашей вставкой
        # Ctrl+V), не считаем — только живую клавиатуру. Для проверок можно
        # разрешить
        self.accept_injected = accept_injected
        self._events: queue.Queue = queue.Queue()
        self._thread_id = 0
        self._hook = None
        self._proc = HOOKPROC(self._callback)   # держим ссылку, иначе сборщик
        self._ready = threading.Event()
        self.error: str | None = None

    def start(self) -> bool:
        threading.Thread(target=self._dispatch, daemon=True, name="key-events").start()
        threading.Thread(target=self._run, daemon=True, name="key-hook").start()
        self._ready.wait(5)
        return self._hook is not None

    def stop(self) -> None:
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._events.put(None)

    def _run(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        self._hook = _user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, _kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            self.error = f"SetWindowsHookEx: {ctypes.get_last_error()}"
            log.error("Перехват клавиатуры не включился: %s", self.error)
            self._ready.set()
            return
        log.info("Перехват клавиатуры включён")
        self._ready.set()
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        _user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        log.info("Перехват клавиатуры выключен")

    def _callback(self, code, wparam, lparam):
        try:
            if code == 0 and wparam in (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP):
                kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                injected = bool(kb.flags & LLKHF_INJECTED)
                fake_altgr = kb.vkCode == VK_LCONTROL and kb.scanCode == ALTGR_FAKE_SCAN
                if (self.accept_injected or not injected) and not fake_altgr:
                    down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                    swallow, events = self.logic.feed(kb.vkCode, down)
                    for ev in events:
                        self._events.put(ev)
                    if swallow:
                        return 1
        except Exception:
            # Ошибка в обработчике не должна ломать клавиатуру всей системе
            pass
        return _user32.CallNextHookEx(None, code, wparam, lparam)

    def _dispatch(self) -> None:
        while True:
            ev = self._events.get()
            if ev is None:
                return
            try:
                self._on_event(ev)
            except Exception as exc:
                log.error("Обработка клавиши %s упала: %s", ev, exc)
