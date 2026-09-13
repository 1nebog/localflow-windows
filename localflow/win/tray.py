"""Значок в трее: состояние, подсказка, меню и уведомления.

Меню строится заново при каждом открытии из `menu.build` — поэтому смена
языка интерфейса видна сразу, без перезапуска (на Mac меню rumps так не
умеет).
"""

import ctypes
import logging
import winreg

from .. import icons
from . import w32

log = logging.getLogger("localflow")

WM_TRAY = w32.WM_APP + 2
ICON_ID = 1

# Окна, на которые фокус «уходит» при клике по трею: их не считаем
# программой, куда человек печатал
_SHELL_CLASSES = {
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
    "TopLevelWindowForOverflowXamlIsland", "#32768", "Windows.UI.Core.CoreWindow",
    "Progman", "WorkerW",
}


def light_taskbar() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "SystemUsesLightTheme")[0])
    except OSError:
        return False


def hicon_from_rgba(rgba) -> int:
    import numpy as np
    h, w = rgba.shape[:2]
    bmp, bits = w32.dib(w, h)
    bgra = np.ascontiguousarray(rgba[..., [2, 1, 0, 3]])
    ctypes.memmove(bits, bgra.ctypes.data, bgra.nbytes)
    mask_bits = (ctypes.c_ubyte * (((w + 15) // 16) * 2 * h))()
    mask = w32.gdi32.CreateBitmap(w, h, 1, 1, ctypes.cast(mask_bits, ctypes.c_void_p))
    info = w32.ICONINFO(True, 0, 0, mask, bmp)
    hicon = w32.user32.CreateIconIndirect(ctypes.byref(info))
    w32.gdi32.DeleteObject(mask)
    w32.gdi32.DeleteObject(bmp)
    return hicon


class ForegroundTracker:
    """Последнее «настоящее» окно, где человек работал: чтобы после меню
    трея вернуть туда фокус и вставить текст из истории."""

    def __init__(self):
        self.hwnd = w32.user32.GetForegroundWindow()
        self._proc = w32.WINEVENTPROC(self._on_event)
        self._hook = w32.user32.SetWinEventHook(
            w32.EVENT_SYSTEM_FOREGROUND, w32.EVENT_SYSTEM_FOREGROUND, None, self._proc,
            0, 0, w32.WINEVENT_OUTOFCONTEXT | w32.WINEVENT_SKIPOWNPROCESS)

    @staticmethod
    def _is_shell(hwnd) -> bool:
        buf = ctypes.create_unicode_buffer(64)
        w32.user32.GetClassNameW(hwnd, buf, 64)
        return buf.value in _SHELL_CLASSES

    def _on_event(self, hook, event, hwnd, id_object, id_child, thread, time_ms):
        try:
            if hwnd and id_object == 0 and not self._is_shell(hwnd):
                self.hwnd = hwnd
        except Exception:
            pass

    def restore(self) -> None:
        if self.hwnd and w32.user32.IsWindow(self.hwnd):
            w32.user32.SetForegroundWindow(self.hwnd)

    def stop(self) -> None:
        if self._hook:
            w32.user32.UnhookWinEvent(self._hook)
            self._hook = None


class _OpensWindow:
    """Пункт меню, который сам открывает окно: фокус ему, а не прежнему окну."""

    def __init__(self, fn):
        self.fn = fn

    def __call__(self):
        self.fn()


class Tray:
    def __init__(self, ui, build_menu):
        self.ui = ui
        self._build_menu = build_menu
        self.state = "loading"
        self.tip = "LocalFlow"
        self._icons: dict = {}
        self._light = light_taskbar()
        self._added = False
        self._menu_open = False
        self._retries = 0
        self.foreground = ForegroundTracker()
        ui.on(WM_TRAY, self._on_tray)
        ui.on(w32.user32.RegisterWindowMessageW("TaskbarCreated"), self._on_taskbar_created)
        ui.on(w32.WM_SETTINGCHANGE, self._on_setting_change)

    # --- Значок ---

    def _icon(self, state: str) -> int:
        key = (state, self._light)
        if key not in self._icons:
            size = w32.user32.GetSystemMetrics(w32.SM_CXSMICON) or 16
            self._icons[key] = hicon_from_rgba(icons.tray_icon(state, size, self._light))
        return self._icons[key]

    def _data(self, flags: int) -> w32.NOTIFYICONDATAW:
        nid = w32.NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(w32.NOTIFYICONDATAW)
        nid.hWnd = self.ui.hwnd
        nid.uID = ICON_ID
        nid.uFlags = flags
        return nid

    def add(self, state: str | None = None, tip: str | None = None) -> bool:
        self.state = state or self.state
        self.tip = tip or self.tip
        nid = self._data(w32.NIF_MESSAGE | w32.NIF_ICON | w32.NIF_TIP | w32.NIF_SHOWTIP)
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._icon(self.state)
        nid.szTip = self.tip[:127]
        w32.shell32.Shell_NotifyIconW(w32.NIM_DELETE, ctypes.byref(self._data(0)))
        if not w32.shell32.Shell_NotifyIconW(w32.NIM_ADD, ctypes.byref(nid)):
            # Сразу после входа в Windows панель задач может быть ещё не готова
            self._added = False
            if self._retries < 30:
                self._retries += 1
                self.ui.after(2.0, self.add)
            else:
                log.error("Значок в трее не появился")
            return False
        nid.uVersion = w32.NOTIFYICON_VERSION_4
        w32.shell32.Shell_NotifyIconW(w32.NIM_SETVERSION, ctypes.byref(nid))
        self._added = True
        self._retries = 0
        return True

    def update(self, state: str, tip: str) -> None:
        if state == self.state and tip == self.tip:
            return
        self.state, self.tip = state, tip
        if not self._added:
            return
        nid = self._data(w32.NIF_ICON | w32.NIF_TIP | w32.NIF_SHOWTIP)
        nid.hIcon = self._icon(state)
        nid.szTip = tip[:127]
        w32.shell32.Shell_NotifyIconW(w32.NIM_MODIFY, ctypes.byref(nid))

    def notify(self, title: str, message: str, error: bool = False) -> None:
        if not self._added:
            log.info("%s: %s", title, message)
            return
        nid = self._data(w32.NIF_INFO)
        nid.szInfoTitle = title[:63]
        nid.szInfo = message[:255]
        nid.dwInfoFlags = (w32.NIIF_ERROR if error else w32.NIIF_INFO) | w32.NIIF_RESPECT_QUIET_TIME
        w32.shell32.Shell_NotifyIconW(w32.NIM_MODIFY, ctypes.byref(nid))

    def remove(self) -> None:
        if self._added:
            w32.shell32.Shell_NotifyIconW(w32.NIM_DELETE, ctypes.byref(self._data(0)))
            self._added = False
        self.foreground.stop()
        for hicon in self._icons.values():
            w32.user32.DestroyIcon(hicon)
        self._icons.clear()

    # --- События ---

    def _on_taskbar_created(self, wparam, lparam):
        log.info("Панель задач перезапустилась — возвращаю значок")
        self.add()
        return 0

    def _on_setting_change(self, wparam, lparam):
        light = light_taskbar()
        if light != self._light:
            self._light = light
            if self._added:
                nid = self._data(w32.NIF_ICON)
                nid.hIcon = self._icon(self.state)
                w32.shell32.Shell_NotifyIconW(w32.NIM_MODIFY, ctypes.byref(nid))
        return None

    def _on_tray(self, wparam, lparam):
        event = lparam & 0xFFFF
        if event in (w32.WM_CONTEXTMENU, w32.NIN_SELECT, w32.NIN_KEYSELECT):
            x = ctypes.c_short(wparam & 0xFFFF).value
            y = ctypes.c_short((wparam >> 16) & 0xFFFF).value
            self.show_menu(x, y)
        return 0

    def show_menu(self, x: int, y: int) -> None:
        if self._menu_open:
            return
        self._menu_open = True
        actions: dict[int, object] = {}
        root = None
        cmd = 0
        try:
            root = self._make_menu(self._build_menu(), actions)
            # Без этого меню не закрывается кликом мимо — известная особенность
            w32.user32.SetForegroundWindow(self.ui.hwnd)
            cmd = w32.user32.TrackPopupMenuEx(
                root, w32.TPM_RETURNCMD | w32.TPM_RIGHTBUTTON | w32.TPM_NONOTIFY
                | w32.TPM_BOTTOMALIGN, x, y, self.ui.hwnd, None)
            w32.user32.PostMessageW(self.ui.hwnd, w32.WM_NULL, 0, 0)
        finally:
            if root:
                w32.user32.DestroyMenu(root)
            self._menu_open = False
        action = actions.get(cmd)
        if action is not None:
            if not isinstance(action, _OpensWindow):
                # человек выбрал пункт — фокус обратно туда, где он работал
                self.foreground.restore()
            try:
                action()
            except Exception:
                log.exception("Пункт меню упал")

    @staticmethod
    def _make_menu(items, actions: dict) -> int:
        hmenu = w32.user32.CreatePopupMenu()
        for item in items:
            if item is None:
                w32.user32.AppendMenuW(hmenu, w32.MF_SEPARATOR, 0, None)
                continue
            label = item.label.replace("&", "&&").replace("\t", " ").replace("\n", " ")
            flags = w32.MF_STRING
            if item.checked:
                flags |= w32.MF_CHECKED
            if not item.enabled:
                flags |= w32.MF_GRAYED
            if item.children is not None:
                sub = Tray._make_menu(item.children, actions)
                w32.user32.AppendMenuW(hmenu, flags | w32.MF_POPUP, sub, label)
            else:
                cmd = max(actions, default=0) + 1
                actions[cmd] = (item.action if item.focus_back or item.action is None
                                else _OpensWindow(item.action))
                w32.user32.AppendMenuW(hmenu, flags, cmd, label)
        return hmenu
