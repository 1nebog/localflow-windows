"""Вставка текста в активное окно: через буфер обмена или печатью.

Перенос `paste_text` с Mac. Что по-другому:
- печать идёт символами Юникода, а не клавишами, поэтому раскладка
  (русская, английская, немецкая) ни на что не влияет — «ё» остаётся «ё»;
- наш текст не оседает в журнале буфера обмена Windows (Win+V) и не уходит
  в облачный буфер: диктовка — личное;
- старое содержимое буфера возвращается целиком, включая картинки.

Windows не даёт обычной программе нажимать клавиши в окнах, запущенных
от администратора. Тогда вставка не пройдёт — об этом вернётся False.
"""

import ctypes
import logging
import threading
import time
from ctypes import wintypes

from ..core import CLIPBOARD_RESTORE_DELAY

log = logging.getLogger("localflow")

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL, VK_SHIFT, VK_RETURN = 0x11, 0x10, 0x0D
VK_V, VK_Z = 0x56, 0x5A

# Форматы, которые не лежат в памяти как байты (картинки GDI и т.п.) —
# их не сохраняем; растровая картинка всё равно есть ещё и в CF_DIB
_HANDLE_FORMATS = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}
_SNAPSHOT_LIMIT = 64 * 1024 * 1024


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_user32.SendInput.restype = wintypes.UINT
_user32.OpenClipboard.argtypes = (wintypes.HWND,)
_user32.GetClipboardData.argtypes = (wintypes.UINT,)
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.EnumClipboardFormats.argtypes = (wintypes.UINT,)
_user32.EnumClipboardFormats.restype = wintypes.UINT
_user32.RegisterClipboardFormatW.argtypes = (wintypes.LPCWSTR,)
_user32.RegisterClipboardFormatW.restype = wintypes.UINT
_user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
_kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
_kernel32.GlobalSize.argtypes = (wintypes.HGLOBAL,)
_kernel32.GlobalSize.restype = ctypes.c_size_t
_kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)

_clip_lock = threading.Lock()
_restore_timer: threading.Timer | None = None


# --- Буфер обмена -------------------------------------------------------------

class _Clipboard:
    """with _Clipboard(): ... — буфер может быть занят другой программой
    на долю секунды, поэтому открываем с повторами."""

    def __enter__(self):
        for _ in range(50):
            if _user32.OpenClipboard(None):
                return self
            time.sleep(0.01)
        raise OSError("буфер обмена занят другой программой")

    def __exit__(self, *exc):
        _user32.CloseClipboard()


def _alloc(data: bytes):
    h = _kernel32.GlobalAlloc(GMEM_MOVEABLE, max(1, len(data)))
    if not h:
        raise MemoryError("GlobalAlloc")
    ptr = _kernel32.GlobalLock(h)
    ctypes.memmove(ptr, data, len(data))
    _kernel32.GlobalUnlock(h)
    return h


def _set(fmt: int, data: bytes) -> None:
    h = _alloc(data)
    if not _user32.SetClipboardData(fmt, h):
        _kernel32.GlobalFree(h)   # не принят — память наша


def _read(fmt: int) -> bytes | None:
    h = _user32.GetClipboardData(fmt)
    if not h:
        return None
    size = _kernel32.GlobalSize(h)
    ptr = _kernel32.GlobalLock(h)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, size)
    finally:
        _kernel32.GlobalUnlock(h)


def get_text() -> str | None:
    with _clip_lock, _Clipboard():
        data = _read(CF_UNICODETEXT)
    if data is None:
        return None
    return data.decode("utf-16-le", errors="replace").split("\x00", 1)[0]


def _private_markers() -> None:
    """Пометки «не сохранять в журнал буфера и не отправлять в облако»."""
    zero = (0).to_bytes(4, "little")
    for name, value in (("ExcludeClipboardContentFromMonitorProcessing", b"\x00"),
                        ("CanIncludeInClipboardHistory", zero),
                        ("CanUploadToCloudClipboard", zero)):
        fmt = _user32.RegisterClipboardFormatW(name)
        if fmt:
            _set(fmt, value)


def set_text(text: str, private: bool = True) -> None:
    with _clip_lock, _Clipboard():
        _user32.EmptyClipboard()
        _set(CF_UNICODETEXT, (text + "\x00").encode("utf-16-le"))
        if private:
            _private_markers()


def snapshot() -> list[tuple[int, bytes]]:
    """Всё, что сейчас в буфере и что можно положить обратно."""
    out, total = [], 0
    with _clip_lock, _Clipboard():
        fmt = 0
        while True:
            fmt = _user32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt in _HANDLE_FORMATS:
                continue
            data = _read(fmt)
            if data is None:
                continue
            total += len(data)
            if total > _SNAPSHOT_LIMIT:
                log.info("Буфер обмена слишком большой — после вставки не верну")
                return []
            out.append((fmt, data))
    return out


def restore(items: list[tuple[int, bytes]]) -> None:
    if not items:
        return
    with _clip_lock, _Clipboard():
        _user32.EmptyClipboard()
        for fmt, data in items:
            _set(fmt, data)
        # это содержимое уже побывало в журнале буфера — не дублируем
        _private_markers()


# --- Нажатия ------------------------------------------------------------------

_user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
_user32.MapVirtualKeyW.restype = wintypes.UINT


def _key(vk=0, scan=0, flags=0) -> INPUT:
    if vk and not scan:
        # скан-код рядом с кодом клавиши: часть программ смотрит только на него
        scan = _user32.MapVirtualKeyW(vk, 0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def _send(events: list[INPUT]) -> bool:
    if not events:
        return True
    arr = (INPUT * len(events))(*events)
    sent = _user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
    return sent == len(events)


def press_combo(*vks: int) -> bool:
    """Сочетание: зажать по порядку, отпустить в обратном."""
    events = [_key(vk) for vk in vks] + [_key(vk, flags=KEYEVENTF_KEYUP) for vk in reversed(vks)]
    return _send(events)


def type_text(text: str) -> bool:
    """Печать символами Юникода. Перенос строки — Shift+Enter: обычный Enter
    в мессенджерах отправляет сообщение."""
    ok = True
    for i, line in enumerate(text.split("\n")):
        if i:
            ok &= press_combo(VK_SHIFT, VK_RETURN)
        units = line.encode("utf-16-le")
        codes = [int.from_bytes(units[j:j + 2], "little") for j in range(0, len(units), 2)]
        # Порциями: часть программ теряет символы, если прислать всё разом
        for start in range(0, len(codes), 32):
            batch = []
            for code in codes[start:start + 32]:
                batch.append(_key(scan=code, flags=KEYEVENTF_UNICODE))
                batch.append(_key(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
            ok &= _send(batch)
            time.sleep(0.004)
    return ok


def undo() -> bool:
    return press_combo(VK_CONTROL, VK_Z)


def paste_text(text: str, method: str = "clipboard") -> bool:
    """Вставить текст в активное окно. False — Windows не пропустила нажатия
    (окно администратора): текст тогда остаётся в буфере обмена."""
    global _restore_timer
    if method == "type":
        return type_text(text)
    try:
        # Прошлая вставка ещё не вернула буфер — не сохраняем свой же текст
        # как «старое содержимое»
        if _restore_timer is not None and _restore_timer.is_alive():
            _restore_timer.cancel()
            old = _restore_timer.args[0]
        else:
            old = snapshot()
        before = _user32.GetClipboardSequenceNumber()
        set_text(text)
        if _user32.GetClipboardSequenceNumber() == before:
            raise OSError("буфер не принял текст")
    except OSError as exc:
        log.warning("Буфер обмена недоступен (%s) — печатаю посимвольно", exc)
        return type_text(text)
    ok = press_combo(VK_CONTROL, VK_V)
    if not ok:
        # Нажатие не прошло: оставляем текст в буфере, чтобы человек
        # вставил его сам
        return False
    _restore_timer = threading.Timer(CLIPBOARD_RESTORE_DELAY, _restore_quietly, args=(old,))
    _restore_timer.daemon = True
    _restore_timer.start()
    return True


def _restore_quietly(items) -> None:
    try:
        restore(items)
    except OSError as exc:
        log.warning("Старый буфер обмена не вернулся: %s", exc)


class Paster:
    """Вставка для диктовки: то же самое одним объектом."""

    @staticmethod
    def paste_text(text: str, method: str = "clipboard") -> bool:
        return paste_text(text, method)

    @staticmethod
    def undo() -> bool:
        return undo()

    @staticmethod
    def set_clipboard(text: str) -> None:
        set_text(text)
